# ----------------------------------------------------------------------------
# create_dense_2021.tcl  --  builds the DENSE GEMV project on Vivado 2021.1
#
# Self-contained: creates the project, generates all 7 IP cores natively at
# 2021.1 revisions, adds the RTL, and (optionally) runs OOC synthesis +
# implementation and dumps the utilisation / timing reports.
#
# WHY the IP is recreated rather than copied: the original build is Vivado
# 2022.2, whose IP revisions (floating_point 7.1 rev 15, axis_data_fifo 2.0
# rev 9, fifo_generator 13.2 rev 7) are NEWER than the 2021.1 catalog. Vivado
# upgrades IP forward only -- a 2022.2 .xci opened in 2021.1 comes up LOCKED and
# cannot generate output products. The CONFIG values below were extracted from
# those .xci files, and the sparse script uses the identical block, so both
# designs provably get the same IP and the area comparison stays honest.
#
# LAYOUT EXPECTED -- put this script next to the source trees:
#   <root>/create_dense_2021.tcl
#   <root>/GEMV_Dense_Source/{Design,Simulation,Emulation}, timing.xdc
#
# USAGE
#   vivado -mode batch -source create_dense_2021.tcl -nojournal -nolog
#   vivado -mode batch -source create_dense_2021.tcl -tclargs impl   ;# + synth/impl
#
# Add "impl" to also run OOC synthesis + implementation and write reports.
# Note: synthesis MUST be out-of-context -- the top has 3628 ports, far beyond
# any package, so a bonded build cannot place.
# ----------------------------------------------------------------------------

# ---- configuration ---------------------------------------------------------
set root      [file normalize [file dirname [info script]]]
set src       "$root/GEMV_Dense_Source"
set proj_name "GEMV_Dense"
set proj_dir  "$root/$proj_name"
set part      "xcu280-fsvh2892-2L-e"
set jobs      8

# Run synthesis + implementation as well?
#   batch : vivado -mode batch -source create_dense_2021.tcl -tclargs impl
#   GUI   : set RUN_IMPL 1     <- in the Tcl Console, BEFORE sourcing this file
# ($argv only exists in batch mode, hence the info-exists guard.)
if {![info exists RUN_IMPL]} { set RUN_IMPL 0 }
if {[info exists argv] && [lsearch -exact $argv "impl"] >= 0} { set RUN_IMPL 1 }
set do_impl $RUN_IMPL

if {![file isdirectory $src]} {
    error "ERROR: source tree not found at $src\n       Put this script beside GEMV_Dense_Source/."
}
if {[file exists $proj_dir]} {
    error "ERROR: $proj_dir already exists. Delete it or change \$proj_dir."
}

puts "=== root      : $root"
puts "=== part      : $part"
puts "=== run impl  : $do_impl"

create_project $proj_name $proj_dir -part $part


# ---- design sources --------------------------------------------------------
# 4 dense-specific modules + 6 carried over verbatim from the sparse design
# (the FP wrappers and the FIFO wrappers, byte-identical in both projects).
add_files -norecurse [list \
    "$src/Design/c_block_dense.vhd" \
    "$src/Design/c_core_dense.vhd" \
    "$src/Design/weights_fifo_dense.vhd" \
    "$src/Design/top_module_dense.vhd" \
    "$src/Design/multiplier_wrapper_4.0.vhd" \
    "$src/Design/adder_wrapper_4.0.vhd" \
    "$src/Design/accumulator_wrapper_4.0.vhd" \
    "$src/Design/vector_fifo_4.0.vhd" \
    "$src/Design/vector_fifo_cycle_4.0.vhd" \
    "$src/Design/c_fifo_4.0.vhd" \
]


# ============================================================================
# IP  --  identical block in create_sparse_2021.tcl. Keep them in sync.
# ============================================================================

proc _need_ip {name} {
    if {[llength [get_ips -quiet $name]]} {
        puts "  already present, skipping : $name"
        return 0
    }
    return 1
}

