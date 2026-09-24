# Multi-tenant GEMV: N independent 4x4 engines on one U280

Proposed by the professor, 2026-09-20. Everything for this experiment lives in
this directory; results go to `results/multi_tenant/` and report snapshots to
`reports/multi_tenant_x<N>/`.

## The claim under test

**One card, several unrelated tenants.** Each tenant is a complete 4x4 engine
running its **own matrix, its own sparsity mode and its own vector**, on its own
HBM channels. Nothing is shared but the card, its memory and the kernel clock.

This is not the same question as the family study. There, one engine grew and the
clock fell with it. Here the engine stays small and only the *system* grows, which
separates two explanations that the family series confounds:

| if N tenants close near 400 MHz | the engine's internal activation broadcast was the limiter |
| if they close near 250-300 MHz | the HBM interconnect and mover congestion set the clock |

Either answer is a result, and neither is available from any build we have.

## The arithmetic

A 4x4 tenant needs **6 of the 32 HBM pseudo-channels** (2 weight + 1 index +
2 activation + 1 output). Tenants do **not** share activation channels: independent
matrices mean independent vectors. So the card holds **five tenants**.

| tenants | PCs | mover CUs | engines | total CUs | DSPs | aggregate theory @400 |
|---|---|---|---|---|---|---|
| 2 | 12 | 12 | 2 | 14 | 256 | 25.6 GMAC/s |
| 4 | 24 | 24 | 4 | 28 | 512 | 51.2 GMAC/s |
| 5 | 30 | 30 | 5 | 35 | 640 | 64.0 GMAC/s |

For scale: the biggest single-engine build (4x32) used 32 PCs and 33 CUs at
250 MHz; 8x8 used 17 PCs and 18 CUs at 325. **5 x 4x4 must hold 400 MHz just to
match 4x32's throughput**, which is why step 1 is two tenants, not five.

## What is here

| file | what |
|---|---|
| `make_multi_build.py` | generates `x<N>/`: link config, floorplan (SLR0 default + split fallback) and `BUILD.md` |
| `host_sparse_multi.cpp` | the N-tenant host: per-tenant queue, span, throughput, output and golden |
| `prep_tenants.py` | one stimulus directory per tenant, each a **different** matrix, with per-tenant checks. Two modes: `correctness` (small, with golden) and `measure` (large, equal weight beats, no golden) |
| `run_multi_measure.py` | the card driver: alone-then-cumulative sets, per-tenant interference, optional power soak, CSV |
| `x2/`, `x4/` | the two- and four-tenant builds, generated |

**No RTL changes and no new `.xo`.** The engine is the same
`krnl_gemv_sparse_4x4.xo` that closed at 400 MHz as a single tenant, instantiated
N times by `nk=`.

## Naming contract

Tenant k owns `mm2s_w0_tk mm2s_w1_tk mm2s_i0_tk mm2s_a0_tk mm2s_a1_tk gemv_tk
s2mm_c0_tk` on `HBM[6k .. 6k+5]`. The link config and the host build those names
from the same rule, so a rename breaks the run loudly instead of quietly.

## Why every tenant gets a different matrix

Besides being what multi-tenancy means, it is the only way a **cross-wired
`stream_connect` fails**. With identical stimulus each tenant would read
plausible data from a neighbour's channels and still match golden.
`prep_tenants.py` gives every tenant a different sparsity mode, shape, row count
and seed, then refuses to finish if two tenants ended up with identical images.

## Two stimulus modes, and why

`--mode correctness` uses `gemv4_cosim_gen.py` + `hex_to_bin.py`: small matrices with
seeded random data and a golden per tenant. This is the gate, and the differing data is
what makes a cross-wired `stream_connect` fail.

`--mode measure` uses `gen_timing_stimulus.py`: large matrices, no golden. **Every tenant
gets the SAME number of weight beats**, because the engine consumes one beat per clock in
every sparsity mode, so equal beats means equal duration -- and only tenants that run for
the same time actually overlap. The first draft gave tenants 32 and 128 beats and the
overlap window came out at 46%, which would have understated contention badly. They still
differ in sparsity mode, vector length, row count and lap count.

