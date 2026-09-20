# Receiver coverage

This matrix tracks the experiment's declared legacy PHY contract. It is not an
enumeration of every requirement in the complete IEEE 802.11 standard.

| Requirement | Independent check | Current evidence |
| --- | --- | --- |
| Eight legacy modulation/coding rates | Known synthetic payloads; external IQ | Reference, RTL, and mapped circuit pass |
| External IQ recordings | 14 pinned recordings; CRC32 | All 137 packets pass reference, RTL, and mapped circuit |
| Packet acquisition without metadata | Raw IQ to generated receiver | RTL and mapped circuit pass |
| Long frames and pilot wrap | 4,095-byte synthetic frames; external 4,000-byte frames | Reference, RTL, and mapped circuit pass |
| CRC failure and next-packet recovery | Deliberately corrupted FCS then valid packet | Reference, RTL, and mapped circuit pass |
| FFT arithmetic, reset, latency | Independent NumPy FFT; componentwise error bounds | RTL and mapped development/audit pass |
| Viterbi, punctures, drain boundaries | Independently encoded codewords; lengths 1–32,782 | RTL and mapped checks pass; 114,359 audit bits |
| Hardware demapping through packet output | Floating equalizer outputs to generated backend | RTL development and audit pass; 128 audit frames |
| Raw IQ through complete generated receiver | GHDL and mapped-netlist byte scoreboard | 273 packet comparisons pass in both simulators, with identical output cycles |
| Output stalls | Hold every byte and metadata field stable | RTL and mapped circuit pass, including maximum-frame bursts |
| Reset during reception | Cancel partial frame, reacquire next packet | RTL and mapped circuit pass |
| Malformed SIGNAL | Invalid parity, then a valid frame | RTL and mapped circuit pass |
| HT rejection | Unsupported-header fixtures | Pending |
| Sustained sample processing | Short- and maximum-frame bursts; check latency growth despite correct bytes | Both simulators: zero growth without stalls; 7.5/3.5-cycle changes with stalls, within the 200-cycle limit |
| Counter lifetime | Rebase sample positions without changing ring addresses | Rebase exercised repeatedly in RTL and mapped bursts and captures |
| Mapped block RAM behavior | Published Xilinx model; independent write/read patterns over all 8,192 addresses | Memory-model test passes |
| Physical 200 MHz timing | Vivado implementation on named part | Pending |
| Receiver sensitivity | Packet error curves vs floating reference | Pending |
| Controlled decomposition/refinement ablations | Same source, constraints, evaluation | Pending |

Machine-readable evidence is written next to each candidate. Acceptance of a
component does not set the complete receiver's acceptance flag.
