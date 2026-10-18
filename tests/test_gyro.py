"""Numerical oracles and protocol/report rejection for the gyro extension."""
import json
import subprocess

import pytest

from fpga_lab.gyro import Config, Gyro, ONE, analytic_demodulation, demo_config, mul, sat
from fpga_lab.gyro_hardware import prepare, replay_sky130_netlist, run, scoreboard


def test_signed_rounding_and_limits():
    assert mul(-1, ONE // 2) == -1  # floor, not truncation toward zero
    assert sat(-(1 << 30)) == -(1 << 21)
    assert sat(1 << 30, 18) == 131071
    assert sat(abs(-(1 << 21))) == (1 << 21) - 1


def test_impulse_causal_coefficients_and_ring_wrap():
    config = Config(coefficients=[[ONE, -ONE // 2, ONE // 4]] + [[ONE]] * 6)
    model = Gyro(config)
    assert [model.fir(0, x) for x in [1000, 0, 0, 0]] == [1000, -500, 250, 0]
    for _ in range(256):
        assert model.fir(0, 0) == 0
    assert model.fir(0, -1000) == -1000


def test_output_mixing_sign_and_clipping():
    c = Config(coefficients=[[ONE]] * 7, gains=[ONE, 0, 0, 0, 0, ONE, -ONE, ONE])
    assert Gyro(c).step(0, 1234)[:2] == (-1234, 1234)
    assert Gyro(c).step(0, -131072)[:2] == (131071, -131072)


def test_integrator_clamps_and_recovers():
    c = Config(coefficients=[[ONE]] * 7, gains=[0] * 8, ki=ONE,
               reference=ONE, i_min=-2 * ONE, i_max=2 * ONE, u_min=-ONE, u_max=ONE)
    model = Gyro(c)
    for _ in range(10):
        assert model.step(0, 0)[4] == ONE
    assert model.integral == 2 * ONE
    c.reference = -ONE
    assert model.step(0, 0)[4] == ONE
    assert model.step(0, 0)[4] == 0
    assert model.step(0, 0)[4] == -ONE
    for _ in range(10):
        model.step(0, 0)
    assert model.integral == -2 * ONE


def test_carrier_demodulation_against_analytic_solution():
    assert analytic_demodulation()['pass']


def test_invalid_calibration_rejected():
    for config in (Config(coefficients=[[0]] * 6), Config(coefficients=[[0] * 129] * 7),
                   Config(gains=[ONE] * 7), Config(kp=131072), Config(i_min=1, i_max=0)):
        with pytest.raises(ValueError):
            config.validate()
    with pytest.raises(ValueError):
        Gyro(demo_config()).step(131072, 0)


def test_scoreboard_rejects_corruption_count_and_latency(tmp_path):
    manifest = prepare(tmp_path)
    expected = json.loads((tmp_path / 'expected.json').read_text())
    lines = []
    cycle = 0
    for case in manifest['cases']:
        for values in expected[case['start']:case['start'] + case['samples']]:
            latency = case['latency_cycles'] + 1
            cycle += latency + 1
            lines.append(' '.join(map(str, [cycle, latency, *values])))
    trace = tmp_path / 'trace.txt'
    trace.write_text('\n'.join(lines))
    assert scoreboard(tmp_path, manifest)['pass']
    trace.write_text('\n'.join(lines[:-1]))
    assert not scoreboard(tmp_path, manifest)['pass']
    bad = lines.copy()
    fields = bad[0].split(); fields[2] = str(int(fields[2]) + 1); bad[0] = ' '.join(fields)
    trace.write_text('\n'.join(bad))
    assert not scoreboard(tmp_path, manifest)['pass']
    fields = lines[0].split(); fields[1] = '100000'; bad = lines.copy(); bad[0] = ' '.join(fields)
    trace.write_text('\n'.join(bad))
    assert not scoreboard(tmp_path, manifest)['pass']


def test_timeout_cannot_leave_stale_acceptance(tmp_path):
    class TimedOutTool:
        def run(self, *args, **kwargs):
            # This assertion checks the persisted state while the tool is active.
            assert not json.loads((tmp_path / 'summary.json').read_text())['accepted']
            raise subprocess.TimeoutExpired('simulator', 180)

    (tmp_path / 'summary.json').write_text('{"accepted": true}')
    result = run(tmp_path, tools=TimedOutTool())
    assert not result['accepted']
    assert result['status'] == 'failed'
    assert not json.loads((tmp_path / 'summary.json').read_text())['accepted']


def test_nonempty_physical_model_cannot_be_pruned(tmp_path):
    out = tmp_path / 'out'; out.mkdir()
    library = tmp_path / 'pdk/lib/cells.lib'; library.parent.mkdir(parents=True)
    library.write_text('deliberately incomplete library fixture')
    model = tmp_path / 'pdk/verilog/sky130_fd_sc_hd.v'; model.parent.mkdir()
    model.write_text('module sky130_fd_sc_hd__tapvpwrvgnd_1 (output x); assign x=1; endmodule')
    netlist = tmp_path / 'netlist.v'; netlist.write_text('module gyro_core(); endmodule')
    with pytest.raises(ValueError, match='physical-only cell models'):
        replay_sky130_netlist(out, netlist, library, None)


def test_physical_model_recognition_allows_supplies_but_never_logic():
    from fpga_lab.gyro_hardware import empty_physical_cells
    models = '''
    module sky130_fd_sc_hd__fill_1 ();
      // Power declarations in the real PDK filler model.
      supply1 VPWR; supply0 VGND; supply1 VPB; supply0 VNB;
    endmodule
    module sky130_fd_sc_hd__tapvpwrvgnd_1 (); endmodule
    module sky130_fd_sc_hd__decap_3 (); assign hidden = 1; endmodule
    module sky130_fd_sc_hd__fill_2 (output x); assign x=1; endmodule
    module sky130_fd_sc_hd__fill_4 (); endmodule
    module sky130_fd_sc_hd__fill_4 (); wire hidden; endmodule
    module sky130_fd_sc_hd__fill_8 (); supply1 functional_signal; endmodule
    module sky130_fd_sc_hd__buf_1 (); endmodule
    /* module sky130_fd_sc_hd__fill_16 (); endmodule */
    '''
    assert empty_physical_cells(models) == [
        'sky130_fd_sc_hd__fill_1', 'sky130_fd_sc_hd__tapvpwrvgnd_1']
