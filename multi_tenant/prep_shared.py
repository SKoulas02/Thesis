"""SHARED WORKLOAD: four 4x4 tenants cooperate on ONE MIXED matrix, one sparsity mode each.

    # correctness: one MIXED matrix + golden, cut into four one-mode quarters
    python3 prep_shared.py correctness --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation --out shared
    ./host_sparse_multi sparse_4x4_x4_375.xclbin 374 0 0:shared/t0 1:shared/t1 2:shared/t2 3:shared/t3
    python3 prep_shared.py stitch --out shared        # compares all four, then the stitched y

    # the same matrix on ONE tenant, for the speedup baseline
    ./host_sparse_multi sparse_4x4_x4_375.xclbin 374 0 0:shared/full

THE WORKLOAD is the family study's MIXED matrix (run_shapes.py --sparsity mix): four
equal-row quarters at 2:4, 2:8, 2:16 and 2:32 over ONE activation vector. Tenant k takes
quarter k -- one sparsity mode per tenant, decided 2026-09-21 -- every tenant gets the SAME
vector, and the four outputs are stitched back into one result.

IT IS LOPSIDED, AND THAT IS PART OF THE RESULT. A 2:4 row costs 8x a 2:32 row, so the
quarters carry work 8:4:2:1. The 2:4 tenant does 8/15 of the matrix and sets the finish
time while the others idle, so the expected speedup over ONE tenant doing the whole matrix
is 15/8 = 1.875x, not 4x. (The balanced split -- every tenant a quarter of EACH quarter --
was discussed and not chosen.)

THE CUT IS EXACT, NOT REGENERATED. gemv4_cosim_gen.py builds the whole matrix in one run with
--sparsities (one code per lap, ONE vector for all laps) and its golden. The packed per-PC
images are consecutive 32-byte beats in lap order and lap L takes nwin x freeze(L) beats, so
quarter k is ONE contiguous byte range of every weight and index image; the activation images
go to every tenant unchanged; golden.txt is in lap order at 16 rows per lap, so tenant k's
golden is one contiguous block of rows. The four quarters ARE the original matrix, which the
checks below prove byte for byte before anything is declared ready.
"""

import argparse
import io
import os
import shutil
import subprocess
import sys

import prep_tenants as pt       # check_packer, run, md5, FREEZE, SP_NAME, LANES, PC_BYTES

MODES = ["00", "01", "10", "11"]            # tenant k <- quarter k: 2:4, 2:8, 2:16, 2:32

# The family study's eleven GEMV shapes -- COPIED from Vitis/run_shapes.py SHAPES, which is
# what the Chart 12-14 numbers were measured on. Keep them identical.
SHAPES = [
    ("G1", 512, 512), ("G2", 768, 768), ("G3", 1024, 1024), ("G4", 3072, 768),
    ("G5", 768, 3072), ("G6", 4096, 1024), ("G7", 1024, 4096), ("G8", 2048, 2048),
    ("G9", 4096, 4096), ("G10", 8192, 2048), ("G11", 2048, 8192),
]
SHAPE = dict((n, (m, k)) for n, m, k in SHAPES)

W_FILES = ["weights_pc%d.bin" % i for i in range(pt.W_PCS)]
I_FILES = ["ind_pc%d.bin" % i for i in range(pt.IND_PCS)]
A_FILES = ["act_pc%d.bin" % i for i in range(pt.A_PCS)]


