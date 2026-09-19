# ----------------------------------------------------------------------------
# s7_wizard_probe.tcl  --  S7 smoke test: can the Vivado 2021.1 RTL Kernel
#                          Wizard express our free-running compute kernel?
#
# Answers, in one run, WITHOUT building anything:
#   Q1  Is the RTL Kernel Wizard in the 2021.1 catalog, and at what VLNV?
#   Q2  Every CONFIG.* property name + default  (drives gen_xo_dense.tcl, S8)
#   Q3  Does ap_ctrl_none work with ZERO m_axi and ZERO scalar args?
#       -> CONFIRMED on the 2021.1 reference; this should pass
#   Q4  How many AXI4-Stream interfaces does it accept?  (need 14 dense /
#       17 sparse; the reference only ever proves 2; spec limit is 32)
#   Q4b Do AXISnn_MODE / AXISnn_WIDTH exist, and how is direction decided?
#   Q5  The generated port list -- above all, does each stream carry TKEEP?
#
# Nothing is synthesised. Runtime: 1-3 minutes.
#
# GUI USAGE (preferred) -- in the Vivado Tcl Console, with NO project open:
#   source /home/<user>/s7_wizard_probe.tcl
#
# This script is GUI-SAFE: it never calls exit, so it cannot close Vivado.
# It leaves the probe project OPEN so you can inspect the wizard by hand
# afterwards (Q4b and Q5 are much clearer in the dialog than in a log).
#
# Output: s7_out/s7_report.txt, written next to this script.
# ----------------------------------------------------------------------------

set part    "xcu280-fsvh2892-2L-e"
set mn      "krnl_gemv_probe"
set root    [file normalize [file dirname [info script]]]
set outdir  "$root/s7_out"

file delete -force $outdir
file mkdir $outdir

set rpt [open "$outdir/s7_report.txt" w]

proc say {msg} {
    global rpt
    puts $msg
    puts $rpt $msg
    flush $rpt
}
proc hdr {msg} {
    say ""
    say "============================================================"
    say "== $msg"
    say "============================================================"
}
# abort cleanly without killing the tool
proc fatal {msg} {
    say "FATAL: $msg"
    return -code error $msg
}

# set a property; report OK / CLAMPed / FAILed / ABSENT, never abort
proc try_set {prop val} {
    global ip cfg_props
    if {[lsearch -exact $cfg_props $prop] < 0} {
        say [format "  ABSENT %-34s (property does not exist in 2021.1)" $prop]
        return 0
    }
    if {[catch {set_property $prop $val $ip} err]} {
        say [format "  FAIL   %-34s -> %-12s  %s" $prop $val $err]
        return 0
    }
    set now [get_property $prop $ip]
    if {$now eq $val} {
        say [format "  OK     %-34s  = %s" $prop $now]
        return 1
    }
    say [format "  CLAMP  %-34s  asked %-8s got %s" $prop $val $now]
    return 0
}

proc find_prop {candidates} {
    global cfg_props
    foreach c $candidates {
        if {[lsearch -exact $cfg_props $c] >= 0} { return $c }
    }
    return ""
}

