# Source file list for the whole chip, in dependency order.
# Used by the Makefile, the cocotb harness, Yosys and the OpenROAD flow so that
# there is exactly one place to add a file.
rtl/pkg/gemm_pkg.sv
rtl/lib/sync_2ff.sv
rtl/lib/reset_bridge.sv
rtl/lib/clock_gate.sv
rtl/lib/sram_1rw.sv
rtl/mem/matrix_store.sv
rtl/host/spi_target.sv
rtl/host/frame_router.sv
rtl/engines/csa_reduce.sv
rtl/engines/acc_bank.sv
rtl/engines/dot_infer.sv
rtl/engines/dot_wallace.sv
rtl/engines/dot_booth4.sv
rtl/engines/dot_signmag.sv
rtl/engines/engine_infer.sv
rtl/engines/engine_wallace.sv
rtl/engines/engine_booth4.sv
rtl/engines/engine_signmag.sv
rtl/engines/engine_bitserial.sv
rtl/seq/engine_array.sv
rtl/seq/gemm_sequencer.sv
rtl/measure/cycle_meter.sv
rtl/measure/mac_meter.sv
rtl/measure/result_checker.sv
rtl/top/bench_core.sv
rtl/top/pad_frame.sv
rtl/top/gemm_bench_chip.sv
