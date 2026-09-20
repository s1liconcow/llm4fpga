# Codex → verified VHDL

Translate bounded Python or MATLAB algorithms into synthesizable VHDL using the
installed Codex CLI. A trusted harness checks numerical results and cycle timing,
synthesizes the candidate for AMD/Xilinx 7-series, and simulates the mapped circuit.
Compiler and development-test failures go back to Codex for repair. A separate audit
runs only after candidate selection.

This project runs on Apple Silicon using **Podman**, with Codex on the host and
GHDL, Yosys, Verilator, Icarus and Octave in Linux containers. The original Google XLS
path is also working in a separate x86 Linux container.

**Verified demos:** Python SHA-256, MATLAB nonlinear sensor normalization,
[2D LiDAR SLAM](docs/demo-results.md#slam-hardware-in-a-feedback-loop), and a complete
[802.11a/g Wi-Fi receiver](docs/demo-results.md#wi-fi-a-complete-streaming-receiver).
See [measured results](docs/demo-results.md) and [architecture and scope](docs/architecture.md).
This research prototype translates bounded algorithms with explicit numerical and
streaming contracts. Floating-point source becomes bounded-error fixed-point
hardware; SHA-256 exercises integer and bitwise computation.

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

## Try SLAM

```bash
uv pip install --python .venv/bin/python -e '.[slam]'
fpga-lab demo slam --out runs/slam-core --replay examples/slam/recorded_codex.json
fpga-lab slam --candidate runs/slam-core/final --out runs/slam-loop
```

Omit `--replay` to have Codex generate a new kernel. The host estimates poses and
builds a map from LiDAR scans; each refinement uses normal equations produced by
the actual Xilinx-mapped circuit in simulation. The demo writes trajectory/map
plots and scores drift against ground truth and a separate floating baseline.
[The demo report](docs/demo-results.md) explains the results, the host and hardware
roles, and how to replay public Intel laser data.

## Try the Wi-Fi receiver

The generated 802.11a/g receiver processes raw radio samples into packet bytes
across all eight legacy rates. It combines separately generated acquisition,
FFT, equalization, error-correction, and packet-decoding logic.

Follow the [Wi-Fi setup](examples/wifi/README.md) to build its pinned tools image
and configure an 8 GiB Podman VM, then replay the saved VHDL:

```bash
fpga-lab wifi fetch --data runs/wifi-data
fpga-lab wifi verify --candidate examples/wifi/generated/receiver.vhd \
  --data runs/wifi-data --out runs/wifi-audit --audit --target xc7a200t
```

Verification checks exact packet bytes, checksum status, output stalls, reset,
and sustained input processing in RTL and Xilinx-mapped simulation. Add
`--simulation-only` for an RTL check. [Results and plots](docs/demo-results.md#wi-fi-a-complete-streaming-receiver)
show real radio captures and the scheduling repairs that eliminated backlog.

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

Packed numerical outputs can define `output_fields`, each with its own `name`,
`bits`, `lsb`, `signed`, `scale`, `max_abs_error` and `max_rms_error`. Fields must
cover the bus exactly once; references become arrays in field order. Optional
`max_luts`, `max_ffs`, `max_dsps` and `max_brams` reject oversized mapped candidates.
`fpga-lab prepare slam --out runs/slam-contract` produces a complete example.

```bash
fpga-lab translate path/to/spec.json --out runs/my-kernel --rounds 4
```

To keep searching after the first passing implementation, use `search`:

```bash
fpga-lab search path/to/spec.json --workers 4 --rounds 6 --tool-jobs 1 \
  --objective luts --out runs/design-search
```

Each worker proposes up to `--rounds` candidates, including improvements to passing
designs. `--workers 4 --rounds 6` permits 24 model proposals. `--tool-jobs` limits
concurrent hardware evaluations independently of generation. Workers explore
different implementation strategies and receive measured development feedback.
The lowest-cost passing candidate remains available if later attempts fail or get
larger. All candidates must satisfy the numerical, protocol, and resource contract.

`--objective` selects `luts` (default), `ffs`, `dsps`, `brams`, or declared cycle
`latency`. Other resource budgets remain hard constraints. Equal objective values
are compared by the remaining resource counts, then a stable candidate identifier.
The generic `brams` objective uses the evaluator's RAMB primitive count; Wi-Fi
reports 18-Kibit equivalents. Generic LUT counts currently include LUT1–LUT6
cells; the Wi-Fi evaluator also includes distributed RAM and shift-register LUTs.
Timing and power are not measured search objectives. Search always includes
synthesis and mapped simulation.

Use `--seed path/to/proposal.json` to start from an existing implementation and
measure its baseline. Seeded iterations return exact source replacements; the
harness saves and evaluates a complete VHDL file for every attempt. Full candidate
source stays in the prompt, while resource inventories and packet traces are
summarized for feedback. The provider cannot run tools or edit the scorer.

`leaderboard.json` records candidate resources and the current best. Each
`worker-NN/round-NN/` retains its prompt, response, complete proposal, logs, and
evaluation. After the proposal budget is exhausted, `selection.json` freezes the
winner before opening the held-out audit. `final/` contains the audited VHDL and
Vivado handoff; `summary.json` reports acceptance and improvement over the baseline.
An unchanged baseline may win, and a run can pass with zero improvement. Audit
failure fails the run and never triggers another candidate selection.

Repeat the exact command with `--resume` to recover an interrupted search. Completed
evaluations are reused only when their saved artifacts match their hashes and the
source, contract, fixture identities, seed, model, search settings, harness, and
container image identity still match. A generated proposal saved before an
interrupted evaluation is rechecked without another model request. Once selection
is frozen, resume only completes or reuses that candidate's audit. Use a fresh
output directory to change the search budget or objective.

The streaming receiver has its own adapter; see [Wi-Fi design search](examples/wifi/README.md#search-for-a-smaller-receiver).
The [recorded four-proposal experiment](docs/wifi-search-results.md) reduced receiver
LUTs by 11.61%, trading 64 additional DSPs, and passed the independent audit.

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