def quarter_laps(M):
    """Laps per quarter: four whole-lap quarters (run_shapes.py: ceil(M / (4 x lanes)))."""
    return -(-M // (4 * pt.LANES))


def quarter_beats(q, nwin):
    return [q * nwin * pt.FREEZE[c] for c in MODES]


def read_bytes(path):
    with io.open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    with io.open(path, "wb") as f:
        f.write(data)


def read_lines(path):
    with io.open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def lap_walk(ind_img, nwin):
    """The host's lap walk (host_sparse_multi.cpp load_stimulus), in Python: -> [(code, beats)]."""
    nbeats = len(ind_img) // pt.PC_BYTES
    laps, pos = [], 0
    while pos < nbeats:
        code = ind_img[pos * pt.PC_BYTES + pt.SP_BYTE] & 0x3
        nb = nwin * pt.FREEZE["{:02b}".format(code)]
        if pos + nb > nbeats:
            raise SystemExit("lap walk: lap %d needs %d beats, %d remain" % (len(laps), nb,
                                                                           nbeats - pos))
        for k in range(pos, pos + nb):
            if ind_img[k * pt.PC_BYTES + pt.SP_BYTE] & 0x3 != code:
                raise SystemExit("lap walk: sparsity changes mid-lap at beat %d" % k)
        laps.append(("{:02b}".format(code), nb))
        pos += nb
    return laps


def fresh(d):
    if os.path.isdir(d):
        shutil.rmtree(d)                        # never leave a stale golden beside new images
    os.makedirs(os.path.join(d, "bin"))


def cmd_correctness(a):
    emu = os.path.abspath(os.path.expanduser(a.emu))
    out = os.path.abspath(os.path.expanduser(a.out))
    for f in ("gemv4_cosim_gen.py", "hex_to_bin.py", "compare_gemv4_py36.py"):
        if not os.path.exists(os.path.join(emu, f)):
            raise SystemExit("%s not found in %s" % (f, emu))
    pt.check_packer(emu)
    M, N = SHAPE[a.shape]
    if N % 32:
        raise SystemExit("N=%d is not a multiple of 32" % N)
    nwin, q = N // 32, quarter_laps(M)
    codes = sum([[c] * q for c in MODES], [])
    bq = quarter_beats(q, nwin)
    print("shared workload, correctness: %s = %dx%d MIXED, 4 quarters x %d laps (%d rows), "
          "V=%d, seed %d" % (a.shape, M, N, q, q * pt.LANES, N, a.seed))
    print("WARNING: this OVERWRITES the shared stimulus in %s" % emu)

    pt.run([sys.executable, "gemv4_cosim_gen.py", "--cores", str(pt.CORES), "--blocks",
            str(pt.BLOCKS), "--nwin", str(nwin), "--sparsities", ",".join(codes),
            "--seed", str(a.seed), "--value-hi", str(a.value_hi)], cwd=emu)
    pt.run([sys.executable, "hex_to_bin.py", "pack"], cwd=emu)

    src = os.path.join(emu, "bin")
    full = dict((f, read_bytes(os.path.join(src, f))) for f in W_FILES + I_FILES + A_FILES)
    golden = read_lines(os.path.join(emu, "golden.txt"))
    total = sum(bq)

    # ---- the whole matrix, as generated: the single-tenant baseline -----------------
    problems = []
    for f in W_FILES + I_FILES:
        if len(full[f]) != total * pt.PC_BYTES:
            problems.append("%s is %d beats, the plan says %d" % (f, len(full[f]) // pt.PC_BYTES,
                                                                  total))
    for f in A_FILES:
        if len(full[f]) != nwin * pt.PC_BYTES:
            problems.append("%s is %d beats, expected nwin = %d" % (f, len(full[f]) // pt.PC_BYTES,
                                                                    nwin))
    if len(golden) != 4 * q * pt.LANES:
        problems.append("golden.txt has %d rows, expected %d" % (len(golden), 4 * q * pt.LANES))
    walk = lap_walk(full[I_FILES[pt.SP_PC]], nwin) if not problems else []
    if walk and [c for c, _ in walk] != codes:
        problems.append("the generated laps are not %d x 2:4, 2:8, 2:16, 2:32 in that order" % q)
    if problems:
        for p in problems:
            print("   FAIL: " + p)
        raise SystemExit("generated matrix is not what was asked for -- nothing written")

    fd = os.path.join(out, "full")
    fresh(fd)
    for f, data in full.items():
        write_bytes(os.path.join(fd, "bin", f), data)
    shutil.copy2(os.path.join(emu, "golden.txt"), fd)
    shutil.copy2(os.path.join(emu, "compare_gemv4_py36.py"), fd)

    # ---- the cut: quarter k = beats [start_k, start_k + bq[k]), rows [k q 16, (k+1) q 16) ---
    start, rows = 0, q * pt.LANES
    for k, code in enumerate(MODES):
        td = os.path.join(out, "t%d" % k)
        fresh(td)
        lo, hi = start * pt.PC_BYTES, (start + bq[k]) * pt.PC_BYTES
        for f in W_FILES + I_FILES:
            write_bytes(os.path.join(td, "bin", f), full[f][lo:hi])
        for f in A_FILES:
            write_bytes(os.path.join(td, "bin", f), full[f])       # the SAME vector
        with io.open(os.path.join(td, "golden.txt"), "w", encoding="utf-8", newline="\n") as g:
            g.write("\n".join(golden[k * rows:(k + 1) * rows]) + "\n")
        shutil.copy2(os.path.join(emu, "compare_gemv4_py36.py"), td)
        start += bq[k]

    # ---- prove the cut: re-read what was WRITTEN and rebuild the original from it ----
    for f in W_FILES + I_FILES:
        joined = b"".join(read_bytes(os.path.join(out, "t%d" % k, "bin", f)) for k in range(4))
        if joined != full[f]:
            problems.append("%s: the four quarters do not reassemble the matrix" % f)
    for k, code in enumerate(MODES):
        tb = os.path.join(out, "t%d" % k, "bin")
        tw = lap_walk(read_bytes(os.path.join(tb, I_FILES[pt.SP_PC])), nwin)
        if [c for c, _ in tw] != [code] * q:
            problems.append("t%d: lap walk finds %s, expected %d laps of %s"
                            % (k, sorted(set(c for c, _ in tw)), q, pt.SP_NAME[code]))
        for f in A_FILES:
            if read_bytes(os.path.join(tb, f)) != full[f]:
                problems.append("t%d: %s is not the shared vector" % (k, f))
    stitched_golden = sum((read_lines(os.path.join(out, "t%d" % k, "golden.txt"))
                           for k in range(4)), [])
    if stitched_golden != golden:
        problems.append("the four golden blocks do not reassemble golden.txt")
    if problems:
        for p in problems:
            print("   FAIL: " + p)
        raise SystemExit("the cut is wrong -- do not run this stimulus")

    print("\n%-4s %-5s %6s %9s %8s %10s   %s" % ("t", "mode", "laps", "w beats", "rows",
                                                "share", "weights_pc0 md5"))
    for k, code in enumerate(MODES):
        print("%-4s %-5s %6d %9d %8d %9.1f%%   %s"
              % ("t%d" % k, pt.SP_NAME[code], q, bq[k], rows, 100.0 * bq[k] / total,
                 pt.md5(os.path.join(out, "t%d" % k, "bin", W_FILES[0]))[:12]))
    print("%-4s %-5s %6d %9d %8d %9.1f%%" % ("full", "MIXED", 4 * q, total, 4 * rows, 100.0))
    print("\nchecked: the quarters reassemble every image byte for byte, each tenant's lap walk")
    print("finds only its own mode, all four carry the same vector, and the golden blocks")
    print("reassemble golden.txt. Expected speedup at equal clock: %d / %d = %.3fx."
          % (total, bq[0], float(total) / bq[0]))
    rel = os.path.relpath(out)
    print("\nrun the four together, then stitch:")
    print("  ./host_sparse_multi <xclbin> <clock> 0 " + " ".join("%d:%s/t%d" % (k, rel, k)
                                                                for k in range(4)))
    print("  python3 prep_shared.py stitch --out %s" % rel)
    print("baseline, the whole matrix on one tenant:")
    print("  ./host_sparse_multi <xclbin> <clock> 0 0:%s/full" % rel)


def compare_in(d):
    try:
        os.remove(os.path.join(d, "tlast.txt"))
    except OSError:
        pass
    p = subprocess.Popen([sys.executable, "compare_gemv4_py36.py"], cwd=d,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    txt = p.communicate()[0]
    line = [ln for ln in txt.split("\n") if ln.startswith("compared")]
    return ("=== PASS ===" in txt), (line[0] if line else txt.strip()[-200:])


def cmd_stitch(a):
    out = os.path.abspath(os.path.expanduser(a.out))
    ok_all, parts = True, []
    for k in range(4):
        td = os.path.join(out, "t%d" % k)
        if not os.path.exists(os.path.join(td, "output.txt")):
            raise SystemExit("t%d/output.txt missing -- run the host first (it deletes old "
                             "outputs at start, so a failed run cannot leave one behind)" % k)
        ok, line = compare_in(td)
        ok_all &= ok
        print("  t%d (%-4s) vs its own golden: %s   %s"
              % (k, pt.SP_NAME[MODES[k]], "PASS" if ok else "FAIL", line))
        parts += read_lines(os.path.join(td, "output.txt"))
    sd = os.path.join(out, "stitched")
    if os.path.isdir(sd):
        shutil.rmtree(sd)
    os.makedirs(sd)
    with io.open(os.path.join(sd, "output.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts) + "\n")
    shutil.copy2(os.path.join(out, "full", "golden.txt"), sd)
    shutil.copy2(os.path.join(out, "full", "compare_gemv4_py36.py"), sd)
    ok, line = compare_in(sd)
    print("  STITCHED result (%d rows) vs the golden of the WHOLE matrix: %s   %s"
          % (len(parts), "PASS" if ok else "FAIL", line))
    if not (ok and ok_all):
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description="shared-workload stimulus: one MIXED matrix, "
                                             "one sparsity quarter per tenant")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("correctness", help="one MIXED matrix + golden, cut into t0..t3 + full")
    c.add_argument("--emu", required=True, help="GEMV_4.0_Source/Emulation directory")
    c.add_argument("--out", default="shared", help="writes <out>/t0..t3 and <out>/full")
    c.add_argument("--shape", default="G3", choices=[n for n, _, _ in SHAPES],
                   help="one of the eleven family shapes (default G3 = 1024x1024)")
    c.add_argument("--seed", type=int, default=777)
    c.add_argument("--value-hi", type=int, default=7,
                   help="gemv4_cosim_gen.py value range; 100 exercises bf16 rounding")
    s = sub.add_parser("stitch", help="compare t0..t3, then the stitched result vs the whole")
    s.add_argument("--out", default="shared")
    a = ap.parse_args()
    if a.cmd == "correctness":
        cmd_correctness(a)
    elif a.cmd == "stitch":
        cmd_stitch(a)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
