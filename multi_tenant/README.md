# Multi-tenant GEMV: several independent engines on one U280

N copies of one family engine (`family_builds/`) in a single bitstream. Each copy, a
*tenant*, has its own HBM pseudo-channels, its own data movers, its own matrix, sparsity
mode and activation vector, and its own command queue on the host. Nothing is shared except
the card, its HBM and the kernel clock. There are no RTL changes: the family engine's `.xo`
is instantiated N times with `nk=`.

Raw measurements are in `results/multi_tenant/`, the chart-ready tables are built by
`scripts/analysis/make_multi_tenant_csv.py`, and the Vivado reports of all nine bitstreams
are in `reports/reports_multi_tenant.tar.gz`.

## The question

In the family study one engine grows and its clock falls, from 400 MHz (4x4, 6 channels) to
250 MHz (4x32, 32 channels). Two causes are mixed there: the engine gets bigger, and the
system around it (channels, data movers, routing) gets bigger. Several small engines keep the
engine small while the system grows, which separates the two.

**Answer: the clock follows the size of the largest engine, not the number of channels**,
until the die next to the HBM runs out of block RAM. Four 4x4 engines on 24 channels run at
374 MHz where one 4x24 engine on the same 24 channels runs at 300. In the two builds whose
failing paths were examined (2 × 4x4 at 400 MHz, and 5 × 4x4 on one die), every failing path
was in the HLS data movers and none in an engine.

## The builds

Nine bitstreams, all with the link strategy of `Vitis/impl_family.cfg`. Timing is from the
final (post-route phys-opt) report. When the kernel clock misses its target, `v++` still
writes an `.xclbin`, with the clock lowered to min(target, ⌊1000 / (period − WNS)⌋); the
"Runs at" column is that clock, read from each `.xclbin`, and it matches the formula for all
nine.

| Folder | Tenants | HBM channels | Compute units | Floorplan | Target (MHz) | Kernel WNS (ns) | Runs at (MHz) | Status |
|---|---|---|---|---|---|---|---|---|
| `x2/` | 2 × 4x4 | 12 | 14 | single die | 400 | −0.037 | 394 | measured |
| `x2/` | 2 × 4x4 | 12 | 14 | single die | 375 | +0.022 | 375 | first measurement, superseded by the 394 MHz bitstream |
| `x3/` | 3 × 4x4 | 18 | 21 | single die | 400 | −0.006 | 399 | measured |
| `x4/` | 4 × 4x4 | 24 | 28 | single die | 375 | 0.000 | 374 | measured |
| `x5/` | 5 × 4x4 | 30 | 35 | single die | 375 | −1.179 | 260 | not used: tenant 0 hangs on the card |
| `x5/` | 5 × 4x4 | 30 | 35 | split | 375 | −0.366 | 329 | measured |
| `8x4_x2/` | 2 × 8x4 | 20 | 22 | single die | 375 | −0.102 | 361 | measured |
| `8x4_x3/` | 3 × 8x4 | 30 | 33 | split | 375 | −0.151 | 354 | measured |
| `16x3_x2/` | 2 × 16x3 | 26 | 28 | split | 375 | −0.262 | 341 | measured |

*Single die* puts every compute unit in SLR0, next to the HBM; *split* moves the engines to
SLR1 and keeps the movers in SLR0. Compute units are one mover per channel plus one engine
per tenant.

