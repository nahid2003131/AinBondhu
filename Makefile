.DEFAULT_GOAL := help

PYTHON ?= python3.12
VENV_PYTHON := .venv/bin/python
OUTPUT ?= grounded-next.json

.PHONY: help setup run test check ingest embed evaluate

help:
	@printf '%s\n' \
	  'make setup       Create environment, install dependencies, prepare .env' \
	  'make run         Start Flask (use PORT=8001 for another port)' \
	  'make test        Run offline tests' \
	  'make check       Check PDFs and database embeddings without writes' \
	  'make ingest      Load checked PDF sections into the database' \
	  'make embed       Generate missing document embeddings' \
	  'make evaluate    Run evaluation (use OUTPUT=filename.json for results)'

setup:
	@test -x "$(VENV_PYTHON)" || "$(PYTHON)" -m venv .venv
	"$(VENV_PYTHON)" -m pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	@chmod 600 .env
	@printf '%s\n' 'Setup ready. Fill .env if this is a new checkout, then run make run.'

run:
	"$(VENV_PYTHON)" app.py

test:
	"$(VENV_PYTHON)" -m unittest test_rag -v

check:
	"$(VENV_PYTHON)" ingest.py --check
	"$(VENV_PYTHON)" embed.py --check

ingest:
	"$(VENV_PYTHON)" ingest.py --load

embed:
	"$(VENV_PYTHON)" embed.py --load

evaluate:
	"$(VENV_PYTHON)" evaluate.py --mode grounded --output "$(OUTPUT)"
