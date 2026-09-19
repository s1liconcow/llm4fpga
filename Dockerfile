# Tool runner for untrusted RTL. The coordinator and its API credentials stay outside.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    iverilog yosys nextpnr-ice40 fpga-icestorm ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
USER 65534:65534
