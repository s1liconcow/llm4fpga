# Receiver selected by CLI design search

`receiver.vhd` is the complete audited winner from two workers and two proposals
per worker. It combines narrower symbol-count division, bounded correlation
widths, and signed multipliers that map to DSP blocks.

It uses 61,034 LUTs, 53,599 registers, 135 DSPs, and 28 BRAM18 equivalents under
Xilinx 7-series mapping. Compared with the original receiver, this saves 11.61%
of LUTs and uses 64 additional DSP blocks. It fits the `xc7a200t` resource profile;
it exceeds the smaller `compact` limits. Physical timing has not been measured.

The selected source passed 15 development and 28 held-out streams in both GHDL
RTL and mapped Verilator simulation, checking 273 packet comparisons per
simulator. See the [results](../../../docs/wifi-search-results.md) for candidate
comparisons, hashes, tool identity, and replay commands. `provenance.json` retains
the source hash and the model's notes from generation, before host verification.

`vivado.tcl` and `clock.xdc` provide a handoff for a user-specified FPGA part.
They have not been executed as part of this experiment.
