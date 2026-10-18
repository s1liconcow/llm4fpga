"""Independent fixed-point interpretation of the M'Closkey gyro block diagram.

The paper supplies topology, not coefficients or binary-point locations. All
choices here are an explicit implementation contract; demo filters are not a
calibration for a physical sensor. No HDL/microcode is used by this oracle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

FRAC = 16
ONE = 1 << FRAC
TAPS = 128


def sat(value: int, bits: int = 22) -> int:
    return min((1 << (bits - 1)) - 1, max(-(1 << (bits - 1)), value))


def mul(a: int, b: int) -> int:
    return sat((a * b) >> FRAC)


def quantize(value: float, bits: int = 18) -> int:
    return sat(round(value * ONE), bits)


@dataclass
class Config:
    coefficients: list[list[int]] = field(default_factory=lambda: [[0] for _ in range(7)])
    gains: list[int] = field(default_factory=lambda: [0] * 8)
    kp: int = 0
    ki: int = 0
    reference: int = 0
    i_min: int = -(1 << 23)
    i_max: int = (1 << 23) - 1
    u_min: int = -(1 << 21)
    u_max: int = (1 << 21) - 1

    def validate(self) -> None:
        if len(self.coefficients) != 7 or any(not 1 <= len(c) <= TAPS for c in self.coefficients):
            raise ValueError('Exactly seven filters, each with 1..128 taps, are required')
        if len(self.gains) != 8:
            raise ValueError('Eight input/output gains are required')
        for value in self.gains + [self.kp, self.ki] + [v for c in self.coefficients for v in c]:
            if not isinstance(value, int) or value != sat(value, 18):
                raise ValueError('Coefficients and gains must be signed 18-bit integers')
        for value, bits in [(self.reference, 22), (self.i_min, 24), (self.i_max, 24),
                            (self.u_min, 22), (self.u_max, 22)]:
            if not isinstance(value, int) or value != sat(value, bits):
                raise ValueError('Control value outside its signed port width')
        if self.i_min > self.i_max or self.u_min > self.u_max:
            raise ValueError('Limiter minimum exceeds maximum')

    def writes(self) -> list[tuple[int, int]]:
        self.validate()
        result = [(bank * TAPS + tap, value) for bank, c in enumerate(self.coefficients)
                  for tap, value in enumerate(c)]
        result += [(0x420 + bank, len(c) - 1) for bank, c in enumerate(self.coefficients)]
        values = self.gains + [self.kp, self.ki, self.reference, self.i_min, self.i_max,
                               self.u_min, self.u_max]
        return result + [(0x400 + i, value) for i, value in enumerate(values)]


class Gyro:
    def __init__(self, config: Config):
        config.validate()
        self.config = config
        self.history = [[0] * TAPS for _ in range(7)]
        self.integral = 0

    def fir(self, bank: int, sample: int) -> int:
        history = self.history[bank]
        history.insert(0, sample)
        history.pop()
        return sat(sum(x * c for x, c in zip(history, self.config.coefficients[bank])) >> FRAC)

    def step(self, a: int, b: int) -> tuple[int, ...]:
        if a != sat(a, 18) or b != sat(b, 18):
            raise ValueError('ADC samples must be signed 18-bit integers')
        c = self.config
        k1, k2, k3, k4, k5, k6, k7, k8 = c.gains
        drive_mode = sat(mul(k1, a) + mul(k2, b))
        sense_mode = sat(mul(k5, a) + mul(k6, b))
        velocity = self.fir(0, drive_mode)
        envelope = self.fir(1, sat(abs(velocity)))
        error = sat(c.reference - envelope)
        proportional = mul(c.kp, error)
        self.integral = min(c.i_max, max(c.i_min, self.integral + ((c.ki * error) >> FRAC)))
        gain = min(c.u_max, max(c.u_min, sat(proportional + self.integral)))
        drive = mul(velocity, gain)
        sense_feedback = self.fir(2, sense_mode)
        demod_signal = self.fir(3, sense_feedback)
        quadrature_reference = self.fir(4, drive_mode)
        in_phase = self.fir(5, mul(demod_signal, drive_mode))
        quadrature = self.fir(6, mul(demod_signal, quadrature_reference))
        x = sat(mul(k3, drive) + mul(k7, sense_feedback))
        y = sat(mul(k4, drive) + mul(k8, sense_feedback))
        return tuple(sat(v, 18) for v in (x, y, in_phase, quadrature, gain))


def demo_config() -> Config:
    """fs=25 kHz, carrier=fs/16 demonstration, with a four-sample -90deg delay.

    FIR5 is only a quadrature shifter at that carrier. A real sensor requires a
    fitted phase compensator/Hilbert filter and measured loop stability margins.
    """
    return Config(coefficients=[[ONE], [ONE // 16] * 16, [ONE], [ONE],
                                [0, 0, 0, 0, ONE], [ONE // 16] * 16, [ONE // 16] * 16],
                  gains=[ONE, 0, ONE, 0, 0, ONE, 0, -ONE],
                  kp=ONE // 4, ki=ONE // 1024, reference=ONE // 4,
                  i_min=0, i_max=ONE, u_min=0, u_max=ONE)


def analytic_demodulation(samples: int = 1024) -> dict:
    """Compare actual fixed-point path to independently known sinusoid products."""
    model = Gyro(demo_config())
    amplitude, in_phase, quadrature = .5, .125, .0625
    tail = []
    for n in range(samples):
        phase = 2 * math.pi * n / 16
        result = model.step(quantize(amplitude * math.cos(phase)),
                            quantize(in_phase * math.cos(phase) + quadrature * math.sin(phase)))
        if n >= samples // 2:
            tail.append(result)
    expected = [amplitude * in_phase / 2, amplitude * quadrature / 2]
    measured = [sum(row[i] for row in tail) / len(tail) / ONE for i in (2, 3)]
    error = max(abs(a - b) for a, b in zip(expected, measured))
    return {'pass': error < 4 / ONE, 'expected_iq': expected, 'measured_iq': measured,
            'max_abs_error': error, 'kind': 'analytic carrier demodulation; not a MEMS plant model'}
