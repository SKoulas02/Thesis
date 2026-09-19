# ----------------------------------------------------------------------------
# gen_xo_sparse.tcl  --  S23: package the 2:M SPARSE GEMV engine as a Vitis
#                             RTL kernel
#
#   krnl_gemv_sparse.xo  (ap_ctrl_none, free-running, 17 AXI4-Stream ports)
#
# Structurally identical to gen_xo_dense.tcl -- same six phases, same IP block.
# That last point is not incidental: create_sparse_2021.tcl and
# create_dense_2021.tcl instantiate the same 7 cores with the same settings,
# which is what makes the sparse-vs-dense area comparison mean anything. Keep
# all three in sync.
#
# Deltas from the dense script: 17 streams instead of 14 (the 3 index PCs),
# two2N_axis instead of dense_gemv_axis, and 12 design files instead of 11.
#
# FLOW (INTEGRATION_PLAN.md 0.2c / 3.2, following the 2021.1 reference example
# rtl_streaming_free_running_k2k/src/gen_xo.tcl):
#   1. RTL Kernel Wizard IP   -> AXIS-conformant kernel shell + kernel.xml
#   2. Open IP Example Design -> a normal Vivado project we can put real RTL in
#   3. Swap the placeholder   -> our krnl_gemv_sparse.v drives two2N_axis
#   4. Add design sources+IP  -> the verified engine, IP regenerated natively
#   5. Re-package the core    -> so component.xml lists the files we added
#   6. package_xo             -> the .xo
#
# GUI USAGE (preferred) -- Vivado Tcl Console, with NO project open:
#   source /home/skoulas/gen_xo_sparse.tcl
#
# To re-run only some phases (e.g. after fixing one by hand), set this first:
#   set ::GEN_XO_PHASES {5 6}
#
# GUI-SAFE: never calls exit, so it cannot close Vivado.
#
# EXPECT ONE ITERATION. Phases 5-6 (folding a multi-file, IP-containing design
# into a wizard-generated core) are the least mechanical part of this flow and
# the part I could not test. If it fails there, the fault is in ipx:: file
# handling, not in the RTL -- send me the log.
# ----------------------------------------------------------------------------

# ---- configuration ---------------------------------------------------------
set part   "xcu280-fsvh2892-2L-e"
set kname  "krnl_gemv_sparse"

# server layout; edit if the tree moves.
# NOTE the asymmetry, confirmed 2026-08-25: the sparse project ROOT is
# GEMV_Sparse (NOT GEMV_4.0, which is the Vivado project dir inside it), but the
# source folder under it IS GEMV_4.0_Source. Dense is GEMV_Dense/GEMV_Dense_Source.
set repo   "/home/skoulas/GEMV_Sparse"
set src    "$repo/GEMV_4.0_Source"

# Build dir and output path are OVERRIDABLE so a second, experimental .xo can be
# produced without clobbering the one an in-flight v++ link is using. Set these
# BEFORE sourcing this script:
#
#   set ::GEN_XO_TAG _fix        -> xo_build_fix/ and krnl_gemv_sparse_fix.xo
#
# Two concurrent builds MUST NOT share a directory: v++ and this script both use
# fixed subdirectories, and two writers in one tree corrupt each other silently.
if {![info exists ::GEN_XO_TAG]} { set ::GEN_XO_TAG "" }
set build  "$repo/xo_build$::GEN_XO_TAG"
set xo_out "$repo/${kname}$::GEN_XO_TAG.xo"

if {![info exists ::GEN_XO_PHASES]} { set ::GEN_XO_PHASES {1 2 3 4 5 6} }
proc phase {n} { return [expr {[lsearch -exact $::GEN_XO_PHASES $n] >= 0}] }
proc say {m} { puts "gen_xo: $m" }

if {![file isdirectory $src]} {
    say "ERROR: source tree not found at $src -- edit the repo variable above."
    return
}

