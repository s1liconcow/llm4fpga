# Wi-Fi design search results

The CLI searched beyond the first passing design and selected `worker-02/round-02`.
Mapped LUT usage fell from **69,048 to 61,034**
(**11.61% fewer LUTs**). Registers fell from 54,066
to 53,599. DSP usage increased from **71 to 135**;
block RAM remained at 28 18-Kibit equivalents.
This is a LUT/DSP tradeoff under the `xc7a200t` resource limits. Physical timing,
power, placement, and board execution were not measured.

The run used `gpt-6-astra`, two independent workers, two proposals per
worker, a `luts` objective, and one hardware evaluation at a time. It freshly
verified the saved receiver as a baseline, evaluated all four proposals, froze
the lowest-LUT passing candidate, and then ran the held-out audit. The selected
source is [saved for replay](../examples/wifi/search/receiver.vhd); the full
resource measurements, hashes, case names, and image identity are in
[the results JSON](wifi-search-results.json).

| Candidate | Development verification | LUTs | Registers | DSPs | BRAM18 equivalents |
|---|---|---:|---:|---:|---:|
| baseline | pass | 69,048 | 54,066 | 71 | 28 |
| worker-01/round-01 | pass | 68,912 | 54,066 | 71 | 28 |
| worker-01/round-02 | pass | 69,424 | 54,060 | 71 | 28 |
| worker-02/round-01 | pass | 68,544 | 53,599 | 71 | 28 |
| worker-02/round-02 | pass | 61,034 | 53,599 | 135 | 28 |

The first round explored narrower constant division for OFDM symbol counts;
worker two also reduced correlation arithmetic widths using range bounds. The
next round explored buffer/mux changes in worker one and explicit signed
multiplication for DSP inference in worker two. The buffer/mux rewrite increased
LUT usage, and the search retained the earlier incumbent. The DSP-based candidate
produced the best measured LUT count. Every proposal and its full candidate source
remain in the local run directory.

The selected candidate passed **15 development streams and 28
held-out streams** in both GHDL RTL and Xilinx-mapped Verilator simulation. Together
they checked **273 packet comparisons per simulator**,
including all eight rates, captured radio data, reset/recovery, audit output
stalls, and sustained short and maximum-length packet bursts. Audit results were
not supplied to the workers and did not affect candidate selection. These are
finite test results, not a formal proof over every possible sample stream.

## Run the search

```sh
fpga-lab wifi search --candidate examples/wifi/generated/receiver.vhd \
  --data runs/wifi-data/openofdm --target xc7a200t \
  --workers 2 --rounds 2 --tool-jobs 1 --objective luts \
  --model gpt-6-astra --out runs/wifi-search
```

Model proposals can vary across fresh runs. Use a fresh output directory, or
add `--resume` to the unchanged command for an existing run. After selection,
resume only completes or reuses the frozen candidate's audit.

## Replay the saved winner without a model call

```sh
fpga-lab wifi verify --candidate examples/wifi/search/receiver.vhd \
  --data runs/wifi-data/openofdm --target xc7a200t --out runs/wifi-search-replay-dev
fpga-lab wifi verify --candidate examples/wifi/search/receiver.vhd \
  --data runs/wifi-data/openofdm --target xc7a200t --audit --out runs/wifi-search-replay-audit
```

Fetch the pinned captures first if needed; the [Wi-Fi README](../examples/wifi/README.md)
describes the tools image and data setup. The replay fixture includes a Vivado
handoff, but its 200 MHz clock is a target rather than a measured result.

The software test suite passed 67 tests with six hardware-gated tests skipped.
A separate real generic search integration test passed, including measured
register reduction, held-out mapped replay, and resume without rebuilding.
Resuming this completed Wi-Fi run took 4.19 seconds;
the summary and 64 checked artifacts, including
model logs and compiled simulators, remained unchanged.
