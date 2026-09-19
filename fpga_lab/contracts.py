"""Explicit finite hardware contracts and independently supplied golden vectors."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputField:
    """One independently scored lane of a packed result (offset measured from LSB)."""
    name: str
    bits: int
    lsb: int
    signed: bool = True
    scale: float = 1.0
    max_abs_error: float = 0.0
    max_rms_error: float = 0.0

    def __post_init__(self):
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', self.name):
            raise ValueError('Invalid output field name')
        if type(self.bits) is not int or not 1 <= self.bits <= 4096:
            raise ValueError('Invalid output field width')
        if type(self.lsb) is not int or self.lsb < 0 or type(self.signed) is not bool:
            raise ValueError('Invalid output field offset or signedness')
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError('Invalid output field scale')
        for value in (self.max_abs_error, self.max_rms_error):
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid output field error limit')

    def decode(self, packed: int) -> float:
        value = (packed >> self.lsb) & ((1 << self.bits)-1)
        if self.signed and value >> (self.bits-1):
            value -= 1 << self.bits
        return value / self.scale


@dataclass(frozen=True)
class TranslationSpec:
    name: str
    input_bits: int
    output_bits: int
    max_latency: int
    initiation_interval: int
    description: str
    source: str
    vectors: str
    audit_vectors: str
    output_signed: bool = False
    output_scale: float = 1.0
    max_abs_error: float = 0.0
    max_rms_error: float = 0.0
    clock_mhz: float = 100.0
    # For block algorithms, optional output -> high input bits chaining in the TB.
    feedback_bits: int = 0
    output_fields: tuple[OutputField, ...] = ()
    max_luts: int | None = None
    max_ffs: int | None = None
    max_dsps: int | None = None
    max_brams: int | None = None

    def __post_init__(self):
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', self.name):
            raise ValueError('name must be a hardware identifier')
        for field in ('input_bits', 'output_bits', 'max_latency', 'initiation_interval'):
            value = getattr(self, field)
            if type(value) is not int or not 1 <= value <= 65536:
                raise ValueError(f'{field} must be an integer in 1..65536')
        if self.input_bits > 4096 or self.output_bits > 4096:
            raise ValueError('Bus widths above 4096 are unsupported')
        if self.feedback_bits not in (0, self.output_bits) or self.feedback_bits >= self.input_bits:
            raise ValueError('feedback_bits must be zero or output_bits, below input_bits')
        for key in ('output_scale', 'clock_mhz'):
            v = getattr(self, key)
            if not math.isfinite(v) or v <= 0:
                raise ValueError(f'{key} must be finite and positive')
        for key in ('max_abs_error', 'max_rms_error'):
            v = getattr(self, key)
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{key} must be finite and nonnegative')
        fields = tuple(OutputField(**f) if isinstance(f, dict) else f for f in self.output_fields)
        object.__setattr__(self, 'output_fields', fields)
        if fields:
            occupied = set()
            names = set()
            for field in fields:
                bits = set(range(field.lsb, field.lsb+field.bits))
                if field.name in names or occupied & bits or max(bits) >= self.output_bits:
                    raise ValueError('Output fields overlap, duplicate names, or exceed the bus')
                occupied |= bits
                names.add(field.name)
            if occupied != set(range(self.output_bits)):
                raise ValueError('Output fields must cover every output bit')
            if self.max_abs_error or self.max_rms_error:
                raise ValueError('Use per-field error bounds for packed numeric outputs')
        for key in ('max_luts', 'max_ffs', 'max_dsps', 'max_brams'):
            value = getattr(self, key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'{key} must be a nonnegative integer or null')

    @classmethod
    def load(cls, path: Path) -> 'TranslationSpec':
        return cls(**json.loads(path.read_text()))

    def public_contract(self) -> dict:
        result = {k: v for k, v in asdict(self).items()
                  if k not in {'source', 'vectors', 'audit_vectors'}}
        if self.output_fields:
            result['output_fields'] = [asdict(f) for f in self.output_fields]
        else:
            result.pop('output_fields')
        for key in ('max_luts', 'max_ffs', 'max_dsps', 'max_brams'):
            if result[key] is None:
                result.pop(key)
        return result


@dataclass(frozen=True)
class Vector:
    input: int
    output: int
    reference: float | tuple[float, ...] | None = None
    label: str = ''
    chain: bool = False

    def as_dict(self) -> dict:
        result = {'input': hex(self.input), 'output': hex(self.output), 'label': self.label}
        if self.reference is not None:
            result['reference'] = self.reference
        if self.chain:
            result['chain'] = True
        return result


def load_vectors(path: Path, spec: TranslationSpec) -> list[Vector]:
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError('Golden vectors must be a nonempty JSON array')
    result = []
    for row in rows:
        def bits(key, width):
            v = row[key]
            n = int(v, 0) if isinstance(v, str) else v
            if type(n) is not int or not 0 <= n < 1 << width:
                raise ValueError(f'Invalid {key} bit pattern')
            return n
        ref = row.get('reference')
        if spec.output_fields:
            if not isinstance(ref, list) or len(ref) != len(spec.output_fields):
                raise ValueError('Packed numeric outputs require one reference per field')
            if not all(type(v) in (int, float) and math.isfinite(v) for v in ref):
                raise ValueError('Nonfinite or invalid field reference')
            ref = tuple(ref)
        elif ref is not None and (type(ref) not in (int, float) or not math.isfinite(ref)):
            raise ValueError('Nonfinite or invalid golden reference')
        chain = row.get('chain', False)
        if type(chain) is not bool or (chain and (not spec.feedback_bits or not result)):
            raise ValueError('Invalid vector feedback chain')
        if (spec.max_abs_error or spec.max_rms_error) and ref is None:
            raise ValueError('Approximate contracts require floating reference values')
        result.append(Vector(bits('input', spec.input_bits), bits('output', spec.output_bits),
                             ref, str(row.get('label', '')), chain))
    return result
