# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# cocotb harness.
#
# cocotb's own makefiles look up python3 and cocotb-config on PATH, so the
# virtualenv has to be on PATH before make starts. The top level Makefile does
# that for you; to drive this file directly, activate the environment first:
#
#   source .venv/bin/activate
#   make -C tb MODULE=test_spi_protocol
#
# TOPLEVEL switches between the two verification entry points:
#   gemm_bench_chip    the whole chip, driven only through its pins
#   tb_engine_harness  every candidate engine side by side on one clock

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

COCOTB_CONFIG := $(shell command -v cocotb-config 2>/dev/null)
ifeq ($(COCOTB_CONFIG),)
  $(error cocotb-config is not on PATH. Run 'make venv' at the repo root, then \
          'source $(REPO_ROOT)/.venv/bin/activate', or drive this through the top level Makefile)
endif

# Icarus is the cocotb backend. Verilator would be faster, but cocotb 2.0
# requires Verilator 5.036 or newer and the pinned toolchain here is 5.020, so
# Verilator is used for linting (make lint) rather than for running tests. Set
# SIM=verilator once a new enough Verilator is available.
SIM ?= icarus
TOPLEVEL_LANG ?= verilog

TOPLEVEL ?= gemm_bench_chip
MODULE ?= test_engine_exact

RTL_FILES := $(shell grep -v '^\#' $(REPO_ROOT)/rtl/filelist.f | grep -v '^$$')
VERILOG_SOURCES := $(addprefix $(REPO_ROOT)/,$(RTL_FILES))

ifeq ($(TOPLEVEL),tb_engine_harness)
VERILOG_SOURCES += $(REPO_ROOT)/tb/tb_engine_harness.sv
endif

export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)

# One build directory per simulator and top level, so switching TOPLEVEL does not
# pick up a stale binary built for the other one.
SIM_BUILD := $(REPO_ROOT)/sim_build/$(SIM)_$(TOPLEVEL)
COCOTB_RESULTS_FILE := $(SIM_BUILD)/results.xml

# Seed for the randomised tests. Pinned by default so a failure is reproducible;
# override with SEED=<n> to widen coverage across runs.
SEED ?= 20260725
export GEMM_SEED := $(SEED)

# Scales the randomised case counts so CI can run a smaller sweep than a full
# local run. Empty means each test uses its own default.
CASES ?=
export GEMM_CASES := $(CASES)

# Where a test that dumps waves should put them.
WAVE_DIR ?= $(REPO_ROOT)/results/trace
export GEMM_WAVE_DIR := $(WAVE_DIR)

ifeq ($(SIM),icarus)
  COMPILE_ARGS += -g2012
endif

ifeq ($(SIM),verilator)
  # Start from a defined state, which is what the behavioural SRAM model and the
  # reset tests assume.
  COMPILE_ARGS += --x-assign 0 --x-initial 0
  ifeq ($(WAVES),1)
    EXTRA_ARGS += --trace --trace-structs
  endif
endif

include $(shell $(COCOTB_CONFIG) --makefiles)/Makefile.sim
