#!/bin/bash
# setup_shared_builds.sh -- make the three shared-vector build directories ready to link.
#
# From the repo root (PowerShell), copy the six generated folders and the tools up:
#   ssh skoulas@coroni "mkdir -p ~/GEMV_Sparse/shared_staging"; scp -r multi_tenant/x3_shared multi_tenant/8x4_x3_shared multi_tenant/16x3_x2_shared multi_tenant/host_sparse_multi.cpp multi_tenant/prep_tenants.py multi_tenant/run_multi_measure.py multi_tenant/setup_shared_builds.sh skoulas@coroni:~/GEMV_Sparse/shared_staging/
# then on the server:
#   bash ~/GEMV_Sparse/shared_staging/setup_shared_builds.sh
#
# For each build it creates ~/GEMV_Sparse/Vitis_multi_<name>/ with the generated files
# (link config, floorplans, BUILD.md, start_build.sh), the mover .xo files and
# impl_family.cfg from Vitis_4x4 (the same files every multi-tenant build used), and the
# multi-tenant tools. It checks that the engine .xo of each shape exists, and refuses to
# touch a directory whose link has already started (_x/ exists). Nothing is started: it
# ends by printing the tmux command of each build.

ROOT=$HOME/GEMV_Sparse
STAGE=$(cd "$(dirname "$0")" && pwd)
SRC=$ROOT/Vitis_4x4
# (x6_shared, x7_shared and 8x8_x2_shared were dropped: 36, 42 and 34 movers, and the HBM
# subsystem has 32 kernel ports -- x6_shared failed to link on it, 2026-09-28)
BUILDS="x3_shared 8x4_x3_shared 16x3_x2_shared"

bad=0
for f in krnl_mm2s.hw.xo krnl_s2mm.hw.xo impl_family.cfg; do
    [ -f "$SRC/$f" ] || { echo "MISSING $SRC/$f -- nothing done"; exit 1; }
done
for f in host_sparse_multi.cpp prep_tenants.py run_multi_measure.py; do
    [ -f "$STAGE/$f" ] || { echo "MISSING $STAGE/$f (copy it up with the folders) -- nothing done"; exit 1; }
done

printf "%-16s %-8s %-38s %s\n" build shape directory status
for d in $BUILDS; do
    dst=$ROOT/Vitis_multi_$d
    if [ ! -d "$STAGE/$d" ]; then
        printf "%-16s %-8s %-38s %s\n" "$d" "?" "-" "NOT UPLOADED (no $STAGE/$d)"; bad=1; continue
    fi
    cfg=$(ls "$STAGE/$d"/sparse_hbm_*_shared.cfg 2>/dev/null | head -1)
    shape=$(basename "$cfg" | sed -E 's/^sparse_hbm_(.+)_x[0-9]+_shared\.cfg$/\1/')
    if [ -d "$dst/_x" ]; then
        printf "%-16s %-8s %-38s %s\n" "$d" "$shape" "Vitis_multi_$d" "SKIPPED: its link has started (_x/ exists)"
        continue
    fi
    mkdir -p "$dst"
    cp "$STAGE/$d"/* "$dst/"
    cp "$SRC/krnl_mm2s.hw.xo" "$SRC/krnl_s2mm.hw.xo" "$SRC/impl_family.cfg" "$dst/"
    cp "$SRC"/krnl_mm2s.hw_emu.xo "$SRC"/krnl_s2mm.hw_emu.xo "$dst/" 2>/dev/null
    cp "$STAGE/host_sparse_multi.cpp" "$STAGE/prep_tenants.py" "$STAGE/run_multi_measure.py" "$dst/"
    if [ -f "$ROOT/krnl_gemv_sparse_$shape.xo" ]; then
        status="ready"
    else
        status="NO ENGINE: $ROOT/krnl_gemv_sparse_$shape.xo missing"; bad=1
    fi
    printf "%-16s %-8s %-38s %s\n" "$d" "$shape" "Vitis_multi_$d" "$status"
done

echo
df -h ~ | tail -1 | awk '{print "disk: " $4 " free in /home"}'
free -g | awk '/^Mem:/ {print "RAM: " $7 " GB available (a link peaks near 20 GB)"}'
echo
echo "start a build (each in its own tmux session; several can run side by side):"
for d in $BUILDS; do
    echo "  tmux new -d -s $d 'bash ~/GEMV_Sparse/Vitis_multi_$d/start_build.sh'"
done
[ $bad -eq 0 ] || echo "FIX THE LINES ABOVE MARKED NOT UPLOADED / NO ENGINE before starting those builds."
