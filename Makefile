.PHONY: test numerical tools replay structured

test:
	python -m pytest -q

numerical:
	python -m fpga_lab structured --out runs/numerical

tools:
	bash scripts/install_xls.sh
	docker build -t xls-e2e-tools:local .

replay:
	python -m fpga_lab agents --provider replay --workers 2 --rounds 3 --out runs/replay

structured:
	python -m fpga_lab structured --hardware --out runs/structured
