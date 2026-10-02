#!/bin/bash
# setup_bcast_8x8_wl.sh -- make the 2x8x8_bcast RELINK ready: multi_tenant/8x8_x2_bcast_wl/
# (2 x 8x8, broadcast vector, --layout writes-last: all 8 output ports on HBM[24..31]).
#
# A COPY OF setup_bcast_twins.sh (2026-10-01), edited to this one build; that script is untouched.
# WHY: the first 2x8x8_bcast reads back wrong from the host (bits 16/17/134/219 of every even 32-byte
# beat, all 32 banks; hbm_dma_test). The relink copies the HBM layout of the single 4x32, the other
# 32-port build, which reads back fine. Same movers, same broadcast kernel (krnl_mm2s_bcast2, copied
# from Vitis_multi_8x8_x2_bcast and checked byte for byte), same floorplan, same 325 MHz target.
#
# From the repo root (PowerShell, ONE line) -- file by file, NO .md FILE GOES TO THE SERVER:
#   ssh skoulas@coroni.microlab.ntua.gr "mkdir -p ~/GEMV_Sparse/bcast_staging/8x8_x2_bcast_wl"; scp multi_tenant/8x8_x2_bcast_wl/*.cfg multi_tenant/8x8_x2_bcast_wl/*.sh skoulas@coroni.microlab.ntua.gr:~/GEMV_Sparse/bcast_staging/8x8_x2_bcast_wl/; scp multi_tenant/setup_bcast_8x8_wl.sh skoulas@coroni.microlab.ntua.gr:~/GEMV_Sparse/bcast_staging/; scp workload/plan_workload_bcast.py skoulas@coroni.microlab.ntua.gr:~/GEMV_Sparse/workload_tools/
# then on the server:
#   bash ~/GEMV_Sparse/bcast_staging/setup_bcast_8x8_wl.sh
#
# The text below is setup_bcast_twins.sh's, true of this one build: it creates
# ~/GEMV_Sparse/Vitis_multi_<name>/ with the generated files, the mover .xo files and
# impl_family.cfg from Vitis_4x4 and the broadcast kernel's .xo. Nothing is started.

ROOT=$HOME/GEMV_Sparse
STAGE=$(cd "$(dirname "$0")" && pwd)
SRC=$ROOT/Vitis_4x4
TOOLS=$ROOT/workload_tools
EMU_SRC=$ROOT/GEMV_4.0_Source/Emulation
# folder : engines (the N of krnl_mm2s_bcast<N>) : engine shape : workload name : the build
# whose folder holds the verified krnl_mm2s_bcast<N> .xo
BUILDS="8x8_x2_bcast_wl:2:8x8:2x8x8_bcast:8x8_x2_bcast"

for f in krnl_mm2s.hw.xo krnl_s2mm.hw.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo impl_family.cfg; do
    [ -f "$SRC/$f" ] || { echo "MISSING $SRC/$f -- nothing done"; exit 1; }
done
for f in run_workload_bcast.py host_workload_bcast.cpp plan_workload_bcast.py; do
    [ -f "$TOOLS/$f" ] || { echo "MISSING $TOOLS/$f -- nothing done"; exit 1; }
