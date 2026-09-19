"""Golden oracle, fixed interface contract and disjoint development/audit sets."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "normalizer"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def analytic(adc: np.ndarray) -> np.ndarray:
    x = np.asarray(adc, dtype=np.float64) / 2048.0
    return x / np.sqrt(0.25 + x * x)


@dataclass(frozen=True)
class Contract:
    max_abs_error: float = 2e-4
    max_rms_error: float = 6e-5
    max_latency: int = 16
    target_mhz: float = 25.0
    max_luts: int = 6000
    max_ffs: int = 6000
    max_bram: int = 32

    def __post_init__(self) -> None:
        if any(not math.isfinite(v) or v <= 0 for v in
               [self.max_abs_error, self.max_rms_error, self.target_mhz]):
            raise ValueError("Numerical and clock limits must be finite and positive")
        if not 1 <= self.max_latency <= 64:
            raise ValueError("Invalid latency bound")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Golden:
    values: np.ndarray
    provenance: dict

    @classmethod
    def load(cls, path: Path | None = None) -> "Golden":
        if path is None:
            codes = np.arange(-2048, 2048)
            return cls(analytic(codes), {"engine": "python-double-mirror",
                       "matlab_executed": False,
                       "source_sha256": digest((EXAMPLE / "normalize_sensor.m").read_bytes())})
        with path.open(newline="") as f:
            rows = list(csv.DictReader(f))
        codes = np.array([int(r["adc"]) for r in rows])
        values = np.array([float(r["y"]) for r in rows])
        if not np.array_equal(codes, np.arange(-2048, 2048)) or not np.isfinite(values).all():
            raise ValueError("Oracle must contain every ADC code in order and finite outputs")
        metadata = path.with_suffix(".meta.json")
        provenance = json.loads(metadata.read_text()) if metadata.exists() else {"engine": "external-csv"}
        provenance["csv_sha256"] = digest(path.read_bytes())
        return cls(values, provenance)

    def at(self, codes: np.ndarray) -> np.ndarray:
        codes = np.asarray(codes)
        if not np.issubdtype(codes.dtype, np.integer) or np.any((codes < -2048) | (codes > 2047)):
            raise ValueError("ADC codes must be signed 12-bit integers")
        return self.values[codes + 2048]

    def require_normalizer(self) -> None:
        if not np.allclose(self.values, analytic(np.arange(-2048, 2048)), atol=1e-12, rtol=0):
            raise ValueError("Structured templates support normalize_sensor.m only; oracle disagrees")


def partitions(seed: int = 20260919) -> tuple[np.ndarray, np.ndarray]:
    """Dev includes corners; audit is never sent back to search workers."""
    corners = np.array([-2048, -2047, -1, 0, 1, 2046, 2047])
    remaining = np.setdiff1d(np.arange(-2048, 2048), corners)
    np.random.default_rng(seed).shuffle(remaining)
    return np.sort(np.r_[corners, remaining[:2048 - len(corners)]]), np.sort(remaining[2048-len(corners):])


def metrics(codes: np.ndarray, outputs: np.ndarray, golden: Golden, contract: Contract) -> dict:
    codes, outputs = np.asarray(codes), np.asarray(outputs)
    if codes.size == 0 or codes.shape != outputs.shape:
        raise ValueError("Empty or mismatched output vectors")
    if not np.issubdtype(outputs.dtype, np.integer) or np.any((outputs < -32768) | (outputs > 32767)):
        raise ValueError("Output must contain signed 16-bit integer codes")
    reference = golden.at(codes)
    actual = outputs.astype(np.float64) / 16384.0
    error = np.abs(actual - reference)
    worst = np.argsort(error)[-8:][::-1]
    maximum, rms = float(error.max()), float(np.sqrt(np.mean(error * error)))
    return {"samples": int(codes.size), "max_abs_error": maximum, "rms_error": rms,
            "violations": int(np.count_nonzero(error > contract.max_abs_error)),
            "pass": bool(maximum <= contract.max_abs_error and rms <= contract.max_rms_error),
            "worst_cases": [{"adc": int(codes[i]), "reference": float(reference[i]),
                             "actual": float(actual[i]), "abs_error": float(error[i])} for i in worst]}
