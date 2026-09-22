# Wi-Fi receiver experiment

This experiment translates a floating-point Python 802.11a/g receiver into VHDL.
The goal is raw radio samples to packet bytes, with packet detection, frequency
correction, FFT, equalization, decoding, and checksum verification in hardware.
Development and held-out results are recorded separately. See [coverage](COVERAGE.md)
and [scope and discrepancies](DISCREPANCIES.md) for the current status.

The saved receiver passes **273 packet comparisons across 43 cases** in both
GHDL RTL and Xilinx-mapped Verilator simulation, with identical packet output
cycles. [Measured results](../../docs/wifi-results.json) include resource counts,
source hashes, and generation history.

The source reference decodes **137 packets from 14 pinned OpenOFDM recordings**.
Independent synthetic tests cover all eight legacy rates, frequency offsets,
multipath, checksum failures, and maximum-length frames. Captures are checked
against [SHA-256 digests](captures.json) and retain their upstream provenance.

## Reproduce

Start with the repository's [setup](../../README.md#setup). The larger
mapped simulators need an 8 GiB Podman VM; Wi-Fi commands default to a 6 GiB
container limit (`FPGA_LAB_MEMORY` overrides it).

After stopping any active work in a smaller Podman VM, increase its memory:

```sh
podman machine stop
podman machine set --memory 8192
podman machine start
```

Build the Wi-Fi tools image with GHDL 6 and the pinned OSS CAD Suite release:

```sh
podman build -f Dockerfile.wifi -t xls-e2e-wifi:local .
```

Wi-Fi commands select this image automatically. `FPGA_LAB_IMAGE` overrides it.

```sh
fpga-lab wifi fetch --data runs/wifi-data
fpga-lab wifi reference --data runs/wifi-data --out runs/wifi/reference

# Inspect a generated architecture before translating individual stages.
fpga-lab wifi plan --out runs/wifi/plan
fpga-lab wifi fft --out runs/wifi/fft --rounds 5
fpga-lab wifi viterbi --out runs/wifi/viterbi --rounds 5
fpga-lab wifi backend --blocks runs/wifi --data runs/wifi-data \
  --out runs/wifi/backend --rounds 6
fpga-lab wifi receiver --blocks runs/wifi --data runs/wifi-data \
  --out runs/wifi/frontend --rounds 6
```

Codex CLI supplies generation. Models receive source, contracts, and development
feedback; they cannot execute tools or modify the evaluator. The initial
architecture proposal informed the staged design. Concrete interfaces and
scoreboards were supplied by the coordinating agent and are preserved in [prompts](prompts/).

To replay the saved receiver without calling Codex:

```sh
fpga-lab wifi verify --candidate examples/wifi/generated/receiver.vhd \
  --data runs/wifi-data --out runs/wifi-replay --target xc7a200t
fpga-lab wifi verify --candidate examples/wifi/generated/receiver.vhd \
  --data runs/wifi-data --out runs/wifi-audit --audit --target xc7a200t
```

