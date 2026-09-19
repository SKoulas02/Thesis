# ----------------------------------------------------------------------------
# sweep_diagonal_ooc.tcl -- area/Fmax sweep over CORES_NUM x BLOCKS_NUM = 64.
#
# WHY THIS SWEEP IS ALMOST FREE. Every point on the diagonal has the SAME
# external interface, because every bus width is a function of the PRODUCT:
#
#     W_bits   = C x B x 2 x 16 = 2048  -> 8 PCs      (all points)
#     IND_bits = C x B x 10     =  640  -> 3 PCs      (all points)
#     C_bits   = C x B x 16     = 1024  -> 4 PCs      (all points)
#     A_PCS    = 2 always                             17 PCs total
#     MACs/cyc = C x B x 2      =  128  -> 512 DSPs   (all points)
#
# So the wrapper, kernel.xml, link config, host and stimulus are UNCHANGED.
# Only two generics move. The IP set is unchanged too -- weights_fifo_2k
# instantiates W_PCS/IND_PCS copies of ONE axis_data_fifo_pc, and those counts
# do not vary here.
#
# WHAT ACTUALLY VARIES, AND IT PULLS TWO WAYS:
#
#   AREA      each core latches the 512-bit activation window into its own
#             A_internal, so the window costs CORES x 512 FF. (1,64) spends 512;
#             (64,1) spends 32,768. The measured sparse-vs-dense register delta
#             of +3,262 FF at 8x8 is this effect -- 8 x 512 = 4,096.
#
#   FMAX      the broadcast tree is: bus -> CORES registers -> each drives
#             BLOCKS index muxes. The registers ARE the fanout buffers. (1,64)
#             has one register driving 64 32:1 muxes; (64,1) loads one bus with
#             64 registers. Both extremes are bad; 8x8 is balanced 8-and-8.
#
# EXPECTED SHAPE: Fmax peaks near the middle and falls at both ends; FF rises
# monotonically with CORES; DSPs are 512 everywhere. If DSPs ever come back
# different, the generic did not take -- treat that as a failed run, not a result.
#
#  ⚠️ CONSTRAIN TIGHT ON PURPOSE. Use the 2.222 ns (450 MHz) constraint that the
#  existing OOC runs used. A design that MEETS its constraint reports a lower
#  bound, because the tool stops optimising once it passes; a design that MISSES
#  reports its true achieved period. Every point here should fail, and then
#
#      Fmax = 1 / (2.222 ns + |WNS|)
#
#  is comparable with the ~422 MHz already quoted for 8x8 in the thesis.
#
#  TOP IS two2N_axis, NOT two2N, AND THAT IS DELIBERATE. timing.xdc carries two
#  create_clock lines with one commented out; `ap_clk` is the live one, which is
#  the WRAPPER's clock. Sweeping the wrapper therefore needs NO xdc edit, and
#  sidesteps the documented trap where constraining a port that does not exist
#  yields WNS = inf on every run -- a result that looks like a pass and is not.
#
#  The wrapper is a safe subject: it declares the SAME generics and passes all
#  twelve straight down to two2N, and it was measured TRANSPARENT at 8x8 (18
#  LUTs and 40 FFs SMALLER than the bare engine, F7/DSP/BRAM exactly equal). It
#  is also what actually goes into the .xo, so these are the numbers that matter.
#
#  ⚠️ If you ever retarget this at the bare engine, you MUST swap the two
#  create_clock lines in timing.xdc first, and swap them back afterwards.
#
# GUI-SAFE: no `exit`, no `create_project -force`. Run it from the Vivado Tcl
# Console with the GEMV_4.0 project open:
#
#     cd <repo root>/reports          ;# reports land in reports/diagonal_sweep/
#     source ../scripts/vivado/sweep_diagonal_ooc.tcl
#
# Each point is a full synth+impl of the project, so expect 30-60 min per point
# and ~6 hours for all seven. The GUI blocks on wait_on_run; that is expected.
# ----------------------------------------------------------------------------

set DIAG {
    {1 64} {2 32} {4 16} {8 8} {16 4} {32 2} {64 1}
}

set OUTDIR [file join [pwd] diagonal_sweep]
file mkdir $OUTDIR

# The AXIS wrapper. It is transparent (18 LUTs SMALLER than the bare engine at
# 8x8), it forwards every generic to two2N, its clock is the one timing.xdc
# already constrains, and it is what goes into the .xo. No xdc edit needed.
set TOPNAME two2N_axis

puts ""
puts "=============================================================="
puts " CORES x BLOCKS = 64 diagonal sweep"
puts " top      : $TOPNAME"
puts " reports  : $OUTDIR"
puts " points   : [llength $DIAG]"
puts "=============================================================="
puts ""
puts "timing.xdc must have the ap_clk create_clock line ACTIVE (it is, by"
puts "default). If WNS comes back as inf, that line got swapped -- stop and fix."
puts ""

set summary [file join $OUTDIR "diagonal_summary.csv"]
set fh [open $summary w]
# COLUMN NAMES SAY "prim" WHERE THE NUMBER IS A PRIMITIVE COUNT FROM get_cells,
# NOT a report_utilization figure. They are different quantities and must not be
# put beside GEMV_Area_Comparison.xlsx without saying so: report_utilization's
# "CLB LUTs" folds in LUT-as-memory, and its "Block RAM Tile" is fractional
# (a RAMB18 counts 0.5). The canonical figures live in the util_*.rpt files
# saved next to this CSV; parse those if you need to compare across studies.
puts $fh "cores,blocks,blocks_total,macs_per_cycle,wns_ns,tns_ns,failing_endpoints,period_achieved_ns,fmax_mhz,lut_prim,ff_prim,muxf7_prim,dsp_prim,ramb_prim"
close $fh