Two consequences of that generator: its weight payload is a fixed tiled pattern, so
equal-length weight images are byte-identical between tenants (harmless -- the engine is
data-independent), and there is no golden, so the driver skips the compare and says so.

⚠️ **Never point `Vitis/measure_power.py` at this host.** Its regex takes the first
"`<n> Mrow/s sustained`" line, which here is tenant 0's, not the aggregate.
`run_multi_measure.py --soak` parses the aggregate itself and imports `power_scraper.py`.

## Workflow

```bash
python make_multi_build.py --tenants 2      # -> x2/, then follow x2/BUILD.md
```

On the server, in `~/GEMV_Sparse/Vitis_multi_x2/`:

1. `prep_tenants.py` -> `t0/`, `t1/` (stimulus + golden + compare script)
2. hw_emu link, then the host: **every tenant bit-exact against its own golden**
3. `-t hw` link with `impl_family.cfg` (the frozen strategy) at 400 MHz
4. read the **`*postroute_physopted.rpt`**, never `_routed`
5. on the card, correctness stimulus: all tenants together, then each one alone
6. regenerate with `--mode measure` (this REPLACES `t0/ t1/ ...`), then
   `python3 run_multi_measure.py --xclbin <x> --clock <MHz> --tenants N --reps 3`

## What to measure

- **Closure frequency** — the headline, per the table at the top.
- **Interference** = tenant k's span with everyone running / its span alone.
  Measured by comparing runs, not within a run. The host prints the **overlap
  window** (the part where every tenant really was running); a slowdown measured
  over a run with little overlap means nothing.
- **Aggregate throughput** over the union window, against the single-engine
  series at the same channel count (4 tenants = 24 PCs = 4x24's channel budget).
- **Power and energy** with the soak: one thread per tenant, so tenants do not
  wait for each other. Compare energy per row against a single large engine.
- **Area**: N engines + N x 6 movers, and how the platform grows with channels.

## Known risks

- **35 CUs is far beyond anything built here** (18 max so far). The two-tenant
  build is the cheap way to find Vitis 2021.1's practical ceiling.
- **P&R congestion**: Serpens needed AutoBridge to route 24 HBM channels at all.
- **Pseudo-channel pairing**: two PCs share a physical HBM channel, so
  neighbouring tenants are not perfectly isolated. A tenant whose block straddles
  PC 16 spans both HBM stacks (tenant 2 is the first) -- worth a look.
- The kernel clock is **one clock for all tenants**; no per-tenant frequency.

## Shared workload: four tenants, ONE matrix (2026-09-21)

Decided with the user: the family study's MIXED matrix (four equal-row quarters at 2:4,
2:8, 2:16, 2:32 over one vector) is cut so that **tenant k takes quarter k -- one sparsity
mode per tenant** -- and every tenant gets the same vector. It runs on the x4 bitstream,
no rebuild. Sharing the activation (and output) channels was discussed and **skipped for
now**: it cannot change speed (the vector is read once, then replayed on-chip) and only
matters for channel count and idle power.

| file | what |
|---|---|
| `prep_shared.py correctness` | one MIXED matrix + golden (`gemv4_cosim_gen.py --sparsities`), cut into `t0..t3` and `full`; proves the cut byte for byte |
| `prep_shared.py stitch` | compares each tenant with its golden, then the stitched result with the whole matrix's golden |
| `run_shared.py` | the eleven MIXED shapes, four tenants vs one tenant doing the whole matrix, measured like `run_shapes.py`; optional lockstep power soaks |
| `host_sparse_multi --lockstep` | soak the tenants as ONE job: launch all, wait for all |

**It is lopsided by design:** the quarters carry work 8:4:2:1, the 2:4 tenant does 8/15
and sets the finish time, so the expected speedup over one tenant is 15/8 = 1.875x, not
4x. `run_shared.py` launches the 2:4 tenant LAST so it always finishes last (launched
first, the others' launch stagger can decide the finish on a short or busy run).
