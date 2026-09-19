# Native Linux verification tools; Codex and its credentials stay on the host.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ghdl iverilog yosys nextpnr-ice40 fpga-icestorm octave ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
ENV HOME=/tmp
# Compiled simulation keeps large mapped datapaths practical on a laptop.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    verilator g++ make && rm -rf /var/lib/apt/lists/*
