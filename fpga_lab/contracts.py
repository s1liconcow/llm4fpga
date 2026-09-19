"""Explicit finite hardware contracts and independently supplied golden vectors."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path


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

    @classmethod
    def load(cls, path: Path) -> 'TranslationSpec':
        return cls(**json.loads(path.read_text()))

    def public_contract(self) -> dict:
        return {k: v for k, v in asdict(self).items()
                if k not in {'source', 'vectors', 'audit_vectors'}}


@dataclass(frozen=True)
class Vector:
    input: int
    output: int
    reference: float | None = None
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
        if ref is not None and not math.isfinite(ref):
            raise ValueError('Nonfinite golden reference')
        chain = row.get('chain', False)
        if type(chain) is not bool or (chain and (not spec.feedback_bits or not result)):
            raise ValueError('Invalid vector feedback chain')
        if (spec.max_abs_error or spec.max_rms_error) and ref is None:
            raise ValueError('Approximate contracts require floating reference values')
        result.append(Vector(bits('input', spec.input_bits), bits('output', spec.output_bits),
                             ref, str(row.get('label', '')), chain))
    return result
