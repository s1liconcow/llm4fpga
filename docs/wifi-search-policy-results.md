# Wi-Fi resource policy comparison

The search now retains the best resource tradeoffs and supports a deterministic
`balanced` objective. Wi-Fi search uses it by default. An LLM proposes changes;
simulation, synthesis, resource caps and the scorer determine eligibility and
selection. LLM opinions cannot override correctness checks or measured resources.

For each resource, utilization is its measured usage divided by the budget
available to this design. Balanced selection minimizes:

1. The highest utilization across LUTs, FFs, DSPs and BRAMs.
2. The sum of all four utilization fractions when the highest values tie.

Remaining ties use the resource fractions in LUT/FF/DSP/BRAM order, then a stable
candidate identifier. Ranking uses exact fractions. Zero budgets require zero
usage. The target supplies budgets; explicit `--max-*` options can tighten them
to reserve resources for other logic. Budgets are fixed before search and pinned
for resume. Scores are comparable within the same budgets, not across policies.

This is a resource-fit heuristic, not a power, timing or silicon-area model. All
functional, numerical and throughput requirements remain hard verification gates.
Physical timing and power remain unmeasured. An LLM could help explain alternatives
or propose new experiments, but the implemented selection stays reproducible.

## Replay of the recorded Wi-Fi measurements

The input is the existing [four-proposal experiment](wifi-search-results.md).
This comparison **does not run new generation, synthesis, simulation or audits**.
It replays development measurements through the updated selection code. The
original frozen selection and its audit are unchanged. The lower-DSP alternative
has development verification only and requires a fresh audit before promotion.

| Policy | Available DSPs | Selected LUTs | Selected DSPs | Highest utilization |
|---|---:|---:|---:|---:|
| Balanced, full XC7A200T | 740 | 61,034 | 135 | 45.34% (LUTs) |
| Balanced, restricted DSP budget | 135 | 68,544 | 71 | 52.59% (DSPs) |
| Balanced, restricted DSP budget | 100 | 68,544 | 71 | 71.00% (DSPs) |
| Balanced, original DSP count | 71 | 68,544 | 71 | 100.00% (DSPs) |
| LUT objective, protect baseline resources | 71 | 68,544 | 71 | Not scored |
| LUT objective, allow resource tradeoffs | 740 | 61,034 | 135 | Not scored |

The full target has budgets of 134,600 LUTs, 269,200 FFs, 740 DSPs and 730 BRAM18
equivalents. Both retained alternatives use 53,599 FFs and 28 BRAM18 equivalents.
The lower-LUT design consumes 18.24% of the full DSP budget. When the receiver is
allocated only 135 DSPs, that same design consumes 100% of its DSP allocation;
balanced selection instead retains headroom with the 71-DSP design.

Both alternatives remain on the Pareto frontier: neither is at least as good as
the other in every resource. Over-cap alternatives remain visible but cannot win.
Workers explore different members of the feasible frontier rather than all
starting from the lowest-LUT candidate.

The [JSON comparison](wifi-search-policy-results.json) includes the input hash,
policy settings, score breakdowns, resource deltas, and candidate source hashes.
The tests in [test_search.py](../tests/test_search.py) replay these measurements,
check hard caps and changed-budget selection, and distinguish development
verification from final acceptance.

## Usage

Search with balanced scoring and up to 100 DSPs allocated to the receiver:

```sh
fpga-lab wifi search --candidate examples/wifi/generated/receiver.vhd \
  --data runs/wifi-data/openofdm --target xc7a200t \
  --objective balanced --max-dsps 100 \
  --workers 2 --rounds 2 --tool-jobs 1 --out runs/wifi-search-balanced
```

Omit `--max-dsps` to use the target's full DSP capacity. Use `--max-dsps 71` to
prohibit DSP growth. Single-objective `--objective luts` protects other baseline
resource counts by default; explicit caps or `--allow-resource-tradeoffs` authorize
growth. Explicit caps never relax the original verification contract.

Generic `fpga-lab search` also supports `--objective balanced`, with all four
budgets supplied by the contract or `--max-*`. Its default remains `luts` because
generic contracts do not necessarily name a target or provide resource budgets.

The changed harness and scoring policy require a fresh output directory for old
runs. New runs can resume only with identical budgets, policy, inputs and tools.
Frozen selections remain terminal: an audit failure cannot trigger reselection.

## Validation

- Full suite: 94 passed, 6 hardware-gated tests skipped.
- Real GHDL/Yosys/Verilator integration: passed with balanced scoring, an explicit
  FF cap that rejects the baseline, zero DSP/BRAM budgets, and resume without
  rebuilding the selected design.
- No new full Wi-Fi search or held-out receiver audit was performed for this
  policy comparison.
