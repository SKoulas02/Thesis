# ----------------------------------------------------------------------------
# 450 MHz kernel clock (2.222 ns period).
# (The old "400Mhz" comment was wrong: 1 / 2.222 ns = 450 MHz. Period unchanged,
#  so results stay comparable to the banked numbers in Utilization.xlsx.)
#
# THE CLOCK PORT NAME DEPENDS ON THE TOP MODULE -- swap the two lines below:
#   dense_gemv_axis  -> ap_clk   (AXIS wrapper; the Vitis RTL Kernel Wizard
#                                 fixes ap_clk / ap_rst_n on the kernel top,
#                                 so this name is required and permanent)
#   dense_gemv       -> clk      (the bare verified engine)
#
# WARNING: constraining a port that does not exist is NOT an error. create_clock
# on an empty object list only raises a critical warning, and the run completes
# reporting WNS = inf with every endpoint unconstrained -- which looks like a
# pass and is not one. If you see WNS = inf, this is the first thing to check.
#
# (No 'if' here on purpose: XDC files reject Tcl control flow --
#  "[Designutils 20-1307] Command 'if' is not supported in the xdc constraint file".)
# ----------------------------------------------------------------------------

# --- top = dense_gemv_axis (current) ---
create_clock -period 2.222 -name ap_clk [get_ports ap_clk]

# --- top = dense_gemv (bare engine) --- uncomment this and comment the line above
#create_clock -period 2.222 -name clk [get_ports clk]
