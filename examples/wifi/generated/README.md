# Saved implementations

`receiver.vhd` is the complete selected receiver: frontend, FFT, Viterbi decoder,
and packet backend in one synthesizable file. Replay it using the commands in
[the experiment README](../README.md).

`fft.vhd`, `viterbi.vhd`, and `backend.vhd`, with their JSON proposals, preserve
the separately generated blocks before integration. The integrated receiver
contains subsequent backend synthesis and output-bandwidth repairs. Their exact
changes are in [the generation record](../generation/README.md).
