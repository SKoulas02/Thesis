# ----------------------------------------------------------------------------
# sweep_families_ooc.tcl -- OOC area/Fmax sweep ACROSS BLOCK-COUNT FAMILIES.
#
# The C x B = 64 diagonal answered "how should 64 blocks be partitioned?" and
# said 8 cores. This sweep asks whether that verdict SURVIVES AT OTHER SIZES, by
# testing CORES in {4, 8, 16} within each family, and at the same time produces
# the DSP-vs-HBM-channel scaling series.
#
#   T = CORES x BLOCKS       DSPs = 8 x T      (2 mult + 1 add + 1 acc per
#                                               block, 2 DSPs each)
#
#   W_IDX    = 2 x BLOCKS            IND_BITS = 10 x T
#   W_PCS    = ceil(T / 8)           IND_PCS  = ceil(IND_BITS / 256)
#   C_PCS    = ceil(T / 16)          A_PCS    = 2  (the 32-element activation
#   HBM PCs  = W + IND + A + C             window is 512b whatever T is)
#
#  ⚠️ SEVEN GENERICS MOVE, NOT THREE. On the C x B = 64 diagonal only
#  CORES_NUM/BLOCKS_NUM/W_IDX changed, because every bus width is a function of
#  the PRODUCT and the product was fixed. Off the diagonal the product moves, so
#  W_PCS, IND_PCS, C_PCS and IND_BITS must move with it. Leaving any of them
#  behind gives an out-of-range slice at synthesis, exactly as leaving W_IDX at
#  16 gave "[Synth 8-97] array index 287 out of range" at 4x16.
#
#  ⚠️ TOP IS two2N -- THE BARE ENGINE -- AND THAT IS FORCED, NOT A PREFERENCE.
#  two2N_axis declares its PC ports INDIVIDUALLY (s_axis_w0_tdata ...
#  s_axis_w7_tdata, each PC_WIDTH wide), so it is hard-wired to 8 weight PCs and
#  cannot follow W_PCS. The bare engine takes
#      s_axis_w_tdata : std_logic_vector(W_PCS*PC_WIDTH-1 downto 0)
#  and resizes from the generic. Only the engine can be swept off-diagonal.
#
#  ⚠️⚠️ timing.xdc MUST HAVE THE `clk` LINE LIVE, NOT `ap_clk`.
#  The engine's clock port is `clk`. Constraining a port that does not exist is
#  NOT an error -- create_clock on an empty object list raises a critical
#  warning and the run completes with EVERY PATH UNCONSTRAINED, reporting NA
#  across the timing summary. That cost hours on 2026-09-08 in the opposite
#  direction (ap_clk needed, clk live). Before running:
#
#      cd ~/GEMV_Sparse/GEMV_4.0_Source
#      sed -i 's/^create_clock \(.*ap_clk.*\)/#create_clock \1/' timing.xdc
#      sed -i 's/^#create_clock \(.*name clk .*\)/create_clock \1/' timing.xdc
#      grep -n "create_clock" timing.xdc      # clk live, ap_clk commented
#
#  SWAP IT BACK AFTERWARDS -- the .xo packaging and every Vitis link use the
#  wrapper and need ap_clk.
#
#  ⚠️ T=64 IS DELIBERATELY ABSENT -- already measured, on the WRAPPER. That
#  leaves a small methodology seam between the two studies. It is defensible:
#  the wrapper was measured transparent at 8x8 (18 LUTs and 40 FFs SMALLER than
#  the bare engine, F7/DSP/BRAM exactly equal). Say so in the write-up rather
#  than presenting the two sets as one homogeneous sweep.
#
# CONSTRAIN TIGHT ON PURPOSE: 2.222 ns (450 MHz), the same constraint as every
# other OOC number in this project. A design that MEETS its constraint reports a
# LOWER BOUND because the tool stops optimising; one that misses reports its true
# achieved period. Fmax = 1000 / (2.222 - WNS).
#
# GUI-SAFE: no `exit`. Run from the Vivado Tcl Console with GEMV_4.0 open:
#     cd <repo root>/reports          ;# reports land in reports/family_sweep/
#     source ../scripts/vivado/sweep_families_ooc.tcl
# ----------------------------------------------------------------------------

