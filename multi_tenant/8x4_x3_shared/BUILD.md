# 3 x 8x4 multi-tenant build, shared vector

3 tenants that share ONE activation vector in HBM[0..1], **26 of 32 HBM channels** (30 with a vector per tenant), 30 mover CUs + 3 engines = **33 compute units**, target **375 MHz**.

Channels: HBM[0] and HBM[1] hold the vector for every tenant; tenant k owns
HBM[2 + 8*k .. 2 + 8*k + 7] (weights, indices, outputs).

Floorplan: **slr_floorplan_8x4_x3_shared_split.cfg** (engines in SLR1, movers in SLR0 beside the HBM). Everything in SLR0 would take ~585 of 672 BRAM (87%).

The single-die variant is generated too, but do not start with it: at 92%
of SLR0's BRAM the 5 x 4x4 build fell to 260 MHz and one tenant hung on the
card, while the split closed 329 MHz with every tenant bit-exact.

Split, SLR0 still holds all 30 movers: ~450 of 672 BRAM (67%).

The engine `.xo` is the one the single-tenant 8x4 build already used and
verified. Nothing in the RTL changes, so nothing in the RTL needs re-verifying.

**What is new here is only the channel binding:** several activation movers
(6) read the same two channels. With more movers than the HBM's 32 ports, v++
puts interconnects in front of the shared channels; that is the part to watch
in timing. Every existing host works unchanged: a buffer takes the HBM bank of
the argument it is first bound to, so each tenant's vector lands in HBM[0..1].

## 1. Copy to the server

Create the build directory, with the mover `.xo` files and `impl_family.cfg`
beside it, exactly like every other `Vitis_<tag>` build directory:

```bash
mkdir -p ~/GEMV_Sparse/Vitis_multi_8x4_x3_shared && cd ~/GEMV_Sparse/Vitis_multi_8x4_x3_shared
cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw.xo .
cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw_emu.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw_emu.xo .
# impl_family.cfg lives in each Vitis_<tag> build dir on the server,
# NOT in ~/GEMV_Sparse/Vitis. Copy it from a family build, or scp the
# repo copy (Vitis/impl_family.cfg) up -- they are the same file.
cp ~/GEMV_Sparse/Vitis_4x4/impl_family.cfg .   # or: find ~/GEMV_Sparse -name impl_family.cfg
ls ../krnl_gemv_sparse_8x4.xo   # the engine; must exist
```

Then, from the repo root on the local machine:

```bash
scp multi_tenant/8x4_x3_shared/*.cfg multi_tenant/host_sparse_multi.cpp \
    multi_tenant/prep_tenants.py multi_tenant/run_multi_measure.py \
    skoulas@coroni.microlab.ntua.gr:/home/skoulas/GEMV_Sparse/Vitis_multi_8x4_x3_shared/
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
cd ~/GEMV_Sparse/Vitis_multi_8x4_x3_shared
python3 prep_tenants.py --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation \
                        --out . --tenants 3 --cores 8 --blocks 4
```

Writes `t0/ .. t2/`, each with `bin/`, its own `golden.txt` and its own copy
of the compare script. **Every tenant gets a different matrix** -- different
shape, seed and sparsity mode -- which is what makes a cross-wired
`stream_connect` fail the compare instead of passing silently.
Each tenant also has its OWN vector, placed in the shared HBM[0..1]: a tenant
that read another tenant's vector from the shared channels would fail its
compare. (One buffer read by every tenant -- MoE -- is the workload host's job.)

## 3. hw_emu (optional, x3_shared only)

Several movers on the same HBM channels have never been linked in this
project. A wrong binding makes the hardware link stop within its first
minutes (connectivity is checked before implementation), so the hardware
builds do not wait for this. If wanted, run hw_emu on 3 x 4x4 shared, the
smallest, IN PARALLEL with the hardware links: it shows every tenant reading
its own vector from HBM[0..1] before any bitstream is finished.

```bash
emconfigutil --platform $PLATFORM --nd 1
export XCL_EMULATION_MODE=hw_emu
v++ -t hw_emu --platform $PLATFORM --config sparse_hbm_8x4_x3_shared.cfg \
    --kernel_frequency 375 -l -o sparse_8x4_x3_shared.hw_emu.xclbin \
    ../krnl_gemv_sparse_8x4.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo
```

Then build the host and run it against the emulated bitstream:

```bash
g++ -Wall -O2 -std=c++1y -I$XILINX_XRT/include host_sparse_multi.cpp \
    -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread -DCORES=8 -DBLOCKS=4 -o host_sparse_multi
./host_sparse_multi sparse_8x4_x3_shared.hw_emu.xclbin 375 0 0:t0 1:t1 2:t2
for k in $(seq 0 2); do (cd t$k && python3 compare_gemv4_py36.py); done
unset XCL_EMULATION_MODE
```

**Gate: every tenant bit-exact against its own golden.** hw_emu timings are
meaningless (simulation-time timestamps); correctness only.
(The host's printed HBM ranges assume a vector per tenant -- ignore them.)

## 4. Link for hardware

One command, which checks everything first and runs the link below in its own
tmux session (several builds can run side by side):

```bash
tmux new -d -s 8x4_x3_shared 'bash ~/GEMV_Sparse/Vitis_multi_8x4_x3_shared/start_build.sh'
tmux attach -t 8x4_x3_shared     # to watch; Ctrl-b d to leave it running
```

By hand instead: in `tmux`, after `df -h ~` (a full `/home` killed a link once):

```bash
v++ -t hw --platform $PLATFORM \
    --config sparse_hbm_8x4_x3_shared.cfg \
    --config impl_family.cfg \
    --config slr_floorplan_8x4_x3_shared_split.cfg \
    --kernel_frequency 375 \
    -l -o sparse_8x4_x3_shared_375.xclbin \
    ../krnl_gemv_sparse_8x4.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo
```

Same frozen strategy as all six family builds -- do not vary it. A miss on
the KERNEL clock is not fatal: v++ writes the xclbin at the highest whole MHz
its own timing proves. **Read DATA_CLK and use THAT as the clock everywhere.**
If it lands far below the target, try `slr_floorplan_8x4_x3_shared.cfg`; never change the
strategy.

**Read the FINAL report, not `_routed`:**

```bash
RS=_x/reports/link/imp/impl_1_*_timing_summary_postroute_physopted.rpt
awk '/Intra Clock Table/,/Inter Clock Table/' $RS | awk 'NF>5 {print $1, $2, $4}' \
  | grep -E 'kernel_0|hbm_aclk_0'
xclbinutil --info --input sparse_8x4_x3_shared_375.xclbin | grep -A2 DATA_CLK
mkdir -p ~/GEMV_Sparse/reports_multi_8x4_x3_shared && cp -r _x/reports/link/imp ~/GEMV_Sparse/reports_multi_8x4_x3_shared/
```

## 5. On the card

```bash
# all tenants together (use the DATA_CLK, not the target, as the clock)
./host_sparse_multi sparse_8x4_x3_shared_375.xclbin <DATA_CLK> 0 0:t0 1:t1 2:t2
for k in $(seq 0 2); do (cd t$k && python3 compare_gemv4_py36.py); done
```

## Pass criteria

- `xclbinutil --info` lists **26** HBM channels bound and **33** CUs
- the link reports **WNS >= 0** at 375 MHz (and note what it closed at)
- **every tenant bit-exact against its own golden**, in hw_emu (x3_shared) and on the card
