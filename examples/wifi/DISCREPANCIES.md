# Scope and discrepancies

## DISC-001: Physical implementation

- Status: WILL-FIX when an AMD implementation toolchain/board is available.
- Local simulation establishes behavior and cycle counts; physical frequency is
  unmeasured. The 200 MHz clock is a design target.
- Tests: receiver throughput and Vivado implementation.

## DISC-002: Finite Viterbi history

- Status: INVESTIGATING reception-quality impact.
- Hardware uses bounded survivor history; the floating oracle retains a complete
  codeword. Noiseless/punctured codewords are checked exactly. Noise sweeps must
  quantify differences in packet error rate.
- Tests: FEC unit vectors and future receiver sensitivity sweeps.

## DISC-003: Legacy scope

- Status: ACCEPTED scope restriction.
- Generation targets single-antenna 20 MHz 802.11a/g. HT/VHT/HE decoding, MAC
  association, acknowledgements, and RF conversion are outside this experiment.
- Tests: legacy rate matrix; rejection/recovery for unsupported headers remains
  explicitly pending in COVERAGE.md.

## DISC-004: Architecture interfaces

- Status: ACCEPTED for this first integration experiment.
- Codex proposed the decomposition and numerical/streaming architecture. The
  coordinating agent then specified concrete block interfaces and scoreboards.
  Automatic interface-contract generation remains a subsequent experiment.
- Evidence: architecture response, generation prompts, and per-block contracts.

## DISC-005: Compact resource goal

- Status: OPEN optimization target; explicit larger-device profile available.
- Complete mapping uses 69,048 LUTs and 54,066 registers, exceeding the original
  40,000/40,000 limits. It uses 71 DSPs and fourteen 36 Kibit block RAMs.
- `--target xc7a200t` checks the Artix-7 200T capacity. The original `compact`
  limits remain the default; changing the profile does not establish placement
  or timing closure.

## DISC-006: Global formal equivalence

- Status: OPTIONAL proof not completed.
- ABC9's global equivalence check exceeded practical local runtimes. Standard
  Xilinx mapping runs with `abc9.verify=0`; independent RTL and mapped-circuit
  replay provide the behavioral checks. `FPGA_LAB_WIFI_FORMAL=1` requests the proof.

Reviewed: 2026-09-19.