# {CORES BLOCKS} -- grouped by family, ordered small to large so the cheap
# points land first and an early stop still leaves whole families.
#  ⚠️ T MUST BE A MULTIPLE OF 16. The engine drives c_fifo with
#      Cout_int : (BLOCKS_NUM*CORES_NUM*EL_SIZE)-1 downto 0   = T x 16 bits
#  while c_fifo's port is
#      Cout     : (OUT_PCS*PC_WIDTH)-1 downto 0               = C_PCS x 256
#  Those agree only when T x 16 is a whole multiple of 256, i.e. T mod 16 = 0.
#  The output beat has to FILL whole pseudo-channels. T=8 gives 128 bits against
#  a 256-bit port and synthesis rejects it in about 40 seconds -- the entire
#  {2 4} {4 2} {8 1} family was structurally invalid, not unlucky.
#
#  That quantisation is itself a result worth stating: 16 blocks is the
#  architecture's smallest viable size on a 256-bit HBM pseudo-channel.
#  T=64 IS INCLUDED even though the C x B = 64 diagonal already measured those
#  three configurations. Re-running them here removes TWO seams rather than
#  leaving them as caveats: the diagonal used the 2.222 ns constraint and the
#  two2N_axis WRAPPER, while this sweep uses $PERIOD and the BARE ENGINE. The
#  wrapper is transparent (18 LUTs SMALLER at 8x8) and Vivado's effort does not
#  change much once a design is failing, so the seams are small -- but T=64 is
#  one of the six DSP/HBM points, and it costs 3 runs to have every number in
#  the study share one method. Keep the diagonal data as the separate study it
#  is; do not merge the two CSVs.
set FAM {
    {4 4} {8 2} {16 1}
    {4 8} {8 4} {16 2}
    {4 12} {8 6} {16 3}
    {4 16} {8 8} {16 4}
    {4 24} {8 12} {16 6}
    {4 32} {8 16} {16 8}
}

set OUTDIR [file join [pwd] family_sweep]
file mkdir $OUTDIR

set TOPNAME two2N

# ---- the constraint, and why it must FAIL everywhere --------------------------
# MUST MATCH timing.xdc. Fmax = 1000 / (PERIOD - WNS) is only a MEASUREMENT when
# the design MISSES the constraint; a design that MEETS it reports a lower bound,
# because Vivado stops optimising the moment it passes.
#
# 2.222 ns (450 MHz) was tight enough for the C x B = 64 diagonal -- every point
# there missed it. It is NOT tight enough for the small families: 4x4 and 8x2 at
# T=16 both came back with POSITIVE WNS (+0.110, +0.124) and 0 failing
# endpoints, so their 473/477 MHz are floors, and the 14 ps between them is
# leftover slack rather than a speed difference. Ranking on that is meaningless.
#
# Set this BELOW what the fastest configuration can reach, so every point misses
# and every number is real. Keep it in sync with timing.xdc BY HAND -- nothing
# checks that they agree, and a mismatch silently rescales every Fmax.
set PERIOD 1.5

# ---- threading -------------------------------------------------------------
# TWO SEPARATE KNOBS, AND ONLY ONE OF THEM MATTERS HERE.
#
#   launch_runs -jobs N   caps how many RUNS execute CONCURRENTLY. This sweep
#                         launches ONE run at a time, so raising it does almost
#                         nothing. It is set high anyway because it costs
#                         nothing and would help if the flow ever fans out.
#
#   general.maxThreads N  caps threads WITHIN synth_design/place_design/
#                         route_design. THIS is the lever on a 40-core machine.
#
# Vivado caps per-command threading regardless of what is asked for -- 8 is the
# documented ceiling for most implementation steps in 2021.1, and several steps
# are single-threaded no matter what. So do not expect 40 cores to give 40x:
# past ~8 threads the returns on a design this size are close to nil. Ask for
# more, take what is granted, and report it.
set JOBS 40
set THREADS 0
foreach n {40 32 16 8 4 2} {
    if {![catch {set_param general.maxThreads $n}]} { set THREADS $n ; break }
}
if {$THREADS == 0} { set THREADS "default (unchanged)" }

proc ceildiv {a b} { return [expr {($a + $b - 1) / $b}] }

puts ""
puts "=============================================================="
puts " BLOCK-COUNT FAMILY SWEEP -- does CORES=8 stay optimal?"
puts " top     : $TOPNAME   (bare engine -- timing.xdc needs `clk` LIVE)"
puts " points  : [llength $FAM]"
puts " threads : general.maxThreads = $THREADS   (launch_runs -jobs $JOBS)"
puts " period  : $PERIOD ns -- timing.xdc MUST say the same; every point must MISS it"
puts " reports : $OUTDIR"
puts "=============================================================="
puts ""
puts "If every WNS comes back as NA, timing.xdc still has ap_clk live."
puts ""

set summary [file join $OUTDIR "family_summary.csv"]
set fh [open $summary w]
puts $fh "config,cores,blocks,blocks_total,macs_per_cycle,dsps_expected,hbm_pcs,w_pcs,ind_pcs,a_pcs,c_pcs,w_idx,ind_bits,wns_ns,tns_ns,failing_endpoints,period_ns,fmax_mhz,lut_prim,ff_prim,muxf7_prim,dsp_prim,ramb_prim"
close $fh

