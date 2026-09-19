# Local demonstration, 19 September 2026

Both source-to-VHDL demos called the installed **Codex CLI, model `gpt-6-astra`**, using
its existing ChatGPT authentication. Both first model candidates passed independently
controlled development and held-out tests. The generated RTL was not hand-edited.

The host is an ARM Mac. Hardware tools run in a rootless Podman Linux VM: GHDL 4.1.0,
Yosys 0.33, Verilator 5.020, Icarus 12.0 and GNU Octave 8.4.0. The pinned Google XLS
release runs in an x86 Linux image through Rosetta. Image IDs, hashes and detailed
measurements are captured in [demo-results.json](demo-results.json).

| Implementation | Verification | Max absolute error | Latency | LUTs | FFs | DSPs |
|---|---|---:|---:|---:|---:|---:|
| Codex Python → SHA-256 VHDL | 149 full messages; 775 held-out compression transactions | 0, bit-exact | 66 | 1,565 | 1,290 | 0 |
| Codex MATLAB → normalizer VHDL | Exhaustive 4,096-code Octave comparison | 5.43344e-5 | 4 | 154 | 53 | 1 |
| Original structured search → new VHDL backend | Exhaustive 4,096-code Octave comparison | 1.35332e-4 | 1 | 90 | 17 | 2 |
| Original structured search → real XLS → Verilog | Exhaustive 4,096-code software reference comparison | 1.35332e-4 | 4 | 92 | 75 | 1 |

LUT counts sum Yosys `LUT1` through `LUT6` cells; muxes and carry cells are reported
separately in JSON. These are technology-mapped Xilinx 7-series counts, not Vivado
placement/routing results. Latency includes the acceptance edge. No physical FPGA,
routed timing, achieved clock frequency or power was measured.

All VHDL rows passed both four-state GHDL RTL simulation and two-state Verilator
simulation of the Yosys-mapped netlist. Protocol checks include reset dominating
valid, cancellation during active work, bubbles, exact latency and output ordering.
The XLS row passed Icarus RTL simulation and Xilinx synthesis. The repaired legacy
multi-worker Verilog replay experiment also passed its exhaustive audit.

The SHA-256 development set contains 23 compression transactions for eight complete
messages and arbitrary states. Its audit contains 775 transactions for 141 additional
complete messages and arbitrary states, including padding boundaries, random binary
messages and a 4,096-byte message. Complete message hashes were independently checked
with `hashlib`. The testbench feeds actual hardware state back between blocks.

The normalizer's 2,048 development and 2,048 audit inputs are disjoint and exhaustive.
Its combined RMS error is approximately **2.10765e-5**, below the **6e-5** limit; its
maximum error is below the **2e-4** limit. Codex chose a 129-knot symmetric LUT,
interpolation, and four pipeline stages. Its bounded integer constant function builds
the table at elaboration, so there is no runtime square-root or divider hardware.

## Reproduce

```bash
source .venv/bin/activate
fpga-lab hash --text abc --out runs/check-abc
# ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad

# Recheck the original Codex responses without another model call:
fpga-lab demo sha256 --replay examples/sha256/recorded_codex.json --out runs/check-sha256
fpga-lab demo normalizer --octave --replay examples/normalizer/recorded_codex.json --out runs/check-normalizer

# Generate new implementations:
fpga-lab demo sha256 --out runs/new-sha256
fpga-lab demo normalizer --octave --out runs/new-normalizer

FPGA_LAB_TEST_HARDWARE=1 pytest -q
```

The 26 local tests pass, including real containerized synthesis/simulation and a
negative test that rejects incorrect latency. The checked-in replay fixtures are
also exercised by the hardware CI workflow; that remote workflow has not been run
from this session.

Original local artifacts are in `runs/sha256-live`, `runs/normalizer-live`,
`runs/normalizer-verified`, `runs/xls-verified` and `runs/legacy-replay`. The first
SHA run was resumed with the same recorded model proposal after replacing slow
Icarus gate simulation with Verilator; no audit feedback was used to revise the RTL.
Final replay runs exercise the completed harness and checked-in fixtures.

[SHA-256 VHDL](../examples/sha256/sha256_core.vhd),
[normalizer VHDL](../examples/normalizer/normalizer_core.vhd), and their adjacent
`vivado.tcl` / `clock.xdc` files are the reviewable hardware handoffs.

These two kernels demonstrate a working translation and verification loop. They do
not establish general arbitrary-program support or state-of-the-art performance;
see the [research milestones](architecture.md#what-would-justify-calling-it-sota).