# ---- Floating-Point Operator x3 -------------------------------------------
# bfloat16 = 1 sign + 8 exponent + 7 mantissa. Xilinx counts the fraction width
# INCLUDING the implicit leading bit, so bf16 is exponent 8 / fraction 8 = 16 b.
#
# CRITICAL: Flow_Control = Blocking AND Has_RESULT_TREADY = true on all three.
# Without them m_axis_result_tvalid sticks high and every block emits duplicate
# output beats.
#
# IP version deliberately NOT pinned (-version omitted) so the 2021.1 catalog
# supplies whatever revision it has.
set fp_common [list \
    CONFIG.A_Precision_Type            {Custom} \
    CONFIG.C_A_Exponent_Width          {8} \
    CONFIG.C_A_Fraction_Width          {8} \
    CONFIG.Result_Precision_Type       {Custom} \
    CONFIG.C_Result_Exponent_Width     {8} \
    CONFIG.C_Result_Fraction_Width     {8} \
    CONFIG.Flow_Control                {Blocking} \
    CONFIG.Has_RESULT_TREADY           {true} \
    CONFIG.Has_ARESETn                 {true} \
    CONFIG.Has_ACLKEN                  {false} \
    CONFIG.Maximum_Latency             {true} \
    CONFIG.C_Optimization              {Speed_Optimized} \
    CONFIG.C_Rate                      {1} \
    CONFIG.C_BRAM_Usage                {No_Usage} \
    CONFIG.Axi_Optimize_Goal           {Resources} \
    CONFIG.Has_A_TUSER                 {false} \
    CONFIG.Has_B_TUSER                 {false} \
    CONFIG.Has_C_TUSER                 {false} \
    CONFIG.Has_OPERATION_TUSER         {false} \
    CONFIG.Has_C_TLAST                 {false} \
    CONFIG.Has_OPERATION_TLAST         {false} \
    CONFIG.C_Has_OVERFLOW              {false} \
    CONFIG.C_Has_UNDERFLOW             {false} \
    CONFIG.C_Has_INVALID_OP            {false} \
    CONFIG.C_Has_DIVIDE_BY_ZERO        {false} \
    CONFIG.C_Has_ACCUM_OVERFLOW        {false} \
    CONFIG.C_Has_ACCUM_INPUT_OVERFLOW  {false} \
]

# TLAST propagation is OFF by default in this IP and MUST be enabled -- the
# wrappers use s_axis_b_tlast (mult/add), s_axis_a_tlast (accum) and
# m_axis_result_tlast. Without these the elaborator reports
# "formal port <s_axis_b_tlast> does not exist in entity <Multiplier>".
# RESULT_TLAST_Behv selects which input channel's TLAST is forwarded to the
# result, which is what carries the end-of-row marker down the pipeline.

if {[_need_ip Multiplier]} {
    create_ip -name floating_point -vendor xilinx.com -library ip -module_name Multiplier
    set_property -dict [concat $fp_common [list \
        CONFIG.Operation_Type     {Multiply} \
        CONFIG.C_Mult_Usage       {Max_Usage} \
        CONFIG.Has_A_TLAST        {false} \
        CONFIG.Has_B_TLAST        {true} \
        CONFIG.RESULT_TLAST_Behv  {Pass_B_TLAST} \
    ]] [get_ips Multiplier]
    puts "  created : Multiplier   (bf16 multiply, DSP, B_TLAST -> RESULT)"
}

if {[_need_ip Adder]} {
    create_ip -name floating_point -vendor xilinx.com -library ip -module_name Adder
    set_property -dict [concat $fp_common [list \
        CONFIG.Operation_Type     {Add_Subtract} \
        CONFIG.Add_Sub_Value      {Add} \
        CONFIG.C_Mult_Usage       {No_Usage} \
        CONFIG.Has_A_TLAST        {false} \
        CONFIG.Has_B_TLAST        {true} \
        CONFIG.RESULT_TLAST_Behv  {Pass_B_TLAST} \
    ]] [get_ips Adder]
    puts "  created : Adder        (bf16 add, no DSP, B_TLAST -> RESULT)"
}

