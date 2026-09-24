"""Build one stimulus directory per TENANT, each with a DIFFERENT matrix.

    # correctness (small, with golden) -- the hw_emu and bit-exactness gate
    python3 prep_tenants.py --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation --out . --tenants 2

    # measurement (large, no golden) -- the interference and throughput runs
    python3 prep_tenants.py --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation --out . --tenants 4 \
                            --mode measure

Writes  <out>/t0/{bin/*.bin [, golden.txt, compare_gemv4_py36.py]}
        <out>/t1/...
which is what host_sparse_multi.cpp expects: it reads <dir>/bin/*.bin and writes
<dir>/output.txt. In correctness mode the compare script is copied in beside the
golden, because it resolves golden.txt next to ITSELF.

WHY EVERY TENANT GETS A DIFFERENT MATRIX. Multi-tenancy means unrelated jobs, so
the tenants differ in shape, sparsity mode and data. It is also the only way a
cross-wired stream_connect FAILS: with identical stimulus every tenant would read
plausible numbers from a neighbour's channels and still match golden.

TWO MODES, AND THE REASON THE SECOND ONE EXISTS
===============================================
correctness  gemv4_cosim_gen.py + hex_to_bin.py -> bin/ + golden.txt.
             Small (tens of beats), because the Python golden model walks every
             MAC. This is the gate: every tenant bit-exact against its OWN golden.

measure      gen_timing_stimulus.py -> bin/ directly, NO golden.
             ⚠️ EVERY TENANT GETS THE SAME NUMBER OF WEIGHT BEATS. The engine
             consumes one weight beat per clock in every sparsity mode, so equal
             beats means equal DURATION -- and tenants that run for the same time
             actually overlap. The first draft gave tenants 32 and 128 beats and
             the overlap window came out at 46%: the short tenant finished while
             the long one was still starting, so any interference ratio measured
             from it would have understated contention badly.
             They still differ in sparsity mode, vector length, row count and
             data, so they remain independent jobs.

Both generators write into the shared Emulation directory, and BOTH ARE SHARED
MUTABLE STATE. This script runs them strictly one tenant at a time, copies the
result out immediately, and leaves that directory holding the LAST tenant's data.
Regenerate before any other measurement.

hex_to_bin.py (correctness mode only) has NO command line -- its PC counts are
constants at the top. This script refuses to run unless they match the shape being
generated and prints the sed line to fix them. It never edits a verified script itself.

ANY ENGINE SHAPE: --cores/--blocks, default 4x4. An 8x4 tenant carries 4 weight +
2 index + 2 activation + 2 output channels and its sparsity code at index PC1 byte 8.
Everything downstream -- the images copied, the beat checks, the sparsity probe --
follows from the shape, so only that flag changes.
"""

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys

CORES, BLOCKS = 4, 4                        # --cores/--blocks override; see configure()
LANES = CORES * BLOCKS
W_PCS, IND_PCS, A_PCS, C_PCS = 2, 1, 2, 1
PC_BYTES = 32
IND_BITS = 10 * LANES                       # 160
SP_PC, SP_BYTE = IND_BITS // 256, (IND_BITS % 256) // 8      # 0, 20


