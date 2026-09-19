# xls_e2e

Two experiments for automating the manual MATLAB floating-point -> fixed-point FPGA step.

The common benchmark is a signed 12-bit nonlinear sensor normalizer:

```matlab
x = double(adc) ./ 2048.0;
y = x ./ sqrt(0.25 + x .* x);
```

Because there are only 4096 possible ADC inputs, the final numerical result can be exhaustively audited.

## A. Structured search above Google XLS

```
MATLAB golden model
  -> range / word-length / architecture search
  -> bit-exact fixed-point candidate
  -> generated DSLX
  -> XLS IR / optimization / Verilog
  -> FPGA synthesis + P&R
  -> error / area / timing feedback
```

`fpga_lab/structured.py` searches LUT and piecewise-linear architectures, table size,
fractional width and rounding. It writes explicit-width DSLX. The search cost is only
a heuristic - it is deliberately not reported as FPGA LUT area.

Quick numerical run:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
python - <<'PY'
from pathlib import Path
from fpga_lab.problem import Golden, Contract
from fpga_lab.structured import search
search(Golden.load(), Contract(), Path("runs/structured"))
PY
```

To turn a generated candidate into RTL with real XLS:

```bash
bash scripts/install_xls.sh
export PATH="$PWD/.tools/xls/bin:$PATH"
bash scripts/xls_to_verilog.sh runs/structured/<candidate>/candidate.x build/xls
```

The MATLAB file remains the specification. CI can use the Python double-precision mirror;
a MATLAB/Octave-generated CSV can be used as an independent oracle.

## B. Parallel Astra generate-test-feedback loop

`agent_regression.py` is the intentionally less structured experiment. It follows the
same pattern as Poetiq's parallel coding experts:

```
Astra worker -> complete RTL -> trusted simulator/synthesis harness -> feedback
     ^                                                        |
     +----------------------- next iteration -----------------+
```

Multiple workers run concurrently and keep independent histories. Workers see only a
2048-code development set. After search, the coordinator selects the best candidate and
runs a hidden exhaustive 4096-code audit. The golden function, scorer and synthesis
commands are outside the model's control.

The worker score includes:
- protocol/latency correctness
- max and RMS numerical error against the floating-point oracle
- number of samples over the error limit
- Yosys iCE40 LUT/FF counts after numerical correctness is reached

Run:

```bash
pip install -e '.[agent,test]'
sudo apt-get install iverilog yosys
export OPENAI_API_KEY=...
export ASTRA_MODEL=<your Astra deployment/model id>
# Optional for an OpenAI-compatible Astra endpoint:
export ASTRA_BASE_URL=https://...
python agent_regression.py --workers 8 --rounds 10
```

The model identifier is configurable on purpose - the repository does not invent an
Astra API/model name.

### Security boundary

Model-generated RTL is untrusted input. The current `agent_regression.py` includes
static restrictions, but for production use the simulator/synthesis subprocesses should
run inside the networkless resource-limited container described by `Dockerfile`. Do not
give model-generated HDL access to API credentials or a writable host filesystem.

## Current scope

This is a concrete research prototype, not a general MATLAB compiler. Experiment A knows
the mathematical structure of this benchmark and searches its implementation space.
Experiment B asks the model to discover the implementation directly and uses measurements
as regression feedback. The comparison is the interesting part: how much structured
compiler machinery is actually necessary once the generate-test loop becomes capable?