foreach pair $DIAG {
    lassign $pair C B
    set tag "${C}x${B}"

    puts ""
    puts "-------- CORES_NUM=$C  BLOCKS_NUM=$B   ($tag) --------"

    # THREE generics, not two, and all in one property so the design is never
    # momentarily inconsistent.
    #
    # W_IDX = 2 x BLOCKS_NUM IS MANDATORY. c_core's port is
    #   W_row : std_logic_vector((W_IDX*EL_SIZE)-1 downto 0)
    # while its generate loop slices ((i+1)*2*EL_SIZE)-1 for i in 0..BLOCKS_NUM-1.
    # Those agree only at W_IDX = 2*BLOCKS_NUM (16 at the 8-block default).
    # Leaving it at 16 while changing BLOCKS_NUM gives, at 4x16:
    #   [Synth 8-97] array index 287 out of range [c_core_4.0.vhd:186]
    # and the mirror case at 16x4 overruns W_row_int at the TOP level instead.
    # The product is invariant -- CORES x W_IDX x EL_SIZE = C x 2B x 16 = 2048
    # everywhere on the diagonal -- which is why the interface stays the same
    # while the internal partitioning does not.
    set W [expr {2 * $B}]
    set_property generic "CORES_NUM=$C BLOCKS_NUM=$B W_IDX=$W" [current_fileset]
    set_property top $TOPNAME [current_fileset]

    reset_run synth_1
    launch_runs impl_1 -jobs 8
    wait_on_run impl_1

    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        puts "  !! impl_1 did not complete for $tag -- skipping, inspect the run"
        continue
    }

    open_run impl_1
    report_utilization     -file [file join $OUTDIR "util_${tag}.rpt"]
    report_timing_summary  -file [file join $OUTDIR "timing_${tag}.rpt"]

    # PARSE THE REPORT, do not ask the in-memory design.
    #
    # The obvious version of this --
    #     get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]
    #     get_property STATS.TNS_FAILING_ENDPOINTS [get_runs impl_1]
    # -- FAILS HERE. In out-of-context mode ap_clk has no clock buffer on the
    # path (Vivado says so: "[Timing 38-493] Port ap_clk has one or several leaf
    # clock pins ... without any clock buffer", and "[Timing 38-242] HD.CLK_SRC
    # ... is not set"), so get_timing_paths came back EMPTY and the run's STATS
    # properties were rejected as "not valid in conjunction with other property
    # setting". The result was `can't use empty string as operand of "-"`.
    #
    # report_timing_summary itself works fine and has already been written to
    # disk above, so read the Design Timing Summary out of it. Same approach used
    # for every other timing number in this project, and it does not depend on
    # which properties a given Vivado build exposes.
    set wns {} ; set tns {} ; set fep {}
    set tfile [file join $OUTDIR "timing_${tag}.rpt"]
    if {[file exists $tfile]} {
        set fh [open $tfile r]
        set txt [read $fh]
        close $fh
        set i [string first "Design Timing Summary" $txt]
        if {$i >= 0} {
            # First all-numeric row after the header is the summary line:
            #   WNS(ns)  TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints ...
            # No backslash escapes in this pattern on purpose.
            foreach line [split [string range $txt $i end] "\n"] {
                if {[regexp {^ +([-0-9.]+) +([-0-9.]+) +([0-9]+) +([0-9]+)} \
                            $line -> w t f tot]} {
                    set wns $w ; set tns $t ; set fep $f
                    break
                }
            }
        }
    }
    if {$wns eq {}} {
        puts "  !! could not read WNS from $tfile -- recording the point without timing"
        set wns 0.0 ; set tns 0.0 ; set fep -1
    }

    set luts  [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ LUT*}]]
    set regs  [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ FD*}]]
    set f7s   [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ MUXF7*}]]
    set dsps  [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ DSP48*}]]
    set brams [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ RAMB*}]]

    # 2.222 ns is the constraint; a NEGATIVE wns means the real period is longer.
    set period [expr {2.222 - $wns}]
    set fmax   [expr {1000.0 / $period}]

    set fh [open $summary a]
    puts $fh "$C,$B,[expr {$C*$B}],[expr {$C*$B*2}],$wns,$tns,$fep,[format %.3f $period],[format %.1f $fmax],$luts,$regs,$f7s,$dsps,$brams"
    close $fh

    puts [format "  WNS %.3f ns -> period %.3f ns -> Fmax %.1f MHz" $wns $period $fmax]
    puts [format "  LUT %d   FF %d   MUXF7 %d   DSP %d   BRAM %d" $luts $regs $f7s $dsps $brams]
    if {$dsps != 512} {
        puts "  !! DSP count is $dsps, expected 512 -- the generic may not have taken."
    }

    close_design
}

puts ""
puts "=============================================================="
puts " DONE. Summary: $summary"
puts " Per-point reports: $OUTDIR/util_*.rpt, timing_*.rpt"
puts "=============================================================="