# ---- pre-flight: every file this script needs, checked up front ------------
# Cheaper to fail here than three phases in. krnl_gemv_sparse.v and
# two2N_axis.vhd are the two most likely to be missing -- they are new.
set need_files {
    Design/krnl_gemv_sparse.v
    Design/two2N_axis.vhd
    Design/top_module_2N.vhd
    Design/c_core_4.0.vhd
    Design/c_block_4.0.vhd
    Design/weights_fifo_4.0_2k.vhd
    Design/multiplier_wrapper_4.0.vhd
    Design/adder_wrapper_4.0.vhd
    Design/accumulator_wrapper_4.0.vhd
    Design/vector_fifo_4.0.vhd
    Design/vector_fifo_cycle_4.0.vhd
    Design/c_fifo_4.0.vhd
}
set missing {}
foreach f $need_files {
    if {![file exists "$src/$f"]} { lappend missing $f }
}
if {[llength $missing]} {
    say "ERROR: [llength $missing] required file(s) missing under $src :"
    foreach f $missing { say "         $f" }
    say "       copy them from the repo and re-run."
    return
}
say "pre-flight ok: all [llength $need_files] source files present"
if {$::GEN_XO_TAG ne ""} { say "  TAG    = $::GEN_XO_TAG  (separate build dir + .xo name)" }
say "  repo   = $repo"
say "  src    = $src"
say "  build  = $build"
say "  xo_out = $xo_out"

# ============================================================================
# PHASE 1 -- RTL Kernel Wizard
# ============================================================================
if {[phase 1]} {
    say "phase 1: RTL Kernel Wizard"
    file delete -force $build
    # create_ip -dir does NOT create the directory in 2021.1 -- it errors with
    # "<path> does not exist". Make both up front.
    file mkdir $build
    file mkdir "$build/ip"
    say "  build dir ready: $build/ip"

    create_project -force ${kname}_pkg "$build/pkg" -part $part

    create_ip -name rtl_kernel_wizard -vendor xilinx.com -library ip -version 1.0 \
              -module_name $kname -dir "$build/ip"

    # Every AXISnn_MODE is set EXPLICITLY on purpose.
    # The wizard default alternates write_only (even index) / read_only (odd),
    # and Vivado echoes only properties that DIFFER from default -- so a Tcl
    # captured from the GUI legitimately omits MODE on rows where the default
    # already matched. Relying on that would silently flip stream directions
    # the moment NUM_AXIS changes, e.g. the 17-stream sparse build.
    set cfg [list \
        CONFIG.KERNEL_NAME    $kname \
        CONFIG.KERNEL_CTRL    {ap_ctrl_none} \
        CONFIG.NUM_CLOCKS     {1} \
        CONFIG.NUM_RESETS     {1} \
        CONFIG.NUM_INPUT_ARGS {0} \
        CONFIG.NUM_M_AXI      {0} \
        CONFIG.NUM_AXIS       {17} \
    ]

    # index -> {interface_name mode}.  32 bytes = 256 bits = one HBM pseudo-channel.
    # 13 slaves (8 weight + 3 index + 2 activation) then 4 masters.
    # Order must match the wizard's AXISnn slots and the names must match
    # two2N_axis's port prefixes exactly -- the same strings key the
    # stream_connect lines in sparse_hbm.cfg.
    set streams {
        00 {s_axis_w0   read_only}   01 {s_axis_w1   read_only}
        02 {s_axis_w2   read_only}   03 {s_axis_w3   read_only}
        04 {s_axis_w4   read_only}   05 {s_axis_w5   read_only}
        06 {s_axis_w6   read_only}   07 {s_axis_w7   read_only}
        08 {s_axis_ind0 read_only}   09 {s_axis_ind1 read_only}
        10 {s_axis_ind2 read_only}   11 {s_axis_a0   read_only}
        12 {s_axis_a1   read_only}   13 {m_axis_c0   write_only}
        14 {m_axis_c1   write_only}  15 {m_axis_c2   write_only}
        16 {m_axis_c3   write_only}
    }
    foreach {idx spec} $streams {
        lassign $spec nm md
        lappend cfg CONFIG.AXIS${idx}_NAME      $nm
        lappend cfg CONFIG.AXIS${idx}_MODE      $md
        lappend cfg CONFIG.AXIS${idx}_NUM_BYTES {32}
    }

    set_property -dict $cfg [get_ips $kname]
    say "  configured: ap_ctrl_none, 0 m_axi, 0 args, 17 x 256-bit AXIS, reset on"

    generate_target {instantiation_template} [get_files ${kname}.xci]
    generate_target all                      [get_files ${kname}.xci]
}

# ============================================================================
# PHASE 2 -- example project
# ============================================================================
if {[phase 2]} {
    say "phase 2: open IP example design"
    open_example_project -force -in_process -dir $build [get_ips $kname]
    say "  example project now current: [current_project]"
}

