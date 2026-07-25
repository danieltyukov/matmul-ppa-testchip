# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Top level entry point. Every target here works with nothing but the tools listed
# in the README; the only exception is `flow`, which needs the IHP PDK and
# OpenROAD and says so.

SHELL := /bin/bash
REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

VENV := $(REPO_ROOT)/.venv
VENV_PY := $(VENV)/bin/python3
# cocotb's makefiles resolve python3 and cocotb-config from PATH, so every recipe
# that reaches cocotb puts the virtualenv in front.
VENV_ENV := PATH="$(VENV)/bin:$$PATH"

RTL_FILES := $(shell grep -v '^\#' $(REPO_ROOT)/rtl/filelist.f | grep -v '^$$')
RTL_PATHS := $(addprefix $(REPO_ROOT)/,$(RTL_FILES))

TB_DIR := $(REPO_ROOT)/tb
RESULTS := $(REPO_ROOT)/results
BUILD := $(REPO_ROOT)/build

# Randomised test case count. The default is a full local run; CI passes a smaller
# number so a pull request does not wait ten minutes for the same conclusion.
CASES ?=
SEED ?= 20260725

CHIP_TESTS := test_config test_spi_protocol test_end_to_end test_tiling \
              test_perf_counters \
              test_reset_gating
ENGINE_TESTS := test_engine_exact test_engine_equiv

.PHONY: all help venv lint lint-template sim sim-engines sim-chip sim-quick synth \
        synth-pdk power images report flow clean distclean check-tools

all: lint lint-template sim synth power images
	@echo ""
	@echo "lint, sim, synth, power and images all completed."

help:
	@echo "matmul-ppa-testchip"
	@echo ""
	@echo "  make venv        create .venv and install the Python requirements"
	@echo "  make lint        Verilator --lint-only -Wall on the whole chip"
	@echo "  make sim         the full cocotb suite (engines and chip)"
	@echo "  make sim-quick   a reduced sweep, what CI runs"
	@echo "  make synth       Yosys per candidate and for the chip, generic gates"
	@echo "  make synth-pdk   the same mapped to IHP SG13G2 (needs SG13G2_LIB)"
	@echo "  make power       switching-activity proxy: gate level and RTL sweeps"
	@echo "  make images      regenerate every figure in docs/img from results/"
	@echo "  make flow        OpenROAD place and route (needs the IHP PDK)"
	@echo "  make all         lint, sim, synth, power, images"
	@echo ""
	@echo "  make report      print the committed measurements as markdown"
	@echo "  make lint-template lint the candidate skeleton a fork starts from"
	@echo ""
	@echo "  make check-tools report which tools are present"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
venv: $(VENV_PY)

$(VENV_PY): requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r requirements.txt
	@echo "virtualenv ready at $(VENV)"

check-tools:
	@for tool in verilator iverilog vvp yosys klayout python3; do \
	  if command -v $$tool >/dev/null 2>&1; then \
	    printf '  %-12s %s\n' "$$tool" "$$($$tool --version 2>&1 | head -1)"; \
	  else \
	    printf '  %-12s MISSING\n' "$$tool"; \
	  fi; \
	done
	@if command -v openroad >/dev/null 2>&1; then \
	  printf '  %-12s %s\n' openroad "$$(openroad -version 2>&1 | head -1)"; \
	else \
	  printf '  %-12s MISSING (make flow is unavailable)\n' openroad; \
	fi
	@if [ -n "$$SG13G2_LIB" ] && [ -f "$$SG13G2_LIB" ]; then \
	  printf '  %-12s %s\n' SG13G2_LIB "$$SG13G2_LIB"; \
	else \
	  printf '  %-12s not set (make synth-pdk is unavailable)\n' SG13G2_LIB; \
	fi

# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
# The chip is linted with -Wall and no waivers at all: zero warnings is the pass
# condition. The engine harness is a verification-only top that instantiates the
# candidates and nothing else, so every host-interface constant in gemm_pkg is
# genuinely unused in that elaboration. UNUSEDPARAM is waived for that top only, and
# for that reason; no other warning is waived anywhere.
lint:
	verilator --lint-only -Wall -sv --top-module gemm_bench_chip $(RTL_PATHS)
	verilator --lint-only -Wall -Wno-UNUSEDPARAM -sv \
	  --top-module tb_engine_harness $(RTL_PATHS) $(TB_DIR)/tb_engine_harness.sv
	@echo "lint: zero warnings on gemm_bench_chip (no waivers)"
	@echo "lint: zero warnings on tb_engine_harness (UNUSEDPARAM waived, see Makefile)"

# rtl/engines/engine_template.sv is the skeleton a fork copies to add its own
# candidate. It is deliberately not in rtl/filelist.f, so it never reaches any
# measurement, but it has to stay lint clean or the first thing a contributor sees is
# a broken starting point.
lint-template:
	verilator --lint-only -Wall -Wno-UNUSEDPARAM -sv \
	  --top-module engine_template \
	  $(REPO_ROOT)/rtl/pkg/gemm_pkg.sv \
	  $(REPO_ROOT)/rtl/engines/acc_bank.sv \
	  $(REPO_ROOT)/rtl/engines/engine_template.sv
	@echo "lint-template: zero warnings"

# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
sim: sim-engines sim-chip

sim-engines: venv
	@for test in $(ENGINE_TESTS); do \
	  echo "=== $$test (tb_engine_harness) ==="; \
	  $(VENV_ENV) $(MAKE) -C $(TB_DIR) MODULE=$$test TOPLEVEL=tb_engine_harness \
	    CASES=$(CASES) SEED=$(SEED) || exit 1; \
	done

sim-chip: venv
	@for test in $(CHIP_TESTS); do \
	  echo "=== $$test (gemm_bench_chip) ==="; \
	  $(VENV_ENV) $(MAKE) -C $(TB_DIR) MODULE=$$test TOPLEVEL=gemm_bench_chip \
	    CASES=$(CASES) SEED=$(SEED) || exit 1; \
	done

sim-quick:
	$(MAKE) sim CASES=64 GEMM_EXHAUSTIVE_STRIDE=64

# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------
synth: venv
	$(VENV_PY) tools/synth_collect.py --mode generic --netlists

synth-pdk: venv
	@if [ -z "$$SG13G2_LIB" ]; then \
	  echo "synth-pdk needs SG13G2_LIB; run tools/fetch_pdk.sh first" >&2; exit 1; \
	fi
	$(VENV_PY) tools/synth_collect.py --mode sg13g2

# ---------------------------------------------------------------------------
# Power proxy
# ---------------------------------------------------------------------------
power: venv
	$(VENV_PY) tools/activity_sweep.py

# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
images: venv
	$(VENV_PY) tools/svg_architecture.py
	$(VENV_PY) tools/svg_dataflow.py
	$(VENV_PY) tools/svg_memory_map.py
	$(VENV_PY) tools/svg_spi_timing.py
	$(VENV_PY) tools/plot_ppa.py
	$(VENV_PY) tools/plot_activity.py
	$(VENV_PY) tools/plot_floorplan.py
	@echo "images: docs/img is up to date"

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
report: venv
	$(VENV_PY) tools/report_summary.py

# ---------------------------------------------------------------------------
# Place and route
#
# Gated on the IHP PDK and OpenROAD, neither of which is installed in the
# environment this repository was developed in. The scripts and constraints are
# complete and committed; see docs/PPA_METHODOLOGY.md for exactly what has and has
# not been run.
# ---------------------------------------------------------------------------
flow:
	$(MAKE) -C flow all

# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
clean:
	rm -rf $(REPO_ROOT)/sim_build $(BUILD) $(TB_DIR)/__pycache__ \
	       $(REPO_ROOT)/tools/__pycache__ $(REPO_ROOT)/obj_dir \
	       $(REPO_ROOT)/results.xml
	rm -f $(REPO_ROOT)/*.vcd $(TB_DIR)/*.vcd
	$(MAKE) -C flow clean

distclean: clean
	rm -rf $(VENV)
