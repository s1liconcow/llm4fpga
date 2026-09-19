# Codex → verified VHDL

Translate bounded Python or MATLAB algorithms into synthesizable VHDL using the
installed Codex CLI. A trusted harness checks numerical results and cycle timing,
synthesizes the candidate for AMD/Xilinx 7-series, and simulates the mapped circuit.
Compiler and development-test failures go back to Codex for repair. A separate audit
runs only after candidate selection.

This project runs on Apple Silicon using **Podman**, with Codex on the host and
GHDL, Yosys, Verilator, Icarus and Octave in Linux containers. The original Google XLS
path is also working in a separate x86 Linux container.

**Verified demos:** Python SHA-256 and MATLAB nonlinear sensor normalization.
See [measured results](docs/demo-results.md) and [architecture and scope](docs/architecture.md).
This is a research prototype with demonstrated kernels, not a proven SOTA compiler
for unrestricted Python/MATLAB. Floating-point source currently becomes bounded-error
fixed-point hardware; SHA-256 is an integer/bitwise benchmark.

## Setup

Requires Podman, uv, and an authenticated Codex CLI (`codex login`). On macOS,
Podman must have a machine; `scripts/setup.sh` starts or creates one. Apple Silicon
needs Podman's Rosetta support for the optional x86 XLS image.

```bash
bash scripts/setup.sh --xls
source .venv/bin/activate
fpga-lab doctor
```

No API key is needed for the Codex provider. It uses your existing login and chosen
model from `~/.codex/config.toml`; override with `--model` or `CODEX_MODEL`.
This installed CLI uses `codex exec` for noninteractive prompts; `codex -p` selects a
configuration profile. The provider disables shell tools and unrelated MCP/hooks.

## Try the generated SHA-256 core

These commands execute the recorded, verified Codex-generated VHDL in GHDL; they do
not make another model call:

```bash
fpga-lab hash --text abc --out runs/hash-abc
fpga-lab hash --file README.md --out runs/hash-file
```

`abc` produces:

```text
ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
```

The hardware performs all 64 compression rounds. The host pads and splits messages;
the testbench chains the actual hardware result between blocks. The final digest is
checked against independent Python `hashlib`.

Generate a **fresh** implementation with Codex, then compile, simulate, synthesize
and audit it:

```bash
fpga-lab demo sha256 --rounds 3 --out runs/my-sha256
fpga-lab demo normalizer --octave --rounds 4 --out runs/my-normalizer
```

Use a fresh output directory for each experiment. `--resume` rechecks saved candidates
and continues an interrupted run; source, vectors, contract and provider must match.
`--replay examples/sha256/recorded_codex.json` verifies the recorded SHA implementation.
The normalizer has an equivalent `examples/normalizer/recorded_codex.json` fixture.
Replay is explicitly labelled in the manifest. `--simulation-only` skips synthesis.

Each run saves source/vector hashes, the exact prompt and response, development
feedback, a held-out audit, VHDL, simulation traces, synthesis logs, resource counts,
and `final/vivado.tcl`. Errors or audit failures return a nonzero exit status.

## Translate another algorithm

Provide a `.py` or `.m` specification, a JSON contract, and separate independently
computed development/audit vector files. The model translates the source; the host
never executes model-generated Python or MATLAB. A complete example can be created
with `fpga-lab prepare normalizer --out runs/example`.

```json
{
  "name": "my_kernel",
  "source": "algorithm.py",
  "input_bits": 16,
  "output_bits": 16,
  "max_latency": 16,
  "initiation_interval": 1,
  "description": "Define packing, valid input ranges, algorithm and output encoding here.",
  "vectors": "dev.json",
  "audit_vectors": "audit.json",
  "output_signed": true,
  "output_scale": 16384.0,
  "max_abs_error": 0.0002,
  "max_rms_error": 0.00006,
  "clock_mhz": 100.0
}
```

Each vector is `{"input":"0xffff","output":"0xfff0","reference":-0.0009765625}`.
Hex values are unsigned **bit patterns**, including two's-complement signed values.
`reference` is required for approximate arithmetic. For exact integer/bitwise output,
set both error limits to zero; comparison preserves every bit, including 256-bit hashes.
Paths are relative to the contract. Supply packed fixed-size inputs for arrays or structs.

```bash
fpga-lab translate path/to/spec.json --out runs/my-kernel --rounds 4
```

The fixed interface is `dut(clk, rst, in_valid, input_data, out_valid, output_data)`.
Reset is synchronous and active high. Latency counts the acceptance edge as edge 1;
valid bubbles and ordering must be preserved. The host respects the stated initiation
interval. There is no ready/backpressure port. `feedback_bits` optionally chains the
previous output into the high input bits, as used by SHA-256.

The harness verifies every output against the trusted oracle and every valid cycle,
including reset during active work. Coverage outside the supplied vectors is not
proved. The normalizer's development and audit sets together cover all 4,096 inputs.
Clock frequency is a synthesis constraint, not a measured timing result.

## AMD/Xilinx handoff

Generated VHDL and Tcl are in each successful run's `final/` directory. The checked-in
SHA-256 example also includes a handoff:

```bash
vivado -mode batch -source examples/sha256/vivado.tcl -tclargs xc7a35tcpg236-1
```

Choose your actual FPGA part. This performs out-of-context IP synthesis and writes
utilization, timing and a checkpoint. Board pin assignments, integration, placement,
routing and bitstream generation remain board-specific. Vivado has not been executed
on this Mac; [AMD supports x86 Linux and Windows](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Supported-Operating-Systems).
Local LUT/FF/DSP counts come from Yosys Xilinx technology mapping, with GHDL RTL
simulation and Verilator mapped-circuit simulation. No physical FPGA is required.

## Original experiments and tests

```bash
# Original numerical search + new VHDL emitter + exhaustive Octave audit
fpga-lab structured --hardware --octave --out runs/structured

# Real pinned XLS: DSLX -> optimized IR -> Verilog, under Linux x86
fpga-lab xls runs/structured/pwl_s64_f14_nearest/candidate.x --out runs/xls

# Original multi-worker Verilog harness, repaired and containerized
python agent_regression.py --provider replay --workers 2 --rounds 1 --tool-jobs 1

pytest -q
FPGA_LAB_TEST_HARDWARE=1 pytest -q
```

The XLS installer verifies the pinned upstream archive checksum. XLS emits Verilog;
it is not represented as a VHDL backend. Structured-search `proxy_cost` is only a
search heuristic. The legacy `--provider astra` API experiment is optional and requires
`pip install -e '.[agent]'`, `OPENAI_API_KEY`, `ASTRA_MODEL` and optionally `ASTRA_BASE_URL`.

Containers have no network, a read-only root, resource limits, and only the attempt
folder mounted. Codex credentials stay on the host. Stop the VM when finished with
`podman machine stop`.