- **The five-tenant single-die build ran out of block RAM.** SLR0 was 91.8% full. The ten
  worst paths were all in tenant 4's movers, and the tool had to lower the clock to 260 MHz.
  On the card, tenant 0 hung even when run alone, while tenants 1–4 were bit-exact. The same
  design split across two dies (68.8% of SLR0's block RAM) runs at 329 MHz with all five
  tenants bit-exact.
- **4 × 4x4 was asked for 375 MHz** and closed with no slack, so its 374 MHz is a floor, not
  its limit. The 2- and 3-tenant 4x4 builds were asked for 400 and reached 394 and 399.

## Findings

- **Correctness.** In every build used for the results, every tenant is bit-exact against
  its own golden output in three tests: all tenants launched together; after 30 s of back-to-back
  runs in parallel (tens of thousands of calculations per tenant); and each tenant's matrix
  run while all the others stream at full rate (the "probe under load", which proves the
  tenants really were running at the same time). All four sparsity modes have run at once on
  one card.
- **No measurable interference.** A tenant's run time with its neighbours running is within
  1.5% of its run time alone (0.988–1.015), with the tenants overlapping for 83–97% of the
  run. The only systematic effect is on tenant 0, which the host always launches first: it
  is always slightly slower than alone (+0.15% to +1.45%), while the other tenants scatter
  both ways (−1.2% to +0.65%).
- **Throughput scales within 2% of linear:** 1.97–1.98× with two tenants, 2.94–2.95× with
  three, 3.96× with four, 4.91× with five.
- **Energy per MAC falls with every tenant added**, because the board's idle power is shared.
  The dynamic part stays between 0.14 and 0.20 nJ per MAC across all 35 power soaks.
- **Idle power follows the HBM channels in use**, not the number of tenants. It steps up by
  about 3 W once a build reaches channel 16, where the second HBM stack begins; the 3 × 4x4
  build (channels 0–17) idles like 8x8 (channels 0–16), not like 16x3 (channels 0–12).
- **Scale-out against scale-up.** At every equal multiplier count, the multi-tenant build is
  5–17% faster than the single engine, with an energy per MAC that is lower or within 1%,
  because smaller engines clock higher. The cost is channels: every engine needs its own two
  activation channels. 3 × 8x4 is the fastest build on the card, ahead of the largest single
  engine (4x32). The full table is in the top-level [README](../README.md#results-at-a-glance).

## Shared workload: four tenants, one matrix

A second experiment on the 4 × 4x4 bitstream. The family study's MIXED matrix (four row
quarters at 2:4, 2:8, 2:16 and 2:32, one activation vector) is split so that tenant k
computes quarter k, and the four outputs are stitched back into one result.

- **Correct:** each tenant is bit-exact, and the stitched result matches the golden output of
  the whole matrix, bit for bit.
- **Lopsided by design:** the quarters carry work in the ratio 8:4:2:1, so the 2:4 tenant sets
  the finish time. The predicted speedup over one tenant doing the whole matrix is 1.88×
  (15/8 plus the per-pass bubbles); the measured speedup is 1.83× (1.81–1.90 over eleven
  matrix shapes).
- **Energy per matrix element:** 399 pJ with four tenants, against 700 pJ for one tenant on
  the same card (−43%).
- The run was taken while another build loaded the server, which biased the launch-overhead
  fit: the absolute latencies read 3–6% high. The ratios above hold; the absolute latencies
  are not charted. Data: `results/multi_tenant/shared_x4_374MHz_during_x5_build.csv`.

## Shared-vector builds (MoE layout, generated 2026-09-28, not built yet)

For the professor's mixture-of-experts case, the activation vector is stored **once**, in
HBM channels 0 and 1, and every tenant's two activation movers read those two channels. The
tenants follow, packed. A build then needs *engines × (weight + index + output channels) + 2*
instead of *engines × (… + 2)*. Nothing else changes: the same `.xo` files, the same CU
names, and each tenant keeps its own activation movers, so the movers' block RAM does not
shrink.

| Folder | Build | Channels | Instead of | Movers | Floorplan | Target | Why |
|---|---|---|---|---|---|---|---|
| `x3_shared/` | 3 × 4x4 | 14 | 18 | 18 | single die | 400 | twin of `x3/`; entirely inside the first HBM stack |
| `8x4_x3_shared/` | 3 × 8x4 | 26 | 30 | 30 | split | 375 | twin of `8x4_x3/` |
| `16x3_x2_shared/` | 2 × 16x3 | 24 | 26 | 26 | split (as its twin) | 375 | twin of `16x3_x2/` |

**Sharing frees channels, but not HBM ports.** The HBM subsystem has 33 connections; the
platform takes one, so at most **32 movers**, because every mover has its own AXI port and
v++ does not share a port between movers that read the same channel. So 6 × 4x4 (36 movers),
7 × 4x4 (42) and 2 × 8x8 (34) fit the 32 channels with a shared vector but cannot be linked.
6 × 4x4 was tried and stopped in `create_bd` with "You have run out of port connections on
/hmss_0. All 33 connections are used". `make_multi_build.py` now refuses any build above 32
movers.

Each folder has a `start_build.sh` (one command, checks first, several builds can run side
by side) and a `BUILD.md`. `setup_shared_builds.sh` turns the uploaded folders into ready
build directories on the server. The existing hosts work unchanged on these
bitstreams: a buffer takes the HBM bank of the argument it is first bound to, so each
tenant's vector lands in channels 0–1 by itself.

## What is here

| File(s) | Purpose |
|---|---|
| `make_multi_build.py` | generates one build folder, `x<N>/` for 4x4 or `<shape>_x<N>/` otherwise: link configuration, both floorplans, a `BUILD.md` and a `start_build.sh`; `--shared-vector` makes the `_shared` variant (one vector in HBM[0..1] for every tenant) |
| `x3_shared/`, `8x4_x3_shared/`, `16x3_x2_shared/`, `setup_shared_builds.sh` | the three shared-vector build folders, and the script that sets them up on the server |
| `x2/` … `x5/`, `8x4_x2/`, `8x4_x3/`, `16x3_x2/` | the generated build folders; `x3/start_build.sh` starts that link with one command |
| `host_sparse_multi.cpp` | OpenCL host for N tenants: one out-of-order command queue per tenant, per-tenant timing, a power soak with one thread per tenant, `--lockstep` for the shared workload. The engine shape is set at compile time, e.g. `-DCORES=8 -DBLOCKS=4` |
| `prep_tenants.py` | stimulus for every tenant, each a different matrix: `--mode correctness` (small, with golden outputs) or `--mode measure` (large, equal weight beats); `--all-sparsity 11` puts every tenant at 2:32 |
| `run_multi_measure.py` | runs each tenant alone, then tenants 0–1, 0–2, … together; records interference, overlap and throughput; with `--soak S --soak-each`, a power soak at every number of running tenants |
| `run_card_tests.sh` | every card test for one bitstream, in order, stopping at the first failure |
| `prep_shared.py`, `run_shared.py` | the shared-workload experiment: build and split the matrix, stitch and compare, measure eleven matrix shapes |

## How to build and test

1. **Generate** a build folder, e.g. three tenants of 8x4:

   ```bash
   python multi_tenant/make_multi_build.py --tenants 3 --config 8x4 --clock 375
   ```

   `--config` is any family shape. The floorplan is chosen automatically: the engines move to
   SLR1 once one die would need more than 85% of SLR0's block RAM. `--floorplan split` or
   `single` overrides it, and the generated files record which one was used and why.
2. **Link** as described in the generated `BUILD.md`. It needs the family engine's `.xo`, the
   mover `.xo` files and `Vitis/impl_family.cfg`. After the link, read the final timing report
   and the `.xclbin`'s `DATA_CLK`.
3. **Test on the card.** From the build folder, with `Vitis/power_scraper.py` in it and this
   folder's tools copied to one place on the server:

   ```bash
   bash <tools folder>/run_card_tests.sh sparse_8x4_x3_375.xclbin 8 4 3
   ```

   The arguments are the `.xclbin`, the engine's cores and blocks, and the number of tenants.
   The script sources XRT and refuses to start if the card's power cannot be read. Then it
   reads the runtime clock from the `.xclbin`, builds the host for the shape, sets the
   packing constants of `hex_to_bin.py`, and runs: correctness with all tenants together,
   30 s of endurance, the measurement stimulus, the probe under load and the mixed
   measurement, then the stimulus, probe and measurement again with every tenant at 2:32.
   It writes
   `multi_[<shape>_]x<N>_<MHz>MHz.csv` and `..._all2to32.csv` and stops at the first failure.
   `--no-timing` skips the report summary; `--mixed-only` repeats only the mixed measurement.
4. **Chart data:** copy the CSVs to `results/multi_tenant/`, then run
   `scripts/analysis/make_multi_tenant_csv.py` and `scripts/analysis/make_chart_xlsx.py`.
   A new build is added to the tables by listing it in `MULTI_BUILDS` at the top of
   `make_multi_tenant_csv.py`.

## Design choices worth knowing

- **Naming contract.** Tenant k owns `mm2s_w<i>_t<k>`, `mm2s_i<i>_t<k>`, `mm2s_a<i>_t<k>`,
  `gemv_t<k>` and `s2mm_c<i>_t<k>` on consecutive HBM channels starting at k × (channels per
  tenant). The generator and the host derive the names from the same rule, so a mismatch
  makes the host fail to find a compute unit instead of computing a wrong answer.
- **Every tenant gets a different matrix.** Besides being what multi-tenancy means, it is the
  only way a cross-wired `stream_connect` shows up: with identical stimulus, a tenant reading
  a neighbour's channels would still match its golden output. `prep_tenants.py` refuses to
  finish if two tenants would get the same matrix.
- **Equal weight beats in measure mode.** The engine consumes one weight beat per clock in
  every sparsity mode, so equal beats means equal run time, and only tenants that run for
  the same time overlap. The tenants still differ in sparsity mode, vector length and row
  count.
- **Interference is a ratio of runs,** a tenant's run time with neighbours over its run time
  alone, measured on the *active* span: from the last input mover's start to the end, which
  leaves out most of the host's launch stagger.
- **One thread per tenant in the power soak,** so no tenant waits for another.
  `--lockstep` does the opposite on purpose, for the shared workload, where waiting for the
  slowest quarter is part of the job.
- **Power is required.** `run_multi_measure.py` stops if the card's telemetry cannot be read
  (`xbutil` must be on the `PATH`), unless `--allow-no-power` is given. Never use
  `Vitis/measure_power.py` with this host: it reads the first tenant's throughput line as if
  it were the whole card's.
- **`hex_to_bin.py` holds per-shape constants** and is shared by every build.
  `prep_tenants.py` checks them and prints the `sed` lines to fix them; `run_card_tests.sh`
  sets them.

## Limits

- One kernel clock for all tenants; a tenant cannot be clocked on its own.
- The host launches compute units at about 60,000 per second in total. With very small
  matrices this, not the engines, sets each tenant's calculation rate; with the measurement
  stimulus (about 6 ms per calculation) it does not matter.
- The hang of tenant 0 on the five-tenant single-die bitstream was not investigated further:
  the split bitstream replaced it.
- Two pseudo-channels share one physical HBM channel, so neighbouring tenants are not fully
  isolated in the memory. No effect was measurable, but it was not studied separately.
