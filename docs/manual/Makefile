MAIN := financing_with_doug_manual
PYTHON ?= $(if $(wildcard ../../../.venv/bin/python),../../../.venv/bin/python,$(if $(wildcard ../../.venv/bin/python),../../.venv/bin/python,python3))

.PHONY: all examples pdf check check-source clean
.NOTPARALLEL:
all: examples pdf check

examples:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) examples/build_examples.py

pdf:
	latexmk -pdf -interaction=nonstopmode -halt-on-error $(MAIN).tex

check:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) examples/check_manual.py

check-source:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) examples/check_manual.py --check-source

clean:
	latexmk -c $(MAIN).tex
	rm -f $(MAIN).ilg $(MAIN).ind