def configure(cores, blocks):
    """Set every width for one engine shape, as the family generator derives them.

    4x4 -> 2+1+2+1 PCs, sparsity at index PC0 byte 20; 8x4 -> 4+2+2+2, PC1 byte 8.
    prep_shared.py reads these as 4x4 constants and never calls this.
    """
    global CORES, BLOCKS, LANES, W_PCS, IND_PCS, A_PCS, C_PCS, IND_BITS, SP_PC, SP_BYTE
    CORES, BLOCKS = cores, blocks
    LANES = CORES * BLOCKS
    W_PCS = -(-32 * LANES // 256)
    IND_PCS = -(-(10 * LANES + 2) // 256)
    A_PCS = 2
    C_PCS = -(-16 * LANES // 256)
    IND_BITS = 10 * LANES
    SP_PC, SP_BYTE = IND_BITS // 256, (IND_BITS % 256) // 8
SP_NAME = {"00": "2:4", "01": "2:8", "10": "2:16", "11": "2:32"}
FREEZE = {"00": 8, "01": 4, "10": 2, "11": 1}

# correctness mode: tenant -> (sparsity code, nwin, nlaps, seed). All different.
PLAN = [
    ("10", 4, 4, 111),      # 2:16, V=128,  64 rows
    ("00", 8, 2, 222),      # 2:4,  V=256,  32 rows
    ("11", 2, 8, 333),      # 2:32, V=64,  128 rows
    ("01", 6, 3, 444),      # 2:8,  V=192,  48 rows
    ("10", 3, 6, 555),      # 2:16, V=96,   96 rows
]

# measure mode: tenant -> (sparsity code, nwin). nlaps is DERIVED so that every
# tenant moves exactly --target-beats weight beats, i.e. runs for the same time.
MEASURE_PLAN = [
    ("10", 32),     # 2:16, V=1024
    ("00", 16),     # 2:4,  V=512
    ("11", 64),     # 2:32, V=2048
    ("01", 32),     # 2:8,  V=1024
    ("10", 16),     # 2:16, V=512
]

# --all-sparsity CODE: every tenant runs the SAME mode. 11 (2:32) gives the most output
# beats and the most lap boundaries for a given vector -- but NOT a full output beat every
# cycle: the engine writes ONE output beat per pass over the vector (every V/32 cycles at
# 2:32, plus the one-cycle lap bubble), so V=256 is one beat per 9 cycles. The INPUT side
# (3 channels per tenant, one beat per cycle) is the same in every mode and dominates the
# HBM traffic. Vector lengths stay distinct so the tenants are still different jobs.
ALL_SAME_NWIN = [32, 16, 64, 8, 128]      # V = 1024, 512, 2048, 256, 4096

def check_packer(emu):
    """hex_to_bin.py's constants must match THIS shape, or the images are wrong."""
    required = {"W_PCS": W_PCS, "IND_PCS": IND_PCS, "A_PCS": A_PCS,
                "C_PCS": C_PCS, "LANES": LANES}
    path = os.path.join(emu, "hex_to_bin.py")
    text = io.open(path, encoding="utf-8").read()
    wrong = []
    for name, want in sorted(required.items()):
        m = re.search(r"^%s\s*=\s*(\d+)" % name, text, re.M)
        if not m:
            raise SystemExit("cannot find %s in %s" % (name, path))
        if int(m.group(1)) != want:
            wrong.append((name, int(m.group(1)), want))
    if wrong:
        print("hex_to_bin.py is set for another configuration:")
        for name, got, want in wrong:
            print("   %-8s = %-4d  should be %d" % (name, got, want))
        print("\nfix it with:")
        for name, got, want in wrong:
            print("   sed -i 's/^%s *= *%d/%s = %d/' %s" % (name, got, name, want, path))
        raise SystemExit(1)


def run(cmd, cwd):
    print("   $ " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, universal_newlines=True)
    out, _ = p.communicate()
    if p.returncode != 0:
        sys.stdout.write(out)
        raise SystemExit("command failed (%d)" % p.returncode)
    return out


def md5(path):
    h = hashlib.md5()
    with io.open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def copy_bins(emu, bindir):
    for pre, n in (("weights_pc", W_PCS), ("ind_pc", IND_PCS), ("act_pc", A_PCS)):
        for i in range(n):
            shutil.copy2(os.path.join(emu, "bin", "%s%d.bin" % (pre, i)), bindir)


def check_tenant(k, tdir, code, nwin, want_beats, want_rows, with_golden):
    """Sizes, lap arithmetic and the sparsity code, from the COPIED images."""
    bindir = os.path.join(tdir, "bin")
    wb = os.path.getsize(os.path.join(bindir, "weights_pc0.bin")) // PC_BYTES
    ib = os.path.getsize(os.path.join(bindir, "ind_pc0.bin")) // PC_BYTES
    ab = os.path.getsize(os.path.join(bindir, "act_pc0.bin")) // PC_BYTES
    problems = []
    if wb != ib:
        problems.append("weights %d beats vs indices %d -- the join would hang" % (wb, ib))
    if wb != want_beats:
        problems.append("%d weight beats, expected %d" % (wb, want_beats))
    if ab != nwin:
        problems.append("%d activation beats, expected nwin = %d" % (ab, nwin))
    if with_golden:
        golden = sum(1 for ln in io.open(os.path.join(tdir, "golden.txt")) if ln.strip())
        if golden != want_rows:
            problems.append("golden.txt has %d rows, expected %d" % (golden, want_rows))
    with io.open(os.path.join(bindir, "ind_pc%d.bin" % SP_PC), "rb") as f:
        img = f.read()
    got = set(img[b * PC_BYTES + SP_BYTE] & 0x3 for b in range(min(wb, 4096)))
    if got != set([int(code, 2)]):
        problems.append("sparsity code at ind_pc%d byte %d reads %s, expected %d"
                        % (SP_PC, SP_BYTE, sorted(got), int(code, 2)))
    if problems:
        for p in problems:
            print("   FAIL: " + p)
        raise SystemExit("tenant %d stimulus is wrong -- nothing further written" % k)
    print("   ok: %d weight beats = %d index beats, %d activation beats%s, code %s at "
          "ind_pc%d byte %d" % (wb, ib, ab,
                                ", %d golden rows" % want_rows if with_golden else "",
                                SP_NAME[code], SP_PC, SP_BYTE))
    return wb


def main():
    ap = argparse.ArgumentParser(description="per-tenant stimulus for the multi-tenant build")
    ap.add_argument("--emu", required=True, help="GEMV_4.0_Source/Emulation directory")
    ap.add_argument("--out", default=".", help="where t0/, t1/ ... are written")
    ap.add_argument("--tenants", type=int, required=True)
    ap.add_argument("--mode", choices=["correctness", "measure"], default="correctness")
    ap.add_argument("--target-beats", type=int, default=2097152,
                    help="measure mode: weight beats PER TENANT, the same for all of them "
                         "so they run for the same time (default 2097152 = 5.6 ms at 375 MHz)")
    ap.add_argument("--cores", type=int, default=CORES,
                    help="engine shape of ONE tenant (default %d)" % CORES)
    ap.add_argument("--blocks", type=int, default=BLOCKS,
                    help="engine shape of ONE tenant (default %d)" % BLOCKS)
    ap.add_argument("--all-sparsity", choices=sorted(SP_NAME), default=None,
                    help="measure mode: run EVERY tenant at this mode. 11 (2:32) gives the "
                         "most output beats and lap boundaries; input traffic is the same in "
                         "every mode")
    a = ap.parse_args()
    if a.all_sparsity and a.mode != "measure":
        raise SystemExit("--all-sparsity only applies to --mode measure")
    configure(a.cores, a.blocks)        # every width follows from the shape

    emu = os.path.abspath(os.path.expanduser(a.emu))
    out = os.path.abspath(os.path.expanduser(a.out))
    plan = PLAN if a.mode == "correctness" else MEASURE_PLAN
    if a.tenants < 1 or a.tenants > len(plan):
        raise SystemExit("--tenants must be 1..%d (extend the plan for more)" % len(plan))
    need = ["gen_timing_stimulus.py"] if a.mode == "measure" else \
           ["gemv4_cosim_gen.py", "hex_to_bin.py", "compare_gemv4_py36.py"]
    for f in need:
        if not os.path.exists(os.path.join(emu, f)):
            raise SystemExit("%s not found in %s" % (f, emu))
    if a.mode == "correctness":
        check_packer(emu)

    print("per-tenant stimulus: %s mode, %d tenant(s), %dx%d engines"
          % (a.mode, a.tenants, CORES, BLOCKS))
    print("WARNING: this OVERWRITES the shared stimulus in %s -- regenerate before any other run"
          % emu)
    if a.mode == "measure":
        per_t_mb = a.target_beats * PC_BYTES * (W_PCS + IND_PCS) / 1e6
        print("         %d weight beats per tenant -> ~%.0f MB per tenant, ~%.1f GB total"
              % (a.target_beats, per_t_mb, per_t_mb * a.tenants / 1e3))
        print("         check `df -h ~` before this if the total is large")

    rows = []
    for k in range(a.tenants):
        tdir = os.path.join(out, "t%d" % k)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir)          # never leave a stale golden beside new images
        bindir = os.path.join(tdir, "bin")
        os.makedirs(bindir)

        if a.mode == "correctness":
            code, nwin, nlaps, seed = plan[k]
            beats = nwin * FREEZE[code] * nlaps
            print("\ntenant %d: %s, V=%d, %d lap(s) -> %d rows, seed %d"
                  % (k, SP_NAME[code], 32 * nwin, nlaps, nlaps * LANES, seed))
            run([sys.executable, "gemv4_cosim_gen.py",
                 "--cores", str(CORES), "--blocks", str(BLOCKS), "--sparsity", code,
                 "--nwin", str(nwin), "--nlaps", str(nlaps), "--seed", str(seed)], cwd=emu)
            run([sys.executable, "hex_to_bin.py", "pack"], cwd=emu)
            copy_bins(emu, bindir)
            shutil.copy2(os.path.join(emu, "golden.txt"), tdir)
            shutil.copy2(os.path.join(emu, "compare_gemv4_py36.py"), tdir)
            check_tenant(k, tdir, code, nwin, beats, nlaps * LANES, True)
        else:
            code, nwin = plan[k]
            if a.all_sparsity:
                code, nwin = a.all_sparsity, ALL_SAME_NWIN[k]
            per_lap = nwin * FREEZE[code]
            nlaps = a.target_beats // per_lap
            beats = nlaps * per_lap
            if beats != a.target_beats:
                raise SystemExit("tenant %d: %d beats/lap does not divide --target-beats %d "
                                 "(use a power of two)" % (k, per_lap, a.target_beats))
            print("\ntenant %d: %s, V=%d, %d laps -> %d rows, %d weight beats"
                  % (k, SP_NAME[code], 32 * nwin, nlaps, nlaps * LANES, beats))
            run([sys.executable, "gen_timing_stimulus.py",
                 "--cores", str(CORES), "--blocks", str(BLOCKS), "--sparsity", code,
                 "--nwin", str(nwin), "--nlaps", str(nlaps)], cwd=emu)
            copy_bins(emu, bindir)
            check_tenant(k, tdir, code, nwin, beats, nlaps * LANES, False)

        rows.append((k, code, nwin, nlaps, beats, nlaps * LANES,
                     md5(os.path.join(bindir, "weights_pc0.bin")),
                     md5(os.path.join(bindir, "ind_pc0.bin"))))

    # WHAT "DIFFERENT" HAS TO MEAN DEPENDS ON THE GENERATOR.
    # correctness: gemv4_cosim_gen.py fills the weights from --seed, so the weight
    #   images must differ -- that is what makes a cross-wired stream_connect fail.
    # measure: gen_timing_stimulus.py writes a FIXED TILED PATTERN, so two tenants
    #   with the same beat count get byte-identical weights however their sparsity
    #   and shape differ. Harmless (the engine is data-independent, and equal beats
    #   is exactly what makes them overlap), but the weight md5 cannot police
    #   cross-wiring here, so require distinct SHAPES and say where the real check
    #   lives.
    if a.mode == "correctness":
        seen = {}
        for r in rows:
            seen.setdefault(r[6], []).append(r[0])
        dupes = [v for v in seen.values() if len(v) > 1]
        if dupes:
            raise SystemExit("tenants %s got IDENTICAL weight images -- a cross-wired "
                             "stream_connect would pass unnoticed. Fix PLAN." % dupes)
    else:
        seen = {}
        for r in rows:
            seen.setdefault((r[1], r[2], r[3]), []).append(r[0])
        dupes = [v for v in seen.values() if len(v) > 1]
        if dupes:
            raise SystemExit("tenants %s have the same (sparsity, nwin, nlaps) -- they are "
                             "not independent jobs. Fix MEASURE_PLAN." % dupes)

    print("\n%-3s %-6s %6s %8s %10s %10s  %-14s %s"
          % ("t", "spars", "V", "laps", "w beats", "rows", "weights_pc0", "ind_pc0 md5"))
    for k, code, nwin, nlaps, beats, nrows, hw, hi in rows:
        print("%-3d %-6s %6d %8d %10d %10d  %-14s %s"
              % (k, SP_NAME[code], 32 * nwin, nlaps, beats, nrows, hw[:12], hi[:12]))
    if a.mode == "measure":
        same = len(set(r[4] for r in rows)) == 1
        print("\nequal weight beats across tenants: %s%s"
              % ("YES -- they will run for the same time and overlap" if same else "NO",
                 "" if same else " (interference ratios will understate contention)"))
        if a.all_sparsity:
            print("every tenant runs %s: weight AND index images repeat,"
                  % SP_NAME[a.all_sparsity])
            print("only the vector length and row count differ. NO GOLDEN here.")
        else:
            print("weight images are a fixed pattern in this mode, so they repeat; the ind_pc0")
            print("md5s differ because each tenant carries its own sparsity mode. NO GOLDEN here:")
            print("prove correctness with --mode correctness, which uses seeded random data.")
    else:
        print("\nevery tenant has its own golden; the driver compares it after every run.")
    print("all %d tenant(s) differ. Run the host with: %s"
          % (len(rows), " ".join("%d:t%d" % (r[0], r[0]) for r in rows)))


if __name__ == "__main__":
    main()
