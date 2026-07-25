# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# OpenROAD place and route, gated on the IHP SG13G2 PDK.
#
# NOTHING IN HERE HAS BEEN RUN in the environment this repository was developed in:
# OpenROAD is not installed and the SG13G2 physical views (tech LEF, cell LEF, SRAM
# macros) are not available. The scripts are complete and the flow is the standard
# Yosys plus OpenROAD sequence, but treat it as untested until you run it. Any area
# number in this repository comes from Yosys, never from here.
#
# Required environment:
#   PDK_ROOT        root of the IHP-Open-PDK checkout
#   SG13G2_LIB      sg13g2_stdcell_typ_1p20V_25C.lib
#   SG13G2_TECH_LEF sg13g2_tech.lef
#   SG13G2_CELL_LEF sg13g2_stdcell.lef
# Optional:
#   SG13G2_IO_LEF   IO cell LEF, to build a real pad frame
#   SG13G2_SRAM_LEF SRAM macro LEF, to bind rtl/lib/sram_1rw.sv to real cuts
#
# tools/fetch_pdk.sh downloads what it can from the public IHP PDK repository.

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
OUT := $(REPO_ROOT)/flow/out
TOP := gemm_bench_chip

OPENROAD ?= openroad
YOSYS ?= yosys

.PHONY: all check synth floorplan place cts route finish gds report clean

all: check synth floorplan place cts route finish report

check:
	@missing=0; \
	for var in SG13G2_LIB SG13G2_TECH_LEF SG13G2_CELL_LEF; do \
	  if [ -z "$${!var}" ]; then echo "missing: $$var"; missing=1; \
	  elif [ ! -f "$${!var}" ]; then echo "missing file: $$var = $${!var}"; missing=1; \
	  fi; \
	done; \
	if ! command -v $(OPENROAD) >/dev/null 2>&1; then \
	  echo "missing: openroad is not on PATH"; missing=1; \
	fi; \
	if [ $$missing -ne 0 ]; then \
	  echo ""; \
	  echo "This flow needs the IHP SG13G2 PDK and OpenROAD. Run"; \
	  echo "  tools/fetch_pdk.sh"; \
	  echo "and install OpenROAD, then try again. Everything else in this"; \
	  echo "repository works without them: make lint sim synth power images."; \
	  exit 1; \
	fi; \
	echo "PDK and OpenROAD found"

$(OUT):
	mkdir -p $(OUT)

# Standard cell mapped netlist, the handoff into OpenROAD.
synth: check | $(OUT)
	TOP=$(TOP) MODE=sg13g2 OUT_DIR=$(OUT) WRITE_NETLIST=1 \
	  FILELIST=$(REPO_ROOT)/rtl/filelist.f \
	  $(YOSYS) -c $(REPO_ROOT)/flow/yosys/synth.tcl
	@test -f $(OUT)/$(TOP)_sg13g2_netlist.v

floorplan: | $(OUT)
	$(OPENROAD) -exit $(REPO_ROOT)/flow/openroad/floorplan.tcl

place: | $(OUT)
	$(OPENROAD) -exit $(REPO_ROOT)/flow/openroad/place.tcl

cts: | $(OUT)
	$(OPENROAD) -exit $(REPO_ROOT)/flow/openroad/cts.tcl

route: | $(OUT)
	$(OPENROAD) -exit $(REPO_ROOT)/flow/openroad/route.tcl

finish: | $(OUT)
	$(OPENROAD) -exit $(REPO_ROOT)/flow/openroad/finish.tcl

report:
	@echo "flow output in $(OUT)"
	@ls -1 $(OUT) 2>/dev/null || true
	@echo ""
	@echo "If $(OUT)/$(TOP).gds exists, render it with:"
	@echo "  python3 $(REPO_ROOT)/tools/render_gds.py $(OUT)/$(TOP).gds"

clean:
	rm -rf $(OUT)