foreach pair $FAM {
    lassign $pair C B
    set tag "${C}x${B}"
    set T [expr {$C * $B}]

    set W_IDX    [expr {2 * $B}]
    set W_PCS    [ceildiv [expr {$T * 32}] 256]
    set IND_BITS [expr {$T * 10}]
    # +2 IS NOT A FUDGE. The sparsity code rides in the index PADDING, above the
    # index bits themselves:
    #     Sparsity_out <= ind_concat(IND_BITS+1 downto IND_BITS)
    # so the joined index word must be at least IND_BITS+2 wide. Plain
    # ceil(IND_BITS/256) fails whenever IND_BITS lands exactly on a channel
    # boundary -- at T=128, IND_BITS=1280 = 5 x 256 exactly, leaving no room and
    # putting the slice off the end. That killed the whole T=128 family.
    # Every other family already had slack, so this changes nothing else.
    set IND_PCS  [ceildiv [expr {$IND_BITS + 2}] 256]
    set C_PCS    [ceildiv [expr {$T * 16}] 256]
    set A_PCS    2
    set PCS      [expr {$W_PCS + $IND_PCS + $A_PCS + $C_PCS}]
    set DSP_EXP  [expr {8 * $T}]

    puts ""
    puts "-------- $tag   T=$T   ${DSP_EXP} DSP   ${PCS} PC --------"
    puts "  W_IDX=$W_IDX W_PCS=$W_PCS IND_PCS=$IND_PCS C_PCS=$C_PCS IND_BITS=$IND_BITS"

    set_property generic "CORES_NUM=$C BLOCKS_NUM=$B W_IDX=$W_IDX W_PCS=$W_PCS IND_PCS=$IND_PCS C_PCS=$C_PCS IND_BITS=$IND_BITS" [current_fileset]
    set_property top $TOPNAME [current_fileset]

    reset_run synth_1
    launch_runs impl_1 -jobs $JOBS
    wait_on_run impl_1

    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        puts "  !! impl_1 did not complete for $tag -- skipping"
        continue
    }

    open_run impl_1
    report_utilization    -file [file join $OUTDIR "util_${tag}.rpt"]
    report_timing_summary -file [file join $OUTDIR "timing_${tag}.rpt"]

    # Read WNS/TNS out of the REPORT. get_timing_paths and the run's STATS.*
    # properties come back empty in OOC mode (no clock buffer on the clock port,
    # HD.CLK_SRC unset), which is what broke the first diagonal sweep.
    set wns {} ; set tns {} ; set fep {}
    set tfile [file join $OUTDIR "timing_${tag}.rpt"]
    if {[file exists $tfile]} {
        set fh [open $tfile r]
        set txt [read $fh]
        close $fh
        set i [string first "Design Timing Summary" $txt]
        if {$i >= 0} {
            foreach line [split [string range $txt $i end] "\n"] {
                if {[regexp {^ +([-0-9.]+) +([-0-9.]+) +([0-9]+) +([0-9]+)} $line -> w t f tot]} {
                    set wns $w ; set tns $t ; set fep $f
                    break
                }
            }
        }
    }
    if {$wns eq {}} {
        puts "  !! no WNS in $tfile -- recording without timing (check timing.xdc)"
        set wns 0.0 ; set tns 0.0 ; set fep -1
    }

    set luts  [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ LUT*}]]
    set regs  [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ FD*}]]
    set f7s   [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ MUXF7*}]]
    set dsps  [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ DSP48*}]]
    set brams [llength [get_cells -hier -filter {IS_PRIMITIVE && REF_NAME =~ RAMB*}]]

    set period [expr {$PERIOD - $wns}]
    set fmax   [expr {1000.0 / $period}]

    set fh [open $summary a]
    puts $fh "$tag,$C,$B,$T,[expr {2*$T}],$DSP_EXP,$PCS,$W_PCS,$IND_PCS,$A_PCS,$C_PCS,$W_IDX,$IND_BITS,$wns,$tns,$fep,[format %.3f $period],[format %.1f $fmax],$luts,$regs,$f7s,$dsps,$brams"
    close $fh

    puts [format "  WNS %.3f -> %.1f MHz    LUT %d  FF %d  MUXF7 %d  DSP %d" \
          $wns $fmax $luts $regs $f7s $dsps]

    # THE SANITY CHECK THAT MATTERS. DSPs are 8 per block, so a wrong count means
    # a generic did not take and the row is void -- not a result.
    if {$wns > 0} {
        puts "  !! WNS is POSITIVE -- this point MET the constraint, so $fmax MHz is a"
        puts "     LOWER BOUND, not a measurement. Tighten PERIOD and timing.xdc."
    }
    if {$dsps != $DSP_EXP} {
        puts "  !! DSP $dsps, expected $DSP_EXP -- GENERICS DID NOT TAKE, row is void"
    }
    # MUXF7 = 128 per 64 blocks = 2 gathers per block x 16 bits x 4 MUXF7
    set f7_exp [expr {128 * $T}]
    if {$f7s != $f7_exp} {
        puts "  .. MUXF7 $f7s vs expected $f7_exp (worth a look, not fatal)"
    }

    close_design
}

puts ""
puts "=============================================================="
puts " DONE. $summary"
puts " Remember to swap timing.xdc BACK to ap_clk before any .xo or link."
puts "=============================================================="