# ============================================================================
# PHASE 3 -- swap the placeholder for the real top
# ============================================================================
if {[phase 3]} {
    say "phase 3: replace the example logic"

    # Drop the placeholder hierarchy (vadd chain, adder, counter, generator).
    set dead [get_files -quiet -filter {NAME =~ "*_example*"}]
    if {[llength $dead]} {
        say "  removing [llength $dead] placeholder file(s)"
        remove_files $dead
        foreach f $dead { file delete -force $f }
    }

    # Replace the generated top with ours. Same module name, parameters and
    # ports -- only the body differs (see the header of krnl_gemv_sparse.v),
    # which is exactly what the generated comments invite.
    set gen_top [get_files -quiet "${kname}.v"]
    if {[llength $gen_top]} {
        set dst [lindex $gen_top 0]
        say "  overwriting generated top: $dst"
        remove_files $gen_top
        file copy -force "$src/Design/${kname}.v" $dst
        add_files -norecurse $dst
    } else {
        say "  WARNING: generated ${kname}.v not found; adding ours directly"
        add_files -norecurse "$src/Design/${kname}.v"
    }
}

# ============================================================================
# PHASE 4 -- the verified engine and its IP
# ============================================================================
if {[phase 4]} {
    say "phase 4: add design sources and IP"

    add_files -norecurse [list \
        "$src/Design/two2N_axis.vhd" \
        "$src/Design/top_module_2N.vhd" \
        "$src/Design/c_core_4.0.vhd" \
        "$src/Design/c_block_4.0.vhd" \
        "$src/Design/weights_fifo_4.0_2k.vhd" \
        "$src/Design/multiplier_wrapper_4.0.vhd" \
        "$src/Design/adder_wrapper_4.0.vhd" \
        "$src/Design/accumulator_wrapper_4.0.vhd" \
        "$src/Design/vector_fifo_4.0.vhd" \
        "$src/Design/vector_fifo_cycle_4.0.vhd" \
        "$src/Design/c_fifo_4.0.vhd" \
    ]

    # ---- IP: byte-identical to create_sparse_2021.tcl. KEEP THE TWO IN SYNC.
    # Non-default settings that silently change behaviour if wrong:
    #   Flow_Control Blocking + Has_RESULT_TREADY -> else duplicate output beats
    #   TLAST propagation                         -> else elaboration fails
    #   Accum 15 / 25 / -7                        -> IS the bf16 numerics
    proc _need_ip {name} {
        if {[llength [get_ips -quiet $name]]} { return 0 }
        return 1
    }

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

    if {[_need_ip Multiplier]} {
        create_ip -name floating_point -vendor xilinx.com -library ip -module_name Multiplier
        set_property -dict [concat $fp_common [list \
            CONFIG.Operation_Type     {Multiply} \
            CONFIG.C_Mult_Usage       {Max_Usage} \
            CONFIG.Has_A_TLAST        {false} \
            CONFIG.Has_B_TLAST        {true} \
            CONFIG.RESULT_TLAST_Behv  {Pass_B_TLAST} \
        ]] [get_ips Multiplier]
        say "  created Multiplier"
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
        say "  created Adder"
    }
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
        say "  created Accumulator (accum 15/25/-7)"
    }

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

    foreach {ipname depth} {axis_data_fifo_pc 512  axis_data_fifo_v 512} {
        if {[_need_ip $ipname]} {
            create_ip -name axis_data_fifo -vendor xilinx.com -library ip -module_name $ipname
            set_property -dict [concat $axis_common [list \
                CONFIG.FIFO_DEPTH    $depth \
                CONFIG.HAS_PROG_FULL {0} \
            ]] [get_ips $ipname]
            say "  created $ipname"
        }
    }
    if {[_need_ip axis_data_fifo_c]} {
        create_ip -name axis_data_fifo -vendor xilinx.com -library ip -module_name axis_data_fifo_c
        set_property -dict [concat $axis_common [list \
            CONFIG.FIFO_DEPTH       {256} \
            CONFIG.HAS_PROG_FULL    {1} \
            CONFIG.PROG_FULL_THRESH {240} \
        ]] [get_ips axis_data_fifo_c]
        say "  created axis_data_fifo_c (prog_full 240)"
    }
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
        say "  created fifo_gen_vector_cycle (513b x 512, FWFT)"
    }

    generate_target {instantiation_template synthesis} [get_ips]
    export_ip_user_files -of_objects [get_ips] -no_script -sync -force -quiet

    set_property top $kname [get_filesets sources_1]
    update_compile_order -fileset sources_1
    say "  top = [get_property top [get_filesets sources_1]]"
}