# ---------------------------------------------------------------- the probe
proc s7_probe {} {
    global part mn root outdir ip cfg_props

    create_project -force ${mn}_probe "$outdir/proj" -part $part

    # -------------------------------------------------------------- Q1
    hdr "Q1  RTL Kernel Wizard in the catalog?"

    set defs [get_ipdefs -all *rtl_kernel*]
    if {[llength $defs] == 0} {
        fatal "no rtl_kernel_wizard IP def in the 2021.1 catalog -> S7 FAILS.\
               Fall back to the hand-written kernel.xml route (PLAN 3.3)."
    }
    foreach d $defs { say "  found: $d" }

    # the 2021.1 reference (rtl_streaming_free_running_k2k/src/gen_xo.tcl) pins -version 1.0
    if {[catch {create_ip -name rtl_kernel_wizard -vendor xilinx.com -library ip \
                          -version 1.0 -module_name $mn -dir "$outdir/ip"} err]} {
        say "  -version 1.0 failed ($err); retrying without a version pin"
        if {[catch {create_ip -name rtl_kernel_wizard -vendor xilinx.com -library ip \
                              -module_name $mn -dir "$outdir/ip"} err2]} {
            fatal "create_ip failed: $err2"
        }
    }
    set ip [get_ips $mn]
    say "  created IP: $mn"

    # -------------------------------------------------------------- Q2
    hdr "Q2  ALL CONFIG.* properties with their DEFAULT values"
    say "(ground truth for gen_xo_dense.tcl -- S8)"
    say ""

    set cfg_props {}
    foreach p [lsort [list_property $ip]] {
        if {[string match "CONFIG.*" $p]} {
            lappend cfg_props $p
            if {[catch {get_property $p $ip} v]} { set v "<unreadable>" }
            say [format "  %-42s = %s" $p $v]
        }
    }
    say ""
    say "  total CONFIG.* properties: [llength $cfg_props]"

    set p_ctrl [find_prop {CONFIG.KERNEL_CTRL CONFIG.CTRL_PROTOCOL CONFIG.KERNEL_CTRL_PROTOCOL}]
    set p_axis [find_prop {CONFIG.NUM_AXIS CONFIG.NUM_AXI_STREAMS CONFIG.NUM_STREAMS CONFIG.NUM_AXIS_INTF}]
    set p_maxi [find_prop {CONFIG.NUM_M_AXI CONFIG.NUM_AXI_MASTERS CONFIG.NUM_MASTERS}]
    set p_args [find_prop {CONFIG.NUM_INPUT_ARGS CONFIG.NUM_SCALAR_ARGS CONFIG.NUM_ARGS}]

    say ""
    say "  control-protocol property : [expr {$p_ctrl eq "" ? "NOT FOUND" : $p_ctrl}]"
    say "  stream-count property     : [expr {$p_axis eq "" ? "NOT FOUND" : $p_axis}]"
    say "  m_axi-count property      : [expr {$p_maxi eq "" ? "NOT FOUND" : $p_maxi}]"
    say "  scalar-arg-count property : [expr {$p_args eq "" ? "NOT FOUND" : $p_args}]"

    # -------------------------------------------------------------- Q3
    hdr "Q3  ap_ctrl_none with ZERO m_axi and ZERO scalar args?"
    say "(the exact profile of two2N / dense_gemv: clk, resetn, streams only)"
    say "CONFIRMED on the 2021.1 reference -- this should PASS. If it does not,"
    say "the install is wrong, not the approach."
    say ""

    try_set CONFIG.KERNEL_NAME $mn

    # two passes: some versions only allow ap_ctrl_none once the memory ports are
    # gone, others only allow 0 m_axi once ap_ctrl_none is set.
    for {set pass 1} {$pass <= 2} {incr pass} {
        say "  -- pass $pass --"
        if {$p_maxi ne ""} { try_set $p_maxi 0 }
        if {$p_args ne ""} { try_set $p_args 0 }
        if {$p_ctrl ne ""} { try_set $p_ctrl "ap_ctrl_none" }
    }

    say ""
    if {$p_ctrl ne ""} {
        set got [get_property $p_ctrl $ip]
        if {$got eq "ap_ctrl_none"} {
            say "  VERDICT Q3: PASS -- free-running kernel is expressible"
        } else {
            say "  VERDICT Q3: FAIL -- control protocol stuck at '$got'"
            say "              -> use the PLAN 3.3 hand-written kernel.xml fallback"
        }
    }

    # -------------------------------------------------------------- Q4
    hdr "Q4  How many AXI4-Stream interfaces will it accept?"
    say "(need 14 dense / 17 sparse; the reference proves only 2; spec limit 32)"
    say ""

    if {$p_axis eq ""} {
        say "  CANNOT PROBE: no stream-count property found."
        say "  -> check the Q2 dump for anything stream-shaped and tell me."
        return
    }

    set max_ok 0
    foreach n {2 10 14 17 32 33} {
        if {[catch {set_property $p_axis $n $ip} err]} {
            say [format "  %-3s streams: FAIL   (%s)" $n $err]
        } else {
            set now [get_property $p_axis $ip]
            if {$now == $n} {
                say [format "  %-3s streams: OK" $n]
                if {$n > $max_ok} { set max_ok $n }
            } else {
                say [format "  %-3s streams: CLAMPED to %s" $n $now]
            }
        }
    }
    say ""
    say "  highest accepted stream count: $max_ok"
    if {$max_ok >= 17} {
        say "  VERDICT Q4: PASS -- both dense (14) and sparse (17) fit"
    } elseif {$max_ok >= 14} {
        say "  VERDICT Q4: PARTIAL -- dense fits, SPARSE DOES NOT."
        say "              sparse would need the PLAN 3.3 fallback."
    } else {
        say "  VERDICT Q4: FAIL -- neither fits; 3.3 fallback for both."
    }

    # -------------------------------------------------------------- Q4b
    catch {set_property $p_axis 14 $ip}
    hdr "Q4b  per-stream properties after setting 14 streams"
    say "(need the naming scheme -- AXIS00_NAME? MODE? WIDTH? -- for S8)"
    say ""
    foreach p [lsort [list_property $ip]] {
        if {[string match "CONFIG.*AXIS*" $p] || [string match "CONFIG.*STREAM*" $p]} {
            if {[catch {get_property $p $ip} v]} { set v "<unreadable>" }
            say [format "  %-42s = %s" $p $v]
        }
    }

    # -------------------------------------------------------------- Q5
    hdr "Q5  Generated top-level port list"
    say "(what dense_gemv_axis.vhd must present -- note especially whether each"
    say " stream carries TKEEP, which our RTL does not have today)"
    say ""

    if {[catch {generate_target {instantiation_template} [get_files ${mn}.xci]} err]} {
        say "  generate_target failed: $err"
        return
    }
    set veo [glob -nocomplain "$outdir/ip/$mn/*.veo" "$outdir/ip/$mn/*.vho"]
    if {[llength $veo] == 0} {
        say "  no .veo/.vho template produced; generated files were:"
        foreach f [glob -nocomplain "$outdir/ip/$mn/*"] { say "    [file tail $f]" }
        return
    }
    set fh [open [lindex $veo 0] r]
    foreach line [split [read $fh] "\n"] { say "  $line" }
    close $fh
}

# ---------------------------------------------------------------------- run
if {[catch {s7_probe} err]} {
    say ""
    say "PROBE ABORTED: $err"
} else {
    hdr "S7 COMPLETE"
}
say ""
say "Report written to: $outdir/s7_report.txt"
say "The probe project is left OPEN -- inspect the wizard by hand:"
say "  Flow Navigator > IP Catalog > search 'RTL Kernel' > double-click"
say "  (or right-click $mn under Sources > Design Sources > Re-customize IP)"
close $rpt