# The Accumulator takes TLAST on the A channel (that is its flush trigger) and
# its internal accumulation is FIXED-POINT over the range set by
# C_Accum_Input_Msb / C_Accum_Msb / C_Accum_Lsb. Those three values define
# exactly how much precision is retained before the bf16 truncation at flush --
# i.e. they ARE the "exact internally, truncate at flush" behaviour the Python
# golden model reproduces bit-exactly. The IP defaults (32/32/-31) are NOT what
# the verified design uses; leaving them default changes the numerics.
if {[_need_ip Accumulator]} {
    create_ip -name floating_point -vendor xilinx.com -library ip -module_name Accumulator
    set_property -dict [concat $fp_common [list \
        CONFIG.Operation_Type     {Accumulator} \
        CONFIG.Add_Sub_Value      {Add} \
        CONFIG.C_Mult_Usage       {Full_Usage} \
        CONFIG.Has_A_TLAST        {true} \
        CONFIG.Has_B_TLAST        {false} \
        CONFIG.RESULT_TLAST_Behv  {Pass_A_TLAST} \
        CONFIG.C_Accum_Input_Msb  {15} \
        CONFIG.C_Accum_Msb        {25} \
        CONFIG.C_Accum_Lsb        {-7} \
    ]] [get_ips Accumulator]
    puts "  created : Accumulator  (bf16 accumulate, A_TLAST flush, accum 25/-7)"
}

# ---- AXI4-Stream Data FIFO x3 ---------------------------------------------
# 256-bit TDATA (32 bytes) + TLAST, common clock, non-packet (FIFO_MODE 1),
# no TKEEP/TSTRB. All First-Word-Fall-Through: the barrier joins present their
# head combinationally, so the datapath depends on it.
set axis_common [list \
    CONFIG.TDATA_NUM_BYTES         {32} \
    CONFIG.HAS_TLAST               {1} \
    CONFIG.HAS_TREADY              {1} \
    CONFIG.HAS_TKEEP               {0} \
    CONFIG.HAS_TSTRB               {0} \
    CONFIG.TID_WIDTH               {0} \
    CONFIG.TDEST_WIDTH             {0} \
    CONFIG.TUSER_WIDTH             {0} \
    CONFIG.IS_ACLK_ASYNC           {0} \
    CONFIG.FIFO_MODE               {1} \
    CONFIG.FIFO_MEMORY_TYPE        {auto} \
    CONFIG.HAS_AEMPTY              {0} \
    CONFIG.HAS_AFULL               {0} \
    CONFIG.HAS_PROG_EMPTY          {0} \
    CONFIG.PROG_EMPTY_THRESH       {5} \
    CONFIG.HAS_RD_DATA_COUNT       {0} \
    CONFIG.HAS_WR_DATA_COUNT       {0} \
    CONFIG.SYNCHRONIZATION_STAGES  {3} \
    CONFIG.ACLKEN_CONV_MODE        {0} \
    CONFIG.ENABLE_ECC              {0} \
    CONFIG.HAS_ECC_ERR_INJECT      {0} \
]

# weight/index PCs and activation PCs: depth 512, no prog_full (s_axis_tready
# backpressures the read engine natively).
foreach {ipname depth} {axis_data_fifo_pc 512  axis_data_fifo_v 512} {
    if {[_need_ip $ipname]} {
        create_ip -name axis_data_fifo -vendor xilinx.com -library ip -module_name $ipname
        set_property -dict [concat $axis_common [list \
            CONFIG.FIFO_DEPTH    $depth \
            CONFIG.HAS_PROG_FULL {0} \
        ]] [get_ips $ipname]
        puts "  created : $ipname  (256b x $depth)"
    }
}

# output PCs: depth 256 WITH prog_full at 240 -- the C cores cannot be
# backpressured, so prog_full feeds the top-level global gate.
if {[_need_ip axis_data_fifo_c]} {
    create_ip -name axis_data_fifo -vendor xilinx.com -library ip -module_name axis_data_fifo_c
    set_property -dict [concat $axis_common [list \
        CONFIG.FIFO_DEPTH       {256} \
        CONFIG.HAS_PROG_FULL    {1} \
        CONFIG.PROG_FULL_THRESH {240} \
    ]] [get_ips axis_data_fifo_c]
    puts "  created : axis_data_fifo_c  (256b x 256, prog_full 240)"
}

