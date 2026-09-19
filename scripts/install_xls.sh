#!/usr/bin/env bash
# Pinned upstream Linux x86_64 binary release; verify archive before extraction.
set -euo pipefail
version=v0.0.0-10704-g0a7c502cc
sha=62eaad69e9545371179485af8208004555c3a9eb6a037f935a0b154f08194f74
root="$(cd "$(dirname "$0")/.." && pwd)/.tools/xls"
archive="xls-${version}-linux-x64.tar.gz"
mkdir -p "$root/bin" "$root/dist"
curl --fail --location --retry 3 "https://github.com/google/xls/releases/download/${version}/${archive}" -o "$root/$archive"
printf '%s  %s\n' "$sha" "$root/$archive" | sha256sum --check -
tar -xzf "$root/$archive" -C "$root/dist"
for tool in ir_converter_main opt_main codegen_main; do
  binary="$(find "$root/dist" -type f -name "$tool" -print -quit)"
  test -n "$binary" || { echo "Missing $tool in release" >&2; exit 1; }
  chmod +x "$binary"
  ln -sfn "$binary" "$root/bin/$tool"
done
printf 'export PATH="%s/bin:$PATH"\n' "$root"
