"""Live Python resonator / Verilator gyro feedback experiment.

Uses Toolchain and HardwareSession transport from the SLAM harness. The plant is
a declared synthetic difference equation, not an identified JPL microgyro.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
import math
from pathlib import Path
import selectors
import shutil
import subprocess

from .cosim import HardwareSession
from .gyro import Gyro, ONE, demo_config, quantize
from .problem import ROOT, digest, write_json
from .toolchain import Toolchain


class GyroSession(HardwareSession):
    def __enter__(self):
        self.log = (self.log_folder / 'server.log').open('w')
        command = self.tools.command(['./server_obj/gyro_server'], self.folder, self.name, interactive=True)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.log, text=True, bufsize=1)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            if self._line() != 'READY':
                raise RuntimeError('Unexpected gyro simulator startup response')
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def exchange(self, command: str, a: int, b: int) -> str:
        self.process.stdin.write(f'{command} {a} {b}\n')
        self.process.stdin.flush()
        return self._line()


def run(out: Path, samples: int = 10000, *, kp: float = 1.75, ki: float = 1 / 1024) -> dict:
    if samples < 10000:
        raise ValueError('At least 10000 samples are required to measure settling')
    if not all(math.isfinite(v) and -2 <= v < 2 for v in (kp, ki)):
        raise ValueError('PI gains must fit signed 18-bit codes with 16 fractional bits')
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'summary.json', {'accepted': False, 'status': 'running', 'tapeout_ready': False})
    for name in ('gyro_core.v', 'gyro_driver.cpp'):
        shutil.copy2(ROOT / 'fpga_lab/hdl' / name, out / name)
    tools = Toolchain.configured()
    tools.run(['verilator', '--cc', '--exe', '--build', '-j', '2', '--top-module', 'gyro_core',
               '--Mdir', 'server_obj', '-o', 'gyro_server', 'gyro_core.v', 'gyro_driver.cpp'],
              out, timeout=180, log='build.log')
    c = demo_config()
    c.coefficients[0] = [0] * 4 + [-ONE]
    c.coefficients[2] = [0] * 4 + [-ONE]
    c.reference = quantize(.3)
    c.i_min = c.u_min = -ONE
    c.kp, c.ki = quantize(kp), quantize(ki)
    oracle = Gyro(c)
    x = xp = .01
    y = yp = yo = yop = 0.0
    omega = 2 * math.pi / 16
    history = []
    max_error = 0
    with GyroSession(out, None) as hardware:
        for address, value in c.writes():
            if hardware.exchange('C', address, value) != 'OK':
                raise RuntimeError('Bad configuration response')
        for n in range(samples):
            if abs(x) >= 1.99 or abs(y) >= 1.99:
                raise RuntimeError('Synthetic plant exceeded ADC range')
            a, b = quantize(x), quantize(y)
            actual = tuple(map(int, hardware.exchange('S', a, b).split()))
            expected = oracle.step(a, b)
            if len(actual) != 5:
                raise RuntimeError('Bad sample response')
            error = max(abs(v - w) for v, w in zip(actual, expected))
            max_error = max(max_error, error)
            if error:
                raise RuntimeError(f'Closed-loop RTL/oracle mismatch at sample {n}: {actual} != {expected}')
            fx, fy = actual[0] / ONE, actual[1] / ONE
            velocity = (x - xp) / math.sin(omega)
            # Two damped resonators, actuator coupling .01, Coriolis-like .0002.
            xn = 2 * .998 * math.cos(omega) * x - .996 * xp + .01 * fx
            yn = 2 * .998 * math.cos(omega) * y - .996 * yp + .01 * fy + .0002 * velocity
            yon = 2 * .998 * math.cos(omega) * yo - .996 * yop + .0002 * velocity
            history.append((n / 25000, x, y, yo, fx, fy, actual[4] / ONE))
            xp, x = x, xn
            yp, y = y, yn
            yop, yo = yo, yon
    tail = history[-1024:]
    envelope = sum(abs(r[1]) for r in tail) / len(tail)
    sense = math.sqrt(sum(r[2] ** 2 for r in tail) / len(tail))
    open_sense = math.sqrt(sum(r[3] ** 2 for r in tail) / len(tail))
    attenuation = 20 * math.log10(open_sense / sense)
    report = {'accepted': abs(envelope - .3) < .01 and attenuation > 10 and max_error == 0,
              'samples': samples, 'rtl_oracle_max_error_lsb': max_error,
              'target_mean_absolute_drive': .3, 'settled_mean_absolute_drive': envelope,
              'sense_rebalance_attenuation_db': attenuation,
              'plant': 'synthetic two-mode difference equation; not calibrated sensor dynamics',
              'feedback_source': 'live Verilator RTL actuator outputs',
              'controller_config': asdict(c),
              'loop_model_sha256': digest(Path(__file__).read_bytes()),
              'rtl_sha256': digest((out / 'gyro_core.v').read_bytes()),
              'driver_sha256': digest((out / 'gyro_driver.cpp').read_bytes())}
    with (out / 'trajectory.csv').open('w') as stream:
        writer = csv.writer(stream)
        writer.writerow(['time_s', 'drive_mode', 'sense_mode', 'open_sense_mode', 'actuator_x', 'actuator_y', 'agc_gain'])
        writer.writerows(history)
    write_json(out / 'summary.json', report)
    return report
