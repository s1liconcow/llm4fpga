"""Packing and independent matrix oracle for the SLAM hardware reduction."""
from __future__ import annotations

import importlib.util
import math
import shutil
from dataclasses import asdict
from functools import cache
from pathlib import Path

import numpy as np

from .contracts import OutputField, TranslationSpec, Vector
from .problem import ROOT, write_json

BATCH = 8
INPUT_BITS = 1156
OUTPUT_BITS = 512
COORD_SCALE = 1 << 24
ROTATION_SCALE = 1 << 30
RESULT_SCALE = 1 << 32
FIELD_NAMES = ('h02', 'h12', 'h22', 'g0', 'g1', 'g2', 'cost', 'count')


@cache
def specification() -> TranslationSpec:
    fields = tuple(OutputField(name, 64, 64*i, scale=RESULT_SCALE,
                               max_abs_error=0 if name=='count' else 0.00005,
                               max_rms_error=0 if name=='count' else 0.00001)
                   for i, name in enumerate(FIELD_NAMES))
    return TranslationSpec('slam', INPUT_BITS, OUTPUT_BITS, 64, 64,
        'Compute the supplied Python 2D ICP normal_equations function for 0..8 point pairs. '
        'Input is packed little-endian by fields: each point pair i occupies bits 128*i..128*i+127, '
        'with signed 32-bit px,py,qx,qy in that order, lowest field first, all scaled by 2^24. '
        'Bits 1024..1055 are signed tx / 2^24; 1056..1087 signed ty / 2^24; '
        '1088..1119 signed cosine / 2^30; 1120..1151 signed sine / 2^30; '
        '1152..1155 unsigned active count, legal range 0..8. Ignore inactive pairs, whose bits are arbitrary. '
        'All coordinates and translations are bounded to [-16,16] metres; cosine/sine are in [-1,1]. '
        'Every output is signed 64-bit scaled by 2^32. From LSB to MSB: '
        'h02,h12,h22,g0,g1,g2,cost,count. The output count is also scaled by 2^32, NOT an unscaled integer. '
        'Zero active points must produce a completely zero packet. '
        'Use a sequential datapath to reuse multipliers across the eight pairs; <=64 cycles latency and '
        'one input packet per 64 cycles. Widen before products, sums, shifts and subtraction. '
        'Intermediate fixed-point rounding is allowed within the per-field error bounds. '
        'Keep at least 24 fractional bits in rotated coordinates and residuals to meet the tight accuracy bounds. '
        'The entire rotation, residual/Jacobian calculation, matrix reduction and cost must be in VHDL.',
        'normal_equations.py', 'dev.json', 'audit.json', output_fields=fields,
        max_luts=8000, max_ffs=6000, max_dsps=40, max_brams=8)


def quantize(value: float, scale: int, bound: float) -> int:
    if not math.isfinite(value) or abs(value) > bound:
        raise ValueError(f'Value outside the hardware contract: {value}, bound={bound}')
    return int(round(value*scale)) & 0xffffffff


def signed32(value: int) -> int:
    value &= 0xffffffff
    return value-(1 << 32) if value & (1 << 31) else value


def pack(points, targets, pose, *, inactive: int = 0) -> int:
    points, targets = np.asarray(points), np.asarray(targets)
    if points.shape != targets.shape or points.ndim != 2 or points.shape[1] != 2 or len(points) > BATCH:
        raise ValueError('Expected 0..8 pairs of 2D points')
    # Inactive data deliberately need not be zero during audits.
    word = inactive & ((1 << 1024)-1)
    for i, (point, target) in enumerate(zip(points, targets)):
        word &= ~(((1 << 128)-1) << (128*i))
        for j, value in enumerate((*point, *target)):
            word |= quantize(float(value), COORD_SCALE, 16) << (128*i+32*j)
    tx, ty, theta = map(float, pose)
    for offset, value, scale, bound in [(1024, tx, COORD_SCALE, 16), (1056, ty, COORD_SCALE, 16),
                                      (1088, math.cos(theta), ROTATION_SCALE, 1),
                                      (1120, math.sin(theta), ROTATION_SCALE, 1)]:
        word |= quantize(value, scale, bound) << offset
    return word | (len(points) << 1152)


