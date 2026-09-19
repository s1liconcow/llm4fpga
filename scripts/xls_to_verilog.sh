#!/usr/bin/env bash
set -euo pipefail
src="${1:?candidate.x required}"
out="${2:-build/xls}"
mkdir -p "$out"
ir_converter_main --top=main "$src" > "$out/raw.ir"
opt_main "$out/raw.ir" > "$out/opt.ir"
codegen_main --generator=pipeline --pipeline_stages=3 --delay_model=unit \
  --module_name=dut --output_port_name=y --reset=rst \
  --input_valid_signal=in_valid --output_valid_signal=out_valid \
  --output_verilog_path="$out/dut.v" --output_signature_path="$out/signature.textproto" \
  "$out/opt.ir"
echo "RTL: $out/dut.v"
