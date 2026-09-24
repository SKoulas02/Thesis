# 3 x 8x4 multi-tenant build

3 independent tenants, 30 of 32 HBM channels, 30 mover CUs + 3 engines = **33 compute units**, target **375 MHz**.

Floorplan: **slr_floorplan_8x4_x3_split.cfg** (engines in SLR1, movers in SLR0 beside the HBM). Everything in SLR0 would take ~585 of 672 BRAM (87%).

The single-die variant is generated too, but do not start with it: at 92%
of SLR0's BRAM the 5 x 4x4 build fell to 260 MHz and one tenant hung on the
card, while the split closed 329 MHz with every tenant bit-exact.

The engine `.xo` is the one the single-tenant 4x4 build already used and
verified. Nothing in the RTL changes, so nothing in the RTL needs re-verifying.

## 1. Copy to the server

```bash
# from the repo root
scp multi_tenant/8x4_x3/*.cfg multi_tenant/host_sparse_multi.cpp \
    multi_tenant/prep_tenants.py multi_tenant/run_multi_measure.py \
    skoulas@coroni.microlab.ntua.gr:/home/skoulas/GEMV_Sparse/Vitis_multi_8x4_x3/
```

Create that directory first. It needs the mover `.xo` files copied in beside
it, exactly like every other `Vitis_<tag>` build directory:

```bash
mkdir -p ~/GEMV_Sparse/Vitis_multi_8x4_x3 && cd ~/GEMV_Sparse/Vitis_multi_8x4_x3
cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw.xo .
cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw_emu.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw_emu.xo .
# impl_family.cfg lives in each Vitis_<tag> build dir on the server,
# NOT in ~/GEMV_Sparse/Vitis. Copy it from a family build, or scp the
# repo copy (Vitis/impl_family.cfg) up -- they are the same file.
cp ~/GEMV_Sparse/Vitis_4x4/impl_family.cfg .   # or: find ~/GEMV_Sparse -name impl_family.cfg
```

## 1b. Environment -- EVERY fresh shell

None of this is in `.bashrc`, and `setup_vitis.sh` must be SOURCED, not
executed -- running it in a child shell loses the exports and the next
command fails with a confusing error (an empty `$PLATFORM` makes
`emconfigutil` report that a platform named `--nd` was not found).

```bash
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
export BDF=0000:af:00.1
echo "PLATFORM=[$PLATFORM]"   # must NOT be empty
```

Use `xilinx_u280_xdma_201920_3`. The `gen3x16_xdma_1_202211_1` entry in the
same directory is a 2022.2 platform that Vitis 2021.1 cannot parse, and every
build in this project used 201920_3.

## 2. Stimulus: one directory per tenant

```bash
cd ~/GEMV_Sparse/Vitis_multi_8x4_x3
python3 prep_tenants.py --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation \
                        --out . --tenants 3 --cores 8 --blocks 4
```

Writes `t0/ .. t2/`, each with `bin/`, its own `golden.txt` and its own copy
of the compare script. **Every tenant gets a different matrix** -- different
shape, seed and sparsity mode -- which is what makes a cross-wired
`stream_connect` fail the compare instead of passing silently.

## 3. hw_emu first

This channel topology has never been linked. hw_emu catches a dangling
`stream_connect` in under an hour; in `-t hw` the same mistake costs a whole
build and its only symptom is one tenant hanging.

```bash
emconfigutil --platform $PLATFORM --nd 1
export XCL_EMULATION_MODE=hw_emu
v++ -t hw_emu --platform $PLATFORM --config sparse_hbm_8x4_x3.cfg \
    --kernel_frequency 375 -l -o sparse_8x4_x3.hw_emu.xclbin \
    ../krnl_gemv_sparse_8x4.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo
```

Then build the host and run it against the emulated bitstream:

```bash
g++ -Wall -O2 -std=c++1y -I$XILINX_XRT/include host_sparse_multi.cpp \
    -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -DCORES=8 -DBLOCKS=4 -o host_sparse_multi
./host_sparse_multi sparse_8x4_x3.hw_emu.xclbin 375 0 0:t0 1:t1 2:t2
for k in $(seq 0 2); do (cd t$k && python3 compare_gemv4_py36.py); done
unset XCL_EMULATION_MODE
```

**Gate: every tenant bit-exact against its own golden.** hw_emu timings are
meaningless (simulation-time timestamps); correctness only.

## 4. Link for hardware

In `tmux`. Check `df -h ~` first -- a full `/home` killed a link once.

```bash
v++ -t hw --platform $PLATFORM \
    --config sparse_hbm_8x4_x3.cfg \
    --config impl_family.cfg \
    --config slr_floorplan_8x4_x3_split.cfg \
    --kernel_frequency 375 \
    -l -o sparse_8x4_x3_375.xclbin \
    ../krnl_gemv_sparse_8x4.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo
```

Same frozen strategy as all six family builds -- do not vary it. A miss on
the KERNEL clock is not fatal: v++ writes the xclbin at the highest whole MHz
its own timing proves. **Read DATA_CLK and use THAT as the clock everywhere.**
If it lands far below the target, try `slr_floorplan_8x4_x3.cfg`; never change the
strategy.

**Read the FINAL report, not `_routed`:**

```bash
RS=_x/reports/link/imp/impl_1_*_timing_summary_postroute_physopted.rpt
awk '/Intra Clock Table/,/Inter Clock Table/' $RS | awk 'NF>5 {print $1, $2, $4}' \
  | grep -E 'kernel_0|hbm_aclk_0'
mkdir -p ~/GEMV_Sparse/reports_multi_8x4_x3 && cp -r _x/reports/link/imp ~/GEMV_Sparse/reports_multi_8x4_x3/
```

## 5. On the card

```bash
# all tenants together
./host_sparse_multi sparse_8x4_x3_375.xclbin 375 0 0:t0 1:t1 2:t2
# each tenant ALONE -- the uncontended baseline for interference
./host_sparse_multi sparse_8x4_x3_375.xclbin 375 0 0:t0
./host_sparse_multi sparse_8x4_x3_375.xclbin 375 0 1:t1
./host_sparse_multi sparse_8x4_x3_375.xclbin 375 0 2:t2
```

Interference for tenant k = its span with everyone running / its span alone.
Check the printed **overlap window** first: if the tenants barely overlapped,
the comparison says nothing.

## Pass criteria

- `xclbinutil --info` lists **30** HBM channels bound and **33** CUs
- the link reports **WNS >= 0** at 375 MHz (and note what it closed at)
- **every tenant bit-exact against its own golden**, both in hw_emu and on the card
- the overlap window is a large fraction of the union window