# ---- FIFO Generator : activation replay / recirculation buffer ------------
# 513 b = 512-bit joined activation beat + 1 tlast bit at bit 0.
# 512 words deep = up to 16384 vector elements (8192 is the comfortable design
# point -- it must never be FULL during recirculation, or the simultaneous
# write-back is dropped and the vector corrupts).
#
# CRITICAL: First_Word_Fall_Through is mandatory. In replay wr_en = rd_en and
# the write-back samples dout on the pop cycle, so the head must already be
# present; standard read mode re-enqueues the wrong word and corrupts the
# vector after one lap.
#
# The IP also exposes a 'full' port; vector_cycle_512's component declaration
# omits it, which is fine -- it is simply left unconnected.
if {[_need_ip fifo_gen_vector_cycle]} {
    create_ip -name fifo_generator -vendor xilinx.com -library ip -module_name fifo_gen_vector_cycle
    set_property -dict [list \
        CONFIG.Fifo_Implementation           {Common_Clock_Builtin_FIFO} \
        CONFIG.Performance_Options           {First_Word_Fall_Through} \
        CONFIG.Input_Data_Width              {513} \
        CONFIG.Input_Depth                   {512} \
        CONFIG.Output_Data_Width             {513} \
        CONFIG.Output_Depth                  {512} \
        CONFIG.Reset_Type                    {Synchronous_Reset} \
        CONFIG.Reset_Pin                     {true} \
        CONFIG.Enable_Reset_Synchronization  {true} \
        CONFIG.Use_Embedded_Registers        {true} \
        CONFIG.Output_Register_Type          {Embedded_Reg} \
        CONFIG.use_dout_register             {false} \
        CONFIG.Use_Dout_Reset                {true} \
        CONFIG.Dout_Reset_Value              {0} \
        CONFIG.Full_Flags_Reset_Value        {0} \
        CONFIG.Programmable_Full_Type        {No_Programmable_Full_Threshold} \
        CONFIG.Programmable_Empty_Type       {No_Programmable_Empty_Threshold} \
        CONFIG.Full_Threshold_Assert_Value   {511} \
        CONFIG.Full_Threshold_Negate_Value   {510} \
        CONFIG.Empty_Threshold_Assert_Value  {4} \
        CONFIG.Empty_Threshold_Negate_Value  {5} \
        CONFIG.Use_Extra_Logic               {false} \
        CONFIG.Enable_Safety_Circuit         {false} \
        CONFIG.Almost_Full_Flag              {false} \
        CONFIG.Almost_Empty_Flag             {false} \
        CONFIG.Valid_Flag                    {false} \
        CONFIG.Underflow_Flag                {false} \
        CONFIG.Overflow_Flag                 {false} \
        CONFIG.Write_Acknowledge_Flag        {false} \
        CONFIG.Data_Count                    {false} \
        CONFIG.Write_Data_Count              {false} \
        CONFIG.Read_Data_Count               {false} \
    ] [get_ips fifo_gen_vector_cycle]
    puts "  created : fifo_gen_vector_cycle  (513b x 512, FWFT)"
}

generate_target {instantiation_template simulation synthesis} [get_ips]
export_ip_user_files -of_objects [get_ips] -no_script -sync -force -quiet

# ============================================================================
# end of shared IP block
# ============================================================================


# ---- simulation + constraints ---------------------------------------------
# NOTE: dense_TB.vhd hard-codes WINDOWS absolute paths for weights.hex /
# activations.hex / output.txt / tlast.txt. Simulation on this server needs
# those constants edited to Linux paths first. Synthesis/implementation are
# unaffected.
add_files -fileset sim_1 -norecurse "$src/Simulation/dense_TB.vhd"
add_files -fileset constrs_1 -norecurse "$src/timing.xdc"