Verification uses GHDL, Xilinx 7-series mapping with Yosys, then Verilator replay.
Mapped block RAM uses the published [Xilinx simulation model](../../fpga_lab/hdl/README.md);
the other primitives use Yosys's models. Check the memory model independently with
`FPGA_LAB_TEST_WIFI_HARDWARE=1 pytest tests/test_wifi.py -q`.
Add `--simulation-only` for a faster RTL check. The saved receiver uses 69,048
LUTs, 54,066 registers, 71 DSPs, and fourteen 36 Kibit block RAMs. It fits the
Artix-7 200T resource profile; the original `compact` profile (40,000 LUTs and
40,000 registers) remains the default and rejects this candidate.
Device capacities come from [AMD DS180](https://docs.amd.com/v/u/en-US/ds180_7Series_Overview).
Full mapping, compilation, and replay take tens of minutes on this Mac. Mapped
streams run in four isolated simulator jobs; `FPGA_LAB_WIFI_JOBS=1` selects serial
execution. Tool logs and individual case results remain in the output directory.
The mapping script uses the standard Xilinx flow. ABC9's optional global formal
equivalence proof is disabled for practical laptop runtimes; correctness is
checked by independent RTL and mapped-circuit simulation. Set
`FPGA_LAB_WIFI_FORMAL=1` to request the additional proof.
It checks packet bytes, rate/length metadata, checksums, output stalls, and reset.
The proposed input schedule is 20 MSamples/s with a 200 MHz hardware clock.
Physical timing and board execution require separate Vivado implementation.

## Search for a smaller receiver

The [recorded search](../../docs/wifi-search-results.md) reduced mapped LUTs from
69,048 to **61,034** while increasing DSPs from 71 to 135. The selected
[receiver](search/receiver.vhd) passed all 43 development and held-out streams in
RTL and mapped simulation. Its [provenance](search/provenance.json) and resource
tradeoffs are preserved separately from the original baseline.

Start from the complete saved receiver and search using its streaming scoreboard:

```sh
fpga-lab wifi search --candidate examples/wifi/generated/receiver.vhd \
  --data runs/wifi-data/openofdm --target xc7a200t \
  --workers 2 --rounds 2 --tool-jobs 1 --objective balanced --max-dsps 71 \
  --out runs/wifi-search-capped
```

Set `--data` to the directory containing the fetched `testing_inputs/` tree. The
command above allows four design proposals plus a fresh baseline evaluation and a
final held-out audit. The saved receiver exceeds the default `compact` limits, so
the example explicitly selects `xc7a200t`. Selecting `compact` instead asks workers
to meet the smaller budgets; an oversized baseline cannot win that search.

The Wi-Fi default is `balanced`: minimize the highest resource usage / budget
ratio across LUTs, FFs, DSPs and BRAM18 equivalents, then total utilization. The
target supplies the budgets; `--max-*` options can tighten them to reserve capacity
for other logic. All caps remain mandatory. The explicit `--max-dsps 71` above
prevents DSP growth. Remove it to use the full target DSP budget, or set
`--max-dsps 100` for limited growth. A cap can never relax the target profile.

On the recorded measurements, balanced selection with the full XC7A200T budget
prefers 61,034 LUTs / 135 DSPs. With only 135 DSPs available to the receiver, it
prefers 68,544 LUTs / 71 DSPs, retaining DSP headroom. This is resource allocation,
not a prediction of timing or power. See the
[recorded policy comparison](../../docs/wifi-search-policy-results.md).

Single-objective searches such as `--objective luts` protect other resources at
baseline usage unless an explicit cap authorizes growth for a resource or
`--allow-resource-tradeoffs` permits growth within the target limits.

Workers receive the reference algorithm, streaming contract, complete current
VHDL, and development feedback. They propose exact source edits, which the host
applies and saves as complete candidate files. The objective is `balanced`,
`luts`, `ffs`, `dsps`, or `brams` (18-Kibit equivalents). Packet correctness, output
stalls, reset, sustained processing rate, and every selected resource limit remain
mandatory. The receiver's JSON `latency` field is a schema placeholder and is not
used as a streaming performance objective.

Every evaluated candidate uses GHDL RTL simulation, Xilinx mapping, and mapped
Verilator replay when earlier stages pass. Each worker continues after a passing
candidate. Workers explore the best resource tradeoffs, and the selected objective
chooses a winner within the search caps. A fresh audit
checks the frozen winner, and audit failures never feed back into generation.
The original receiver remains unchanged; the selected implementation is written
to `runs/wifi-search-capped/final/candidate.vhd` with a Vivado handoff.

Inspect `leaderboard.json` for resource deltas, cap violations, the complete Pareto
frontier, and its feasible subset. Over-cap alternatives remain available for
inspection but cannot win. `summary.json` includes the baseline, winner, all
resource changes, and audit verdict; frontier alternatives have development
verification only. `balanced_score` includes both score terms, the per-resource
utilization, budgets, and limiting resources. An LLM proposes changes; the
deterministic scorer and independent checks decide selection and acceptance.
Add `--resume` to the same command
to reuse completed evaluations and continue interrupted work. After the winner is
frozen, resume cannot extend the search or choose a different candidate. Changing
caps or tradeoff policy requires a new output directory; old-version runs cannot
resume with the changed harness.

Full receiver mapping and simulator builds can take tens of minutes per candidate.
Keep `--tool-jobs 1` on the 8 GiB VM; generation can still run concurrently. The
search measures mapped resource savings, while the 200 MHz clock remains a target
requiring physical implementation and timing verification.
