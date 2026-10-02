#!/bin/bash
# setup_bcast3_build.sh -- make the x3_bcast build directory (3 x 4x4, broadcast vector) ready,
# and install the current *_bcast workload tools.
#
# A COPY OF setup_bcast_builds.sh (2026-09-29), edited to the one build x3_bcast, whose
# kernel krnl_mm2s_bcast3 is compiled by make_bcast3_xo.sh; setup_bcast_builds.sh is
# untouched. It creates one new directory; the tool update refreshes the four *_bcast files
# in workload_tools (the host now launches from ONE thread -- the fix for the "failed to
# launch execution buffer" hw_emu failure of 2026-09-29), files no other tool reads.
#
# From the repo root (PowerShell, one line), copy everything up to the staging folder -- file by
# file, never a whole folder: NO .md FILE GOES TO THE SERVER (the user's rule, 2026-09-29):
#   ssh skoulas@coroni "mkdir -p ~/GEMV_Sparse/bcast_staging/x3_bcast"; scp multi_tenant/x3_bcast/*.cfg multi_tenant/x3_bcast/*.sh skoulas@coroni:~/GEMV_Sparse/bcast_staging/x3_bcast/; scp multi_tenant/setup_bcast3_build.sh multi_tenant/make_bcast3_xo.sh Vitis/krnl_mm2s_bcast3.cpp Vitis/tb_mm2s_bcast3.cpp workload/plan_workload_bcast.py workload/run_workload_bcast.py workload/host_workload_bcast.cpp workload/run_all_workloads_bcast.sh skoulas@coroni:~/GEMV_Sparse/bcast_staging/
# then on the server:
#   tmux new -d -s bcast3_xo 'bash ~/GEMV_Sparse/bcast_staging/make_bcast3_xo.sh'   # ~10 min
#   bash ~/GEMV_Sparse/bcast_staging/setup_bcast3_build.sh
#
# The text below is setup_bcast_builds.sh's, true of this one build:
# For each build it creates ~/GEMV_Sparse/Vitis_multi_<name>/ with the generated files (link
# config, floorplans, BUILD.md, run_hw_emu.sh, start_build.sh), the mover .xo files and
# impl_family.cfg from Vitis_4x4 (the same files every multi-tenant build used), and the
# broadcast kernel's .xo for its engine count. It checks the engine .xo of each shape and
# refuses to touch a directory whose hardware link has started (_x/ exists).
# It installs into ~/GEMV_Sparse/workload_tools/ the four *_bcast tools (plan, run, host,
# chain) -- files no other tool reads, so nothing that exists changes behaviour.
# Nothing is started: it ends by printing the command of each build's hw_emu gate (step 3).

ROOT=$HOME/GEMV_Sparse
STAGE=$(cd "$(dirname "$0")" && pwd)
SRC=$ROOT/Vitis_4x4
TOOLS=$ROOT/workload_tools
# folder : engines (the N of krnl_mm2s_bcast<N>) : engine shape
BUILDS="x3_bcast:3:4x4"
TOOL_FILES="plan_workload_bcast.py run_workload_bcast.py host_workload_bcast.cpp run_all_workloads_bcast.sh"

bad=0
for f in krnl_mm2s.hw.xo krnl_s2mm.hw.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo impl_family.cfg; do
    [ -f "$SRC/$f" ] || { echo "MISSING $SRC/$f -- nothing done"; exit 1; }
done
for f in $TOOL_FILES; do
    [ -f "$STAGE/$f" ] || { echo "MISSING $STAGE/$f (copy it up with the folders) -- nothing done"; exit 1; }
done
for b in $BUILDS; do
    n=$(echo "$b" | cut -d: -f2)
    for t in hw hw_emu; do
        [ -f "$STAGE/krnl_mm2s_bcast$n.$t.xo" ] || { echo "MISSING $STAGE/krnl_mm2s_bcast$n.$t.xo \
-- run make_bcast3_xo.sh first -- nothing done"; exit 1; }
    done
done
[ -d "$TOOLS" ] || { echo "MISSING $TOOLS (the workload tools folder) -- nothing done"; exit 1; }

echo "workload tools -> $TOOLS"
for f in $TOOL_FILES; do
    if [ -f "$TOOLS/$f" ] && cmp -s "$STAGE/$f" "$TOOLS/$f"; then
        echo "  unchanged  $f"
    else
        [ -f "$TOOLS/$f" ] && st="updated  " || st="installed"
        cp "$STAGE/$f" "$TOOLS/$f" && echo "  $st  $f"
    fi
done
chmod +x "$TOOLS/run_all_workloads_bcast.sh"

echo
printf "%-14s %-6s %-30s %s\n" build shape directory status
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1); n=$(echo "$b" | cut -d: -f2); shape=$(echo "$b" | cut -d: -f3)
    dst=$ROOT/Vitis_multi_$d
    if [ ! -d "$STAGE/$d" ]; then
        printf "%-14s %-6s %-30s %s\n" "$d" "$shape" "-" "NOT UPLOADED (no $STAGE/$d)"; bad=1; continue
    fi
    if [ -d "$dst/_x" ]; then
        printf "%-14s %-6s %-30s %s\n" "$d" "$shape" "Vitis_multi_$d" "SKIPPED: its hardware link has started (_x/ exists)"
        continue
    fi
    mkdir -p "$dst"
    cp "$STAGE/$d"/*.cfg "$STAGE/$d"/*.sh "$dst/"          # never a .md file onto the server
    cp "$SRC/krnl_mm2s.hw.xo" "$SRC/krnl_s2mm.hw.xo" "$SRC/krnl_mm2s.hw_emu.xo" \
       "$SRC/krnl_s2mm.hw_emu.xo" "$SRC/impl_family.cfg" "$dst/"
    cp "$STAGE/krnl_mm2s_bcast$n.hw.xo" "$STAGE/krnl_mm2s_bcast$n.hw_emu.xo" "$dst/"
    chmod +x "$dst/run_hw_emu.sh" "$dst/start_build.sh"
    if [ -f "$ROOT/krnl_gemv_sparse_$shape.xo" ]; then
        status="ready"
    else
        status="NO ENGINE: $ROOT/krnl_gemv_sparse_$shape.xo missing"; bad=1
    fi
    printf "%-14s %-6s %-30s %s\n" "$d" "$shape" "Vitis_multi_$d" "$status"
done

echo
df -h ~ | tail -1 | awk '{print "disk: " $4 " free in /home"}'
free -g | awk '/^Mem:/ {print "RAM: " $7 " GB available (a link peaks near 20 GB)"}'
echo
echo "step 3, the hw_emu gate (own tmux session; links the hw_emu xclbin first, then runs the gate):"
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1)
    echo "  tmux new -d -s ${d}_emu 'bash ~/GEMV_Sparse/Vitis_multi_$d/run_hw_emu.sh'"
done
echo "step 4, ONLY after its gate PASSED (last lines of hw_emu_gate.log in its folder):"
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1)
    echo "  tmux new -d -s $d 'bash ~/GEMV_Sparse/Vitis_multi_$d/start_build.sh'"
done
[ $bad -eq 0 ] || echo "FIX THE LINES ABOVE MARKED NOT UPLOADED / NO ENGINE before using those builds."