done
[ -f "$EMU_SRC/gemv4_cosim_gen.py" ] || { echo "MISSING $EMU_SRC/gemv4_cosim_gen.py -- nothing done"; exit 1; }
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1); n=$(echo "$b" | cut -d: -f2); shape=$(echo "$b" | cut -d: -f3)
    arch=$(echo "$b" | cut -d: -f4); from=$(echo "$b" | cut -d: -f5)
    [ -d "$STAGE/$d" ] || { echo "NOT UPLOADED: no $STAGE/$d (see the scp line at the top) -- nothing done"; exit 1; }
    for f in "$STAGE/$d/sparse_hbm_$d.cfg" "$STAGE/$d/run_hw_emu.sh" "$STAGE/$d/start_build.sh"; do
        [ -f "$f" ] || { echo "MISSING $f (upload it) -- nothing done"; exit 1; }
    done
    for t in hw hw_emu; do
        [ -f "$ROOT/Vitis_multi_$from/krnl_mm2s_bcast$n.$t.xo" ] || { echo "MISSING \
$ROOT/Vitis_multi_$from/krnl_mm2s_bcast$n.$t.xo -- nothing done"; exit 1; }
    done
    [ -f "$ROOT/krnl_gemv_sparse_$shape.xo" ] || { echo "MISSING $ROOT/krnl_gemv_sparse_$shape.xo \
(the $shape engine) -- nothing done"; exit 1; }
    grep -q "\"Vitis_multi_$d\"" "$TOOLS/plan_workload_bcast.py" || { echo "$TOOLS/plan_workload_bcast.py \
does not point $arch at Vitis_multi_$d -- upload the new one (scp line at the top) -- nothing done"; exit 1; }
    if pgrep -u "$USER" -f -- "sparse_${d}[._]|Vitis_multi_${d}/" >/dev/null; then
        echo "$d: its hw_emu gate or hardware link is RUNNING -- nothing done (see: tmux ls)"; exit 1
    fi
    if [ -d "$ROOT/Vitis_multi_$d/_x" ]; then
        echo "$d: its hardware link has started (_x/ exists) -- nothing done"; exit 1
    fi
done

printf "%-14s %-6s %-26s %s\n" build shape directory "broadcast kernel (copied from)"
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1); n=$(echo "$b" | cut -d: -f2); shape=$(echo "$b" | cut -d: -f3)
    from=$(echo "$b" | cut -d: -f5)
    dst=$ROOT/Vitis_multi_$d
    mkdir -p "$dst"
    cp "$STAGE/$d"/*.cfg "$STAGE/$d"/*.sh "$dst/"          # never a .md file onto the server
    cp "$SRC/krnl_mm2s.hw.xo" "$SRC/krnl_s2mm.hw.xo" "$SRC/krnl_mm2s.hw_emu.xo" \
       "$SRC/krnl_s2mm.hw_emu.xo" "$SRC/impl_family.cfg" "$dst/"
    for t in hw hw_emu; do
        cp "$ROOT/Vitis_multi_$from/krnl_mm2s_bcast$n.$t.xo" "$dst/"
        cmp -s "$ROOT/Vitis_multi_$from/krnl_mm2s_bcast$n.$t.xo" "$dst/krnl_mm2s_bcast$n.$t.xo" \
            || { echo "$d: the copy of krnl_mm2s_bcast$n.$t.xo differs from its source -- stop"; exit 1; }
    done
    chmod +x "$dst/run_hw_emu.sh" "$dst/start_build.sh"
    printf "%-14s %-6s %-26s %s\n" "$d" "$shape" "Vitis_multi_$d" \
        "krnl_mm2s_bcast$n (Vitis_multi_$from, identical)"
done

echo
df -h ~ | tail -1 | awk '{print "disk: " $4 " free in /home"}'
free -g | awk '/^Mem:/ {print "RAM: " $7 " GB available (a hardware link peaks near 20 GB)"}'
echo
echo "step 3, the hw_emu gates (own tmux sessions; each links its hw_emu xclbin first, then runs"
echo "the gate; watch with tail -f <folder>/hw_emu_gate.log):"
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1)
    echo "  tmux new -d -s ${d}_emu 'bash ~/GEMV_Sparse/Vitis_multi_$d/run_hw_emu.sh'"
done
echo "step 4, ONLY after its gate PASSED (last lines of hw_emu_gate.log in its folder):"
for b in $BUILDS; do
    d=$(echo "$b" | cut -d: -f1)
    echo "  tmux new -d -s $d 'bash ~/GEMV_Sparse/Vitis_multi_$d/start_build.sh'"
done