# ============================================================================
# PHASE 5 -- fold the added files into the packaged core
# ============================================================================
# The wizard component.xml lists only the files the wizard generated.
# Everything added in phases 3-4 must be merged in, or package_xo produces a
# .xo whose IP is still the empty shell -- which links happily and computes
# nothing.

# Recursive search -- where open_example_project puts the packaged core varies
# with how create_ip was called (-dir), so locate component.xml rather than
# assuming a layout.
proc find_files {dir name} {
    set out {}
    foreach f [glob -nocomplain -directory $dir *] {
        if {[file isdirectory $f]} {
            set out [concat $out [find_files $f $name]]
        } elseif {[string equal [file tail $f] $name]} {
            lappend out $f
        }
    }
    return $out
}

if {[phase 5]} {
    say "phase 5: package the core using the wizard's own package_kernel.tcl"

    set ex_dir  "$build/${kname}_ex"
    set pkg_dir "$ex_dir/$kname"
    set pk      "$ex_dir/imports/package_kernel.tcl"

    if {![file exists $pk]} {
        say "ERROR: $pk not found -- phase 2 did not produce an example project."
        return
    }

    # ipx::package_project packages the CURRENT project, so the example project
    # must be the open one. Packaging the wrong project silently produces a .xo
    # containing the wrong design.
    if {[current_project] ne "${kname}_ex"} {
        say "ERROR: current project is '[current_project]', expected '${kname}_ex'."
        say "       Open the example project first, or re-run from phase 1."
        return
    }

    # Defines: edit_core, package_project, package_project_dcp[_and_xdc], and
    # sets kernel_name / kernel_vendor / kernel_library at its top.
    source -notrace $pk

    # package_project does the whole job:
    #   ipx::package_project -import_files   -> pulls in the 12 VHDL files and
    #                                           the 7 IP cores we added
    #   edit_core                            -> re-applies all 17 AXIS interface
    #                                           definitions, clock/reset assoc,
    #                                           and the ap_ctrl_none model
    #   check_integrity -kernel / -xrt       -> validates against XRT's rules
    #                                           BEFORE v++ ever sees the .xo
    say "  packaging into: $pkg_dir"
    package_project $pkg_dir $kernel_vendor $kernel_library $kernel_name

    if {![file exists "$pkg_dir/component.xml"]} {
        say "ERROR: package_project ran but produced no component.xml"
        return
    }
    set ::GEN_XO_CORE_DIR $pkg_dir
    say "  core packaged: $pkg_dir/component.xml"
}

# ============================================================================
# PHASE 6 -- package_xo
# ============================================================================
if {[phase 6]} {
    say "phase 6: package_xo"
    if {[info exists ::GEN_XO_CORE_DIR]} {
        set core_dir $::GEN_XO_CORE_DIR
    } else {
        set core_dir "$build/${kname}_ex/$kname"
    }
    set kxml "$build/${kname}_ex/imports/kernel.xml"
    if {![file isdirectory $core_dir] || ![file exists "$core_dir/component.xml"]} {
        say "ERROR: no packaged core at $core_dir -- run phase 5 first."
        return
    }
    say "  packaging from: $core_dir"
    file delete -force $xo_out
    # kernel.xml was generated by the wizard for this exact kernel (17 stream
    # ports, ap_ctrl_none, no s_axi_control) -- use it rather than letting
    # package_xo re-derive one.
    package_xo -force -xo_path $xo_out -kernel_name $kname \
               -ip_directory $core_dir -kernel_xml $kxml
    if {[file exists $xo_out]} {
        say "SUCCESS: $xo_out  ([file size $xo_out] bytes)"
        say "Next: VERIFY BY LISTING THE ARCHIVE, NOT BY SIZE:"
        say "        unzip -l $xo_out"
        say "      A correct .xo here is only ~130 KB (dense was 117,517 bytes and"
        say "      ran bit-exact on hardware) -- size proves nothing. What matters"
        say "      is that the VHDL and the 7 .xci are inside; if only the wizard"
        say "      shell is there, it links happily and computes nothing."
        say "      Also check: no interface-inference or unassociated-port warnings"
        say "      above -- those are quiet now and only bite at link time."
    } else {
        say "FAILED: no .xo produced -- send me the log above."
    }
}

say "done. phases run: $::GEN_XO_PHASES"
