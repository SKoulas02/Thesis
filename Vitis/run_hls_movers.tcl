# ----------------------------------------------------------------------------
# run_hls_movers.tcl -- S12/S13: csim + csynth + cosim for the two data movers.
#
# Two separate HLS projects, because each has exactly one top function.
#
# USAGE (from this directory, on the server):
#   vitis_hls -f run_hls_movers.tcl
#
# vitis_hls has no GUI-console equivalent of Vivado's Tcl console, so this one
# is a batch run; the results open in the GUI afterwards with
#   vitis_hls -p hls_mm2s
#
# S12 GATE: the mm2s csim must print
#     PASS: N beats reproduce weights.hex exactly, TLAST/TKEEP correct
# That is the shortcut the whole plan leans on -- if the mover reproduces the
# files the RTL was verified against, the existing verification chain carries
# over to hardware unchanged.
#
# PREREQUISITE: run the image packer first, or csim has nothing to read:
#   cd ../GEMV_Dense_Source/Emulation && python3 hex_to_bin.py pack
# ----------------------------------------------------------------------------

set PART   "xcu280-fsvh2892-2L-e"
# 300 MHz -- the initial kernel clock in the link config (INTEGRATION_STEPS S17).
# Raise it later alongside the compute kernel if timing allows.
set PERIOD 3.333

# Vitis HLS 2021.1 ships an mpfr.h that still expects __gmp_const, a symbol
# removed from GMP long ago. csim is fine (Xilinx gcc + its own includes), but
# cosim rebuilds the testbench with flags that leak Ubuntu's gmp.h, and the
# header collapses with hundreds of "__gmp_const does not name a type".
# ap_uint<256> is what drags GMP in at all (>64 bits -> arbitrary precision).
set TBFLAGS "-D__gmp_const=const"

# cosim is the least valuable step here: csynth already reports II, and hw_emu
# (S18) exercises the real mover RTL end-to-end against golden.txt. If cosim
# keeps fighting the toolchain, set this to 0 and move on -- do not lose days.
set DO_COSIM 1

# ---- mm2s ------------------------------------------------------------------
open_project -reset hls_mm2s
add_files krnl_mm2s.cpp
add_files -tb tb_mm2s.cpp -cflags $TBFLAGS
set_top krnl_mm2s
open_solution -reset sol1 -flow_target vitis
set_part $PART
create_clock -period $PERIOD -name default

puts "=== mm2s: csim ==="
csim_design
puts "=== mm2s: csynth ==="
csynth_design
if {$DO_COSIM} {
    puts "=== mm2s: cosim ==="
    cosim_design
} else {
    puts "=== mm2s: cosim SKIPPED (DO_COSIM 0) ==="
}
close_project

# ---- s2mm ------------------------------------------------------------------
open_project -reset hls_s2mm
add_files krnl_s2mm.cpp
add_files -tb tb_s2mm.cpp -cflags $TBFLAGS
set_top krnl_s2mm
open_solution -reset sol1 -flow_target vitis
set_part $PART
create_clock -period $PERIOD -name default

puts "=== s2mm: csim ==="
csim_design
puts "=== s2mm: csynth ==="
csynth_design
if {$DO_COSIM} {
    puts "=== s2mm: cosim ==="
    cosim_design
} else {
    puts "=== s2mm: cosim SKIPPED (DO_COSIM 0) ==="
}
close_project

puts ""
puts "=== done. Check in each report:"
puts "===   csim   : PASS from the testbench"
puts "===   csynth : II=1 on the mm2s/s2mm loop -- II>1 bottlenecks every CU"
puts "===   cosim  : Pass, and note the achieved burst length"