set_property top dense_gemv [get_filesets sources_1]
set_property top dense_TB   [get_filesets sim_1]
set_property top_lib xil_defaultlib [get_filesets sim_1]
set_property -name {xsim.simulate.runtime} -value {-all} -objects [get_filesets sim_1]

# ---- OOC synthesis ---------------------------------------------------------
# Mandatory: dense_gemv has 3628 top-level ports (2048 weights + 512
# activations + 1024 output + sidebands), which no package can bond. These are
# internal AXI-Stream connections to the HLS read/write engine in the real
# system, so building without I/O buffers is also the correct measurement.
set_property -name {STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS} \
             -value {-mode out_of_context} -objects [get_runs synth_1]

update_compile_order -fileset sources_1
update_compile_order -fileset sim_1

puts ""
puts "=== project created : $proj_dir"
puts "=== synth top = dense_gemv   sim top = dense_TB   (OOC)"


# ---- optional: synthesis + implementation + reports -----------------------
if {$do_impl} {
    set rpt "$proj_dir/reports"
    file mkdir $rpt

    puts "=== launching synthesis ..."
    launch_runs synth_1 -jobs $jobs
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
        error "ERROR: synthesis failed -- see $proj_dir/$proj_name.runs/synth_1/"
    }
    open_run synth_1 -name synth_1
    report_utilization -file "$rpt/post_synth_utilization.rpt"
    close_design

    puts "=== launching implementation ..."
    launch_runs impl_1 -jobs $jobs
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "ERROR: implementation failed -- see $proj_dir/$proj_name.runs/impl_1/"
    }
    open_run impl_1
    report_utilization        -file "$rpt/post_impl_utilization.rpt"
    report_timing_summary     -file "$rpt/post_impl_timing.rpt"
    report_utilization -hierarchical -file "$rpt/post_impl_utilization_hier.rpt"

    puts ""
    puts "=== reports written to $rpt"
    puts "    post_impl_utilization.rpt  <- LUT / FF / DSP / BRAM for the thesis table"
    puts "    post_impl_timing.rpt       <- WNS -> Fmax (module-level, OOC)"
}

puts ""
puts "=============================================================="
puts " VERIFY the IP matches the 2022.2 reference before trusting"
puts " any numbers:"
puts "=============================================================="
foreach {ip prop expect} {
    Multiplier        C_Latency          9
    Adder             C_Latency          9
    Accumulator       C_Latency          20
    Multiplier        Has_RESULT_TREADY  true
    Adder             Has_RESULT_TREADY  true
    Accumulator       Has_RESULT_TREADY  true
    Multiplier        Flow_Control       Blocking
    Multiplier        Has_B_TLAST        true
    Adder             Has_B_TLAST        true
    Accumulator       Has_A_TLAST        true
    Multiplier        RESULT_TLAST_Behv  Pass_B_TLAST
    Accumulator       RESULT_TLAST_Behv  Pass_A_TLAST
    Accumulator       C_Accum_Input_Msb  15
    Accumulator       C_Accum_Msb        25
    Accumulator       C_Accum_Lsb        -7
    axis_data_fifo_pc FIFO_DEPTH         512
    axis_data_fifo_c  FIFO_DEPTH         256
    axis_data_fifo_c  PROG_FULL_THRESH   240
} {
    set got [get_property CONFIG.$prop [get_ips $ip]]
    set flag [expr {$got eq $expect ? "ok " : ">>>"}]
    puts [format " %s %-18s %-18s got=%-12s expect=%s" $flag $ip $prop $got $expect]
}
puts ""
puts " A C_Latency mismatch is NOT fatal: Maximum_Latency is true so 2021.1"
puts " picks its own maximum. The datapath propagates valid/tlast as AXI-Stream"
puts " sidebands and is latency-agnostic, the Python golden model is beat-based,"
puts " and the end-of-calc TLAST logic counts flushes rather than assuming a"
puts " drain depth. It only shifts area/timing slightly -- record what you get,"
puts " and make sure the SPARSE project reports the same values."
puts "=============================================================="
