PYTHON ?= .venv/bin/python
.PHONY: setup test doctor numerical tools replay structured sha256 legacy-replay

setup:
	bash scripts/setup.sh --xls

test:
	$(PYTHON) -m pytest -q

doctor:
	$(PYTHON) -m fpga_lab doctor

numerical:
	$(PYTHON) -m fpga_lab structured --out runs/numerical

tools:
	podman build -t xls-e2e-tools:local .
	podman build --platform linux/amd64 -f Dockerfile.xls -t xls-e2e-xls:local .

replay:
	$(PYTHON) -m fpga_lab demo sha256 --replay examples/sha256/recorded_codex.json --out runs/replay-$$(date +%Y%m%d-%H%M%S)

sha256:
	$(PYTHON) -m fpga_lab demo sha256 --out runs/sha256-$$(date +%Y%m%d-%H%M%S)

structured:
	$(PYTHON) -m fpga_lab structured --hardware --octave --out runs/structured

legacy-replay:
	$(PYTHON) agent_regression.py --provider replay --workers 2 --rounds 1 --tool-jobs 1 --out runs/legacy-replay
