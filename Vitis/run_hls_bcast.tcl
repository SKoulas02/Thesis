# ----------------------------------------------------------------------------
# run_hls_bcast.tcl -- csim + csynth + cosim for the BROADCAST vector mover,
# one HLS project per engine count (krnl_mm2s_bcast2 / 6 / 7).
#
# A COPY OF run_hls_movers.tcl (2026-09-28), edited; that script is untouched.
#
# USAGE (from the directory holding krnl_mm2s_bcast.cpp + tb_mm2s_bcast.cpp):
#   vitis_hls -f run_hls_bcast.tcl
#
# GATES, per kernel, in the console / reports:
#   csim   : "PASS activations.hex ... on all N outputs" and "=== PASS ==="
#   csynth : the bcast loop at II=1 (hls_bcast<N>/sol1/syn/report/
#            krnl_mm2s_bcast<N>_csynth.rpt) -- II>1 would slow every layer's vector
#   cosim  : Pass
#
# csim needs activations.hex in EMU_DIR (the sparse Emulation directory on the
# server, the default in tb_mm2s_bcast.cpp). No packer run is needed: the testbench
# splits the .hex into its two PC images itself.
# ----------------------------------------------------------------------------

set PART   "xcu280-fsvh2892-2L-e"
# 400 MHz -- the fastest kernel clock any broadcast build targets is 375; check
# the loop at a little above it.
set PERIOD 2.5

# Same workaround as run_hls_movers.tcl: Vitis HLS 2021.1's mpfr.h expects
# __gmp_const, and cosim leaks Ubuntu's gmp.h (ap_uint<256> drags GMP in).
set TBFLAGS "-D__gmp_const=const"

# As in run_hls_movers.tcl: cosim is the least valuable step (csynth reports II,
# and hw_emu runs the real mover RTL against golden). Set 0 if it fights the tools.
set DO_COSIM 1

foreach N {2 6 7} {
    open_project -reset hls_bcast$N
    add_files krnl_mm2s_bcast.cpp
    add_files -tb tb_mm2s_bcast.cpp -cflags "$TBFLAGS -DONLY_N=$N"
    set_top krnl_mm2s_bcast$N
    open_solution -reset sol1 -flow_target vitis
    set_part $PART
    create_clock -period $PERIOD -name default

    puts "=== bcast$N: csim ==="
    csim_design
    puts "=== bcast$N: csynth ==="
    csynth_design
    if {$DO_COSIM} {
        puts "=== bcast$N: cosim ==="
        cosim_design
    } else {
        puts "=== bcast$N: cosim SKIPPED (DO_COSIM 0) ==="
    }
    close_project
}

puts ""
puts "=== done. Check for EACH of bcast2, bcast6, bcast7:"
puts "===   csim   : === PASS === from the testbench"
puts "===   csynth : II=1 on the bcast loop"
puts "===   cosim  : Pass"
