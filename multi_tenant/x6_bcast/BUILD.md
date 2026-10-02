# 6 x 4x4 multi-tenant build, BROADCAST vector

6 engines fed ONE activation vector by ONE broadcast mover pair: **26 of 32 HBM channels**, **26 of the 32 HBM kernel ports** (a mover pair per engine would need 36), 26 mover CUs + 6 engines = **32 compute units**, target **375 MHz**. MoE workload only.

Channels: HBM[0] and HBM[1] hold the vector, one copy, read only by
`mm2s_bcast_a0` / `mm2s_bcast_a1`; engine k owns HBM[2 + 4*k .. 2 + 4*k + 3]
(weights, indices, outputs).

Floorplan: **slr_floorplan_4x4_x6_bcast_split.cfg** (engines in SLR1, movers in SLR0 beside the HBM). Everything in SLR0 would take ~580 of 672 BRAM (86%); split, SLR0 holds ~394 (59%).

**What is new, and only this:** the kernel `krnl_mm2s_bcast6` (`Vitis/krnl_mm2s_bcast.cpp`: one m_axi, 6 AXIS outputs) and its 12 `stream_connect` lines. The engine `.xo`
(`krnl_gemv_sparse_4x4.xo`), `krnl_mm2s` and `krnl_s2mm` are the ones every build used.
The host is `workload/host_workload_bcast.cpp`, the gate and the measurement
`workload/run_workload_bcast.py --arch 6x4x4_bcast` (MoE only: with one vector for every
engine, the independent-tenant test of the other builds cannot run here).

## 1. Set up (once, for all three broadcast builds)

The broadcast kernel `.xo` files come first -- `make_bcast_xo.sh` runs the C
simulation and the six `v++ -c` compiles; then `setup_bcast_builds.sh` creates
this folder with everything in it and installs the `*_bcast` workload tools
(`*_bcast` files only; nothing else is touched). Both are in
`~/GEMV_Sparse/bcast_staging/`; the scp line that puts them there is at the top of
`multi_tenant/setup_bcast_builds.sh`.

```bash
tmux new -d -s bcast_xo 'bash ~/GEMV_Sparse/bcast_staging/make_bcast_xo.sh'
# ~30 min; then:
bash ~/GEMV_Sparse/bcast_staging/setup_bcast_builds.sh
```

## 1b. Environment -- EVERY fresh shell

```bash
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
export BDF=0000:af:00.1
echo "PLATFORM=[$PLATFORM]"   # must NOT be empty
```

## 2. Stimulus

Nothing to prepare: the gate generates its own (per MoE test layer ONE tall
matrix with ONE vector and its golden, cut into one stack per engine and proven
byte for byte), exactly as on every other build.

## 3. hw_emu first

The broadcast kernel and this topology have never been linked. hw_emu shows every
engine computing from the ONE broadcast vector before a bitstream is built; a
dangling broadcast `stream_connect` would hang it instead. One command (own tmux
session; the link ~30-60 min, the run up to a few hours):

```bash
tmux new -d -s x6_bcast_emu 'bash ~/GEMV_Sparse/Vitis_multi_x6_bcast/run_hw_emu.sh'
tail -f ~/GEMV_Sparse/Vitis_multi_x6_bcast/hw_emu_gate.log   # to watch; Ctrl-C stops only tail
```

Nothing typed in the tmux window can kill the run (it ignores Ctrl-C and hang-up and
reads nothing from the terminal), but watching the log with `tail -f` is the safe habit.

It links `sparse_4x4_x6_bcast.hw_emu.xclbin`, builds `host_workload_bcast`, and runs the
MoE gate against it with a PRIVATE copy of the Emulation scripts (`emu_hw_emu/`),
so it can run beside anything else:

```bash
python3 ~/GEMV_Sparse/workload_tools/run_workload_bcast.py --arch 6x4x4_bcast --correctness \
    --emu emu_hw_emu --xclbin sparse_4x4_x6_bcast.hw_emu.xclbin --clock 375 --host-timeout 14400
```

**Gate: `CORRECTNESS PASSED` -- every engine bit-exact on all 3 layers** (the last
lines of `hw_emu_gate.log`). hw_emu timings are meaningless; correctness only.

## 4. Link for hardware

After the hw_emu gate passed. One command, which checks everything first and runs
the link in its own tmux session (several builds can run side by side):

```bash
tmux new -d -s x6_bcast 'bash ~/GEMV_Sparse/Vitis_multi_x6_bcast/start_build.sh'
tmux attach -t x6_bcast     # to watch; Ctrl-b d to leave it running
```

By hand instead: in `tmux`, after `df -h ~` (a full `/home` killed a link once):

```bash
v++ -t hw --platform $PLATFORM \
    --config sparse_hbm_4x4_x6_bcast.cfg \
    --config impl_family.cfg \
    --config slr_floorplan_4x4_x6_bcast_split.cfg \
    --kernel_frequency 375 \
    -l -o sparse_4x4_x6_bcast_375.xclbin \
    ../krnl_gemv_sparse_4x4.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo krnl_mm2s_bcast6.hw.xo
```

Same frozen strategy as all six family builds -- do not vary it. A miss on
the KERNEL clock is not fatal: v++ writes the xclbin at the highest whole MHz
its own timing proves. **Read DATA_CLK and use THAT as the clock everywhere.**
If it lands far below the target, try `slr_floorplan_4x4_x6_bcast.cfg`; never change the
strategy.

**Read the FINAL report, not `_routed`:**

```bash
RS=_x/reports/link/imp/impl_1_*_timing_summary_postroute_physopted.rpt
awk '/Intra Clock Table/,/Inter Clock Table/' $RS | awk 'NF>5 {print $1, $2, $4}' \
  | grep -E 'kernel_0|hbm_aclk_0'
xclbinutil --info --input sparse_4x4_x6_bcast_375.xclbin | grep -A2 DATA_CLK
mkdir -p ~/GEMV_Sparse/reports_multi_x6_bcast && cp -r _x/reports/link/imp ~/GEMV_Sparse/reports_multi_x6_bcast/
```

## 5. On the card

Record the DATA_CLK in `BCAST_ARCHS` of `workload/plan_workload_bcast.py` (the
`None` of `6x4x4_bcast`), copy that file up to `~/GEMV_Sparse/workload_tools/`, then:

```bash
bash ~/GEMV_Sparse/workload_tools/run_all_workloads_bcast.sh --gate-only 6x4x4_bcast
```

The measurement (`run_all_workloads_bcast.sh 6x4x4_bcast`, no `--gate-only`) belongs in the
isolated window with the others.

## Pass criteria

- `xclbinutil --info` lists **26** HBM channels bound and **32** CUs
- 30 minutes into the link, `check_30min.txt`: **0** stream/connect warnings
- the link reports **WNS >= 0** at 375 MHz (and note what it closed at)
- **every engine bit-exact on all 3 MoE layers**, in hw_emu and on the card
