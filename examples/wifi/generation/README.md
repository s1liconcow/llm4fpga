# Generation record

These are the original Codex prompts and structured responses. The model received
source code, interfaces, constraints, and development feedback. Its tools were
disabled. Capture payloads and audit failures were not supplied to it.

The sequence was:

1. Propose an architecture, then attempt a complete receiver. The complete attempt
   returned empty VHDL and was rejected.
2. Generate and test FFT, Viterbi, and packet-decoding blocks separately.
3. Generate the acquisition/equalization frontend and connect those blocks. Three
   frontend candidates repaired packet acquisition and sustained processing rate.
4. Apply narrowly scoped Codex patches for GHDL compatibility and sample-counter
   lifetime. Each patch contains exact replacements and its explanation.
5. Repair output bandwidth after a maximum-frame burst revealed a second source
   of backlog. The backend now emits one byte per ready clock, and the frontend
   sends consecutive symbols exactly 800 clocks apart.

FFT candidate 4 was selected after increasing the Podman VM memory; candidate 5
had already been generated when the earlier simulator builds hit their memory
limit. Backend candidate 2 repaired a reserved-word compile error. Later compiler
patches also changed the backend's slice casts and integer absolute value.

The coordinating agent supplied concrete interfaces, built the evaluator,
diagnosed compiler failures, and identified the counter-lifetime issue. The
generated receiver's algorithms and VHDL repairs came from Codex.

[manifest.json](manifest.json) records hashes and CLI-reported token counts. Those
counts describe generation calls, and exclude the coordinating session and tools.