def unpack(word: int):
    count = (word >> 1152) & 15
    if count > BATCH:
        raise ValueError('Invalid active point count')
    pairs = np.array([[signed32(word >> (128*i+32*j))/COORD_SCALE for j in range(4)]
                      for i in range(count)], dtype=np.float64).reshape(count, 4)
    tx, ty = (signed32(word >> bit)/COORD_SCALE for bit in (1024, 1056))
    c, s = (signed32(word >> bit)/ROTATION_SCALE for bit in (1088, 1120))
    return pairs[:, :2], pairs[:, 2:], tx, ty, c, s


def oracle(word: int) -> np.ndarray:
    """Independent vectorized J.T@J/J.T@r oracle, not the source's scalar reduction."""
    p, q, tx, ty, c, s = unpack(word)
    u = p @ np.array([[c, s], [-s, c]])
    residual = u + [tx, ty] - q
    jacobian = np.zeros((len(p), 2, 3))
    jacobian[:, 0, 0] = 1
    jacobian[:, 1, 1] = 1
    jacobian[:, 0, 2] = -u[:, 1]
    jacobian[:, 1, 2] = u[:, 0]
    j = jacobian.reshape(-1, 3)
    r = residual.ravel()
    h, g = j.T @ j, j.T @ r
    return np.array([h[0, 2], h[1, 2], h[2, 2], *g, r @ r, len(p)])


def pack_result(values) -> int:
    return sum((int(round(float(value)*RESULT_SCALE)) & ((1 << 64)-1)) << (64*i)
               for i, value in enumerate(values))


def decode_result(word: int) -> np.ndarray:
    return np.array([field.decode(word) for field in specification().output_fields])


def matrices(values):
    h02, h12, h22, g0, g1, g2, cost, count = values
    return np.array([[count, 0, h02], [0, count, h12], [h02, h12, h22]]), np.array([g0, g1, g2]), cost


def source_function():
    module_spec = importlib.util.spec_from_file_location('slam_normal_equations', ROOT/'examples/slam/normal_equations.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module.normal_equations


def prepare_slam(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/'examples/slam/normal_equations.py', out/'normal_equations.py')
    scalar = source_function()
    seen = set()
    for name, seed, size in [('dev', 1719, 160), ('audit', 82041, 800)]:
        rng = np.random.default_rng(seed)
        vectors = []
        for i in range(size):
            count = i % 9
            p = rng.uniform(-16,16,(count,2))
            q = rng.uniform(-16,16,(count,2))
            pose = [*rng.uniform(-16,16,2), rng.uniform(-math.pi,math.pi)]
            case = i % 7
            if case == 0:
                p[:] = 0  # zero/rank-deficient geometry
            elif case == 1:
                p[:] = rng.choice([-16, 16], p.shape)
                q[:] = rng.choice([-16, 16], q.shape)
                pose[2] = rng.choice([0, math.pi/2, -math.pi/2, math.pi])
            elif case == 2:
                p[:,1] = 0  # collinear data
            elif case == 3:
                p *= 1e-5  # tiny coordinates and cancellation
            elif case == 4:
                # Aligned local data like near-converged ICP.
                pose = [*rng.uniform(-0.1,0.1,2),rng.uniform(-0.05,0.05)]
                p *= 0.5
                c,s = math.cos(pose[2]),math.sin(pose[2])
                q = p @ np.array([[c,s],[-s,c]]) + pose[:2] + rng.normal(0,0.01,p.shape)
            inactive = int.from_bytes(rng.bytes(128),'little')
            word = pack(p,q,pose,inactive=inactive)
            if word in seen:
                raise RuntimeError('Development/audit overlap')
            seen.add(word)
            expected = oracle(word)
            if not np.allclose(scalar(*unpack(word)),expected,rtol=1e-12,atol=1e-9):
                raise RuntimeError('Independent matrix oracle disagrees with the Python specification')
            vectors.append(Vector(word,pack_result(expected),tuple(expected),f'{name}-{i}/n={count}/case={case}'))
        write_json(out/f'{name}.json',[v.as_dict() for v in vectors])
    write_json(out/'spec.json',asdict(specification()))
    return out/'spec.json'
