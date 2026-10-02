"""THE MEASUREMENT WINDOW: every measurement of the professor's isolated window, in one run.

    bash ~/GEMV_Sparse/workload_tools/run_window.sh --check                    # preflight, BEFORE
    tmux new -d -s window 'bash ~/GEMV_Sparse/workload_tools/run_window.sh'   # THE window
    tail -f ~/GEMV_Sparse/window_latest.log                                    # watch it

A NEW FILE (2026-10-01). It drives the two measurement runners that were gated before -- and
changes neither of them:
  part 1  QWEN  run_qwen.py --arch <a> on every build of plan_qwen.archs() (22 once every clock is
                recorded): 40 layers of the Qwen3.5 MoE step-1 test, realistic N(0,1) data
  part 2  UC1   run_workload_xrt.py --arch <a> --vectors per-matrix on the 13 original builds
                (plan_workload.archs_for("per-matrix")): every matrix its own calculation + vector
Qwen first: if time runs short, the new result is complete and UC1 (a rerun) is what is cut.

MEASUREMENTS ONLY. Every correctness gate ran BEFORE the window, with the same tools and settings
(run_all_qwen.sh / run_all_workloads_xrt.sh --per-matrix --gate-only, XRT_INI_PATH exported). The
preflight checks the evidence: every Qwen build has its qwen_accuracy_<a>_<MHz>MHz_wsigma1.csv and
every UC1 build its wl_correct/t0/c0/output.txt, both written AFTER the ert=false file existed.

SETTINGS (the user's decisions, 2026-10-01), applied here and written to settings.txt:
  host        host_workload_xrt (native XRT, one thread, pre-built runs) -- compiled into every
              build folder by --check; the window compiles nothing (~15 s x 22 of window time)
  scheduler   ert=false: XRT_INI_PATH=~/GEMV_Sparse/xrt_ert_off.ini for every run; the card is
              reset at the start (XRT applies it when an xclbin loads) and at the end
  soak        60 s of whole passes per build after 3 timed passes, then 20 s of idle power
  data        Qwen --weight-sigma 1.0: N(0,1) weights magnitude-pruned, N(0,1) vector

SAFETY. The window refuses to start if the server is not quiet (1-minute load above --max-load,
any vivado / v++ / vitis_hls / xsim of ANY user, any other program on the card), or if a build,
its clock, its host, its power telemetry or its gate evidence is missing, or the ert=false file is
not exactly what it should be. A failed build is logged, the card is reset and the window goes on;
three failures in a row stop it. A TIME GUARD skips what would not finish inside --minutes
(default 110 of the 120). Ctrl-C and hang-up are ignored (run_window.sh), so a dropped SSH session
does not stop it. To stop it on purpose:
    pkill -u $USER -f run_window.py; pkill -u $USER -f "run_qwen.py|run_workload_xrt.py"; pkill -u $USER -f host_workload
then reset the card: xbutil reset --device 0000:af:00.1

RESULTS: ~/GEMV_Sparse/window_<date>/ -- qwen/ and uc1/ (the runners' CSV pairs), logs/ (one per
build), window.log, settings.txt, window_summary.csv. The last line printed is the scp that brings
the folder home. Python 3.6, stdlib only.

TEST HOOKS (local tests only; unset on the server): WINDOW_FAKE_PS (a file of "pid user comm args"
lines instead of ps), WINDOW_FAKE_LOAD (a load instead of os.getloadavg).
"""

import argparse
import csv
import hashlib
import io
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_workload as pw                              # noqa: E402
import plan_qwen as pq                                  # noqa: E402
import run_workload_xrt as rw                           # noqa: E402  (data_clk)

ROOT = os.environ.get("GEMV_ROOT", os.path.expanduser("~/GEMV_Sparse"))
EMU = os.environ.get("EMU", os.path.join(ROOT, "GEMV_4.0_Source", "Emulation"))
BDF = os.environ.get("BDF", "0000:af:00.1")
SERVER = "skoulas@coroni.microlab.ntua.gr"
INI = os.path.join(ROOT, "xrt_ert_off.ini")
INI_TEXT = "[Runtime]\nert=false\n"
TOOLS = ["run_window.py", "run_window.sh", "run_qwen.py", "gen_qwen_stimulus.py", "plan_qwen.py",
         "run_workload_xrt.py", "plan_workload.py", "plan_workload_bcast.py", "host_workload_xrt.cpp"]
BUSY_NAMES = ("vivado", "vitis_hls", "xsim", "xsimk", "v++")
CARD_USERS = ("host_workload", "host_sparse")          # programs that use the card itself
WEIGHT_SIGMA = "1.0"


class Log(object):
    def __init__(self, path):
        self.path = path
        self.f = io.open(path, "a", encoding="utf-8")

    def __call__(self, msg=""):
        print(msg)
        sys.stdout.flush()
        self.f.write(msg + "\n")
        self.f.flush()


def now():
    return time.strftime("%H:%M:%S")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def processes():
    """[(pid, user, comm, args)] of every process on the server, or None if ps fails."""
    fake = os.environ.get("WINDOW_FAKE_PS")
    if fake:
        lines = io.open(fake, encoding="utf-8").read().splitlines() if os.path.exists(fake) else []
    else:
        try:
            lines = subprocess.check_output(["ps", "-eo", "pid=,user=,comm=,args="],
                                            universal_newlines=True).splitlines()
        except (OSError, subprocess.CalledProcessError):
            return None
    out = []
    for ln in lines:
        p = ln.split(None, 3)
        if len(p) >= 3:
            out.append((p[0], p[1], p[2], p[3] if len(p) > 3 else ""))
    return out


def load1():
    fake = os.environ.get("WINDOW_FAKE_LOAD")
    if fake:
        return float(fake)
    return os.getloadavg()[0] if hasattr(os, "getloadavg") else None


def server_state():
    """-> (1-min load, [busy processes], [other card programs]); lists are None if ps failed."""
    procs = processes()
    me = str(os.getpid())
    if procs is None:
        return load1(), None, None
    busy = ["%s %s (pid %s)" % (u, c, pid) for pid, u, c, a in procs if c in BUSY_NAMES]
    card = ["%s %s (pid %s)" % (u, a[:60], pid) for pid, u, c, a in procs
            if pid != me and any(k in a for k in CARD_USERS)]
    return load1(), busy, card


def selected(parts, names):
    """[(part, arch tuple)] in window order: Qwen on every build with a clock, then UC1 on the 13."""
    out = []
    if "qwen" in parts:
        out += [("qwen", a) for a in pq.archs()]
    if "uc1" in parts:
        out += [("uc1", a) for a in pw.archs_for("per-matrix")]
    if names:
        known = set(a[0] for _, a in out)
        bad = [n for n in names if n not in known]
        if bad:
            raise SystemExit("unknown or not ready (no clock recorded): %s -- known: %s"
                             % (" ".join(bad), " ".join(sorted(known))))
        out = [(p, a) for p, a in out if a[0] in names]
    return out


def outputs(part, name, clk):
    stem = ("workload_xrt_qwen_%s_%dMHz" if part == "qwen" else
            "workload_xrt_permatrix_%s_%dMHz") % (name, clk)
    return [stem + ".csv", stem + "_calcs.csv"]


def gate_evidence(part, name, clk, fpath, since):
    """None if this build's gate ran after `since` (the ert=false file), else what is wrong."""
    if part == "qwen":
        f = os.path.join(fpath, "qwen_accuracy_%s_%dMHz_wsigma1.csv" % (name, clk))
    else:
        f = os.path.join(fpath, "wl_correct", "t0", "c0", "output.txt")
    rel = os.path.relpath(f, ROOT)
    if not os.path.exists(f):
        return "no gate output %s" % rel
    if os.path.getmtime(f) < since:
        return "gate older than the ert=false file (%s)" % rel
    return None


def host_problem(fpath):
    cpp = os.path.join(fpath, "host_workload_xrt.cpp")
    exe = os.path.join(fpath, "host_workload_xrt")
    if not os.path.exists(exe):
        return "no host_workload_xrt"
    if not os.path.exists(cpp) or md5(cpp) != md5(os.path.join(HERE, "host_workload_xrt.cpp")):
        return "its host_workload_xrt.cpp differs from workload_tools'"
    if os.path.getmtime(exe) < os.path.getmtime(cpp):
        return "host_workload_xrt older than its source"
    return None


def compile_host(fpath, cores, blocks):
    shutil.copy2(os.path.join(HERE, "host_workload_xrt.cpp"), fpath)
    xrt = os.environ["XILINX_XRT"]
    cmd = ["g++", "-Wall", "-O2", "-std=c++17", "-I" + os.path.join(xrt, "include"),
           "host_workload_xrt.cpp", "-L" + os.path.join(xrt, "lib"), "-lxrt_coreutil", "-lrt",
           "-pthread", "-DCORES=%d" % cores, "-DBLOCKS=%d" % blocks, "-o", "host_workload_xrt"]
    p = subprocess.Popen(cmd, cwd=fpath, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, universal_newlines=True)
    out = p.communicate()[0]
    return None if p.returncode == 0 else "g++ failed: %s" % out.strip()[-300:]


def find_power_scraper():
    for d, _, files in os.walk(ROOT):
        if "power_scraper.py" in files:
            return os.path.join(d, "power_scraper.py")
    return None


def telemetry(fpath):
    """One reading through the folder's own power_scraper.py -> (board W, None) or (None, why)."""
    code = ("import sys; sys.path.insert(0, '.'); import power_scraper as p; "
            "r = p.read_once(%r); print('BOARD %%.2f' %% r['board_w'])" % BDF)
    p = subprocess.Popen([sys.executable, "-c", code], cwd=fpath, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         universal_newlines=True)
    out = p.communicate()[0]
    for ln in out.splitlines():
        if ln.startswith("BOARD "):
            return float(ln.split()[1]), None
    return None, out.strip()[-200:]


def reset_card(log):
    p = subprocess.Popen(["xbutil", "reset", "--device", BDF], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    out = p.communicate("y\n")[0]
    ok = p.returncode == 0 and "Successfully reset" in out
    log("%s card reset (%s): %s" % (now(), BDF, "ok" if ok else "FAILED -- " + out.strip()[-200:]))
    return ok


def preflight(sel, a, log, check):
    """-> list of blockers (empty = ready). check=True: compile every host, copy power_scraper,
    and only WARN about load/busy (builds may still be linking when the check runs)."""
    blockers, warnings = [], []
    # ---- tools, environment, the ert=false file ----------------------------------------------
    if not os.environ.get("XILINX_XRT"):
        blockers.append("XILINX_XRT not set (run through run_window.sh)")
    for f in TOOLS:
        if not os.path.exists(os.path.join(HERE, f)):
            blockers.append("workload_tools has no %s" % f)
    if any(p == "uc1" for p, _ in sel) and not os.path.exists(os.path.join(EMU,
                                                                           "gen_timing_stimulus.py")):
        blockers.append("no gen_timing_stimulus.py in %s (the UC1 stimulus)" % EMU)
    if not os.path.exists(INI):
        blockers.append("no %s -- create it: printf '[Runtime]\\nert=false\\n' > %s, then re-run "
                        "every gate with XRT_INI_PATH set to it" % (INI, INI))
        ini_time = time.time()
    else:
        text = io.open(INI, encoding="utf-8").read()
        if text.strip().split() != INI_TEXT.strip().split():
            blockers.append("%s holds %r, not %r" % (INI, text, INI_TEXT))
        ini_time = os.path.getmtime(INI)
    if a.free_gb > shutil.disk_usage(ROOT).free / 1e9:
        blockers.append("less than %d GB free in %s" % (a.free_gb, ROOT))
    # ---- the server: quiet? ------------------------------------------------------------------
    load, busy, card = server_state()
    quiet = []
    if load is not None and load > a.max_load:
        quiet.append("1-minute load %.1f > %.1f" % (load, a.max_load))
    if busy is None:
        quiet.append("cannot list processes (ps failed)")
    else:
        quiet += ["running: %s" % b for b in busy]
    if card:
        blockers += ["another program on the card: %s" % c for c in card]
    if check or a.ignore_load:
        warnings += quiet
    else:
        blockers += quiet
    # ---- every build: folder, xclbin, clock, host, telemetry, gate evidence ------------------
    src = find_power_scraper()
    folders, order = {}, []
    for part, (name, shape, n, clk, folder, xclbin) in sel:
        if folder not in folders:
            folders[folder] = (name, shape, clk, xclbin, [])
            order.append(folder)
        folders[folder][4].append((part, name))
    log("%-28s %-14s %-5s %-6s %-6s %-8s %s" % ("build folder", "builds", "MHz", "clock",
                                               "host", "power", "gate evidence"))
    first = True
    for folder in order:
        name, shape, clk, xclbin, uses = folders[folder]
        fpath = os.path.join(ROOT, folder)
        row = []
        if not os.path.isdir(fpath) or not os.path.exists(os.path.join(fpath, xclbin)):
            blockers.append("%s: no %s/%s" % (name, folder, xclbin))
            log("%-28s %-14s MISSING %s" % (folder, name, xclbin))
            continue
        try:
            got = rw.data_clk(os.path.join(fpath, xclbin))
        except SystemExit as e:
            got = None
            blockers.append("%s: no DATA_CLK (%s)" % (name, e))
        if got is not None and got != clk:
            blockers.append("%s: %s runs at %d MHz, the plan says %d" % (name, xclbin, got, clk))
        row.append("ok" if got == clk else "%s" % got)
        c, b = pq.SHAPE_OF[shape]
        if check:
            why = compile_host(fpath, c, b)
            if why:
                blockers.append("%s: %s" % (name, why))
            if not os.path.exists(os.path.join(fpath, "power_scraper.py")):
                if src:
                    shutil.copy2(src, fpath)
                else:
                    blockers.append("no power_scraper.py anywhere under %s" % ROOT)
        why = host_problem(fpath)
        if why:
            blockers.append("%s: %s%s" % (name, why, "" if check else " -- run --check"))
        row.append("ok" if not why else "BAD")
        if not os.path.exists(os.path.join(fpath, "power_scraper.py")):
            blockers.append("%s: no power_scraper.py%s" % (name, "" if check else " -- run --check"))
            row.append("none")
        elif first:
            w, why = telemetry(fpath)
            first = False
            if w is None:
                blockers.append("power telemetry fails: %s" % why)
                row.append("FAIL")
            else:
                row.append("%.1fW" % w)
        else:
            row.append("-")
        ev = []
        for part, nm in uses:
            why = gate_evidence(part, nm, clk, fpath, ini_time)
            ev.append("%s %s" % (part, "ok" if not why else "MISSING"))
            if why:
                blockers.append("%s (%s): %s" % (nm, part, why))
        log("%-28s %-14s %-5d %-6s %-6s %-8s %s" % (folder, name, clk, row[0], row[1], row[2],
                                                 ", ".join(ev)))
    for w in warnings:
        log("  WARNING: %s" % w)
    return blockers


def run_build(part, arch, env, win, log, a):
    """One measurement. -> (status, seconds, note)."""
    name, shape, n, clk, folder, xclbin = arch
    fpath = os.path.join(ROOT, folder)
    if part == "qwen":
        cmd = [sys.executable, os.path.join(HERE, "run_qwen.py"), "--arch", name,
               "--weight-sigma", WEIGHT_SIGMA]
    else:
        cmd = [sys.executable, os.path.join(HERE, "run_workload_xrt.py"), "--arch", name,
               "--vectors", "per-matrix", "--emu", EMU]
    cmd += ["--soak", str(a.soak), "--idle-after", str(a.idle_after),
            "--host-timeout", str(a.host_timeout)]
    logf = os.path.join(win, "logs", "%s_%s.log" % (part, name))
    t0 = time.time()
    with io.open(logf, "w", encoding="utf-8") as fh:
        fh.write("$ cd %s && %s\n" % (fpath, " ".join(cmd)))
        fh.flush()
        kw = {"start_new_session": True} if os.name == "posix" else {}
        p = subprocess.Popen(cmd, cwd=fpath, env=env, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=subprocess.STDOUT, **kw)
        try:
            rc = p.wait(timeout=a.build_timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(p.pid, signal.SIGKILL)  # the runner AND the host it started
                except OSError:
                    pass
            else:
                p.kill()
            p.wait()
            rc = None
    dur = time.time() - t0
    text = io.open(logf, encoding="utf-8", errors="replace").read()
    fresh = []
    for f in outputs(part, name, clk):
        src = os.path.join(fpath, f)
        if os.path.exists(src) and os.path.getmtime(src) >= t0 - 1:
            shutil.copy2(src, os.path.join(win, part, f))
            fresh.append(f)
    if rc is None:
        status, note = "FAIL", "no result after %d s -- killed" % a.build_timeout
    elif rc != 0:
        last = [ln for ln in text.strip().splitlines() if ln.strip()][-1:] or [""]
        status, note = "FAIL", "exit %d: %s" % (rc, last[0][:150])
    elif len(fresh) != 2:
        status, note = "FAIL", "exit 0 but its CSVs were not written"
    else:
        status, note = "OK", ""
    lines = text.splitlines()
    marks = [i for i, ln in enumerate(lines) if ln.startswith("=== ")]
    if status == "OK" and marks:
        for ln in lines[marks[-1]:]:
            if ln.strip() and not ln.startswith("wrote "):
                log("      " + ln.strip())
    return status, dur, note


def main():
    ap = argparse.ArgumentParser(description="the measurement window (run it through run_window.sh)")
    ap.add_argument("--check", action="store_true",
                    help="preflight only: compile every host, check every build, run nothing")
    ap.add_argument("--minutes", type=float, default=110.0,
                    help="time budget for the measurements (default 110 of the 120-minute window)")
    ap.add_argument("--parts", default="qwen,uc1", help="qwen,uc1 (default both, in that order)")
    ap.add_argument("--max-load", type=float, default=4.0,
                    help="refuse to start above this 1-minute load (default 4.0 on 40 cores)")
    ap.add_argument("--ignore-load", action="store_true",
                    help="start even if the server is busy (the results will say so; avoid)")
    ap.add_argument("--free-gb", type=int, default=5)
    ap.add_argument("--soak", type=float, default=60.0, help="soak seconds per build (window: 60)")
    ap.add_argument("--idle-after", type=float, default=20.0,
                    help="idle-power seconds after each soak (window: 20)")
    ap.add_argument("--host-timeout", type=int, default=300)
    ap.add_argument("--build-timeout", type=int, default=900)
    ap.add_argument("--estimate", type=float, default=None,
                    help="seconds per build until one has been measured (default 150 Qwen, 140 UC1)")
    ap.add_argument("names", nargs="*", help="only these builds (e.g. to resume an interrupted window)")
    a = ap.parse_args()
    parts = [p.strip() for p in a.parts.split(",") if p.strip()]
    if not parts or any(p not in ("qwen", "uc1") for p in parts):
        raise SystemExit("--parts takes qwen and/or uc1")
    sel = selected(parts, a.names)
    stamp = time.strftime("%Y%m%d_%H%M")
    if a.check:
        log = Log(os.path.join(ROOT, "window_check_%s.log" % stamp))
        win = None
    else:
        win, k = os.path.join(ROOT, "window_%s" % stamp), 1
        while os.path.exists(win):                      # a second window in the same minute
            k += 1
            win = os.path.join(ROOT, "window_%s_%d" % (stamp, k))
        for d in ("qwen", "uc1", "logs"):
            os.makedirs(os.path.join(win, d))
        log = Log(os.path.join(win, "window.log"))
        latest = os.path.join(ROOT, "window_latest.log")
        try:
            if os.path.lexists(latest):
                os.remove(latest)
            os.symlink(log.path, latest)
        except (OSError, NotImplementedError, AttributeError):
            pass
    est = {"qwen": 150.0, "uc1": 140.0}
    if a.estimate:
        est = {"qwen": a.estimate, "uc1": a.estimate}
    n_q = sum(1 for p, _ in sel if p == "qwen")
    n_u = sum(1 for p, _ in sel if p == "uc1")
    log("=== %s %s, %s, %s" % ("WINDOW PREFLIGHT" if a.check else "MEASUREMENT WINDOW",
                              time.strftime("%Y-%m-%d %H:%M:%S"), socket.gethostname(),
                              "log " + log.path))
    log("builds: %d Qwen + %d UC1; estimate ~%.0f min of the %.0f-minute budget"
        % (n_q, n_u, (n_q * est["qwen"] + n_u * est["uc1"]) / 60.0, a.minutes))
    waiting = [x[0] for x in pq.BUILDS if x[3] is None]
    if waiting and "qwen" in parts and not a.names:
        log("NOT IN THIS WINDOW -- no DATA_CLK recorded yet: %s" % ", ".join(waiting))
    blockers = preflight(sel, a, log, a.check)
    if waiting and "qwen" in parts and not a.names:
        blockers.append("%d build(s) have no clock recorded yet: %s" % (len(waiting),
                                                                        ", ".join(waiting)))
    if blockers:
        log("")
        for b in blockers:
            log("  NOT READY: %s" % b)
        log("=== %s" % ("PREFLIGHT: NOT READY -- fix the lines above" if a.check else
                       "NOTHING RUN -- fix the lines above (then run_window.sh --check)"))
        return 1
    if a.check:
        log("=== PREFLIGHT PASSED: every build ready; start the window with\n"
            "    tmux new -d -s window 'bash ~/GEMV_Sparse/workload_tools/run_window.sh'\n"
            "    tail -f ~/GEMV_Sparse/window_latest.log")
        return 0

    # ---- the window ------------------------------------------------------------------------------
    env = dict(os.environ, XRT_INI_PATH=INI, PYTHONUNBUFFERED="1")
    load, busy, _ = server_state()
    with io.open(os.path.join(win, "settings.txt"), "w", encoding="utf-8") as f:
        f.write("window %s on %s, user %s\n" % (stamp, socket.gethostname(),
                                                 os.environ.get("USER", "?")))
        f.write("1-minute load at the start: %s; busy processes: %s\n"
                % (load, ", ".join(busy) if busy else "none"))
        f.write("card %s; XRT_INI_PATH=%s:\n%s" % (BDF, INI, io.open(INI, encoding="utf-8").read()))
        try:
            ver = subprocess.check_output(["xbutil", "--version"], universal_newlines=True,
                                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            f.write("xbutil --version:\n%s\n" % ver.strip())
        except (OSError, subprocess.CalledProcessError) as e:
            f.write("xbutil --version failed: %s\n" % e)
        f.write("Qwen --weight-sigma %s; soak %g s, idle %g s, 3 timed passes; host timeout %d s\n"
                % (WEIGHT_SIGMA, a.soak, a.idle_after, a.host_timeout))
        f.write("tool md5s:\n")
        for t in TOOLS:
            f.write("  %s  %s\n" % (md5(os.path.join(HERE, t)), t))
        f.write("builds (part arch shape engines MHz folder xclbin):\n")
        for part, x in sel:
            f.write("  %s %s %s %d %d %s %s\n" % ((part,) + tuple(x)))
    if not reset_card(log):
        log("=== NOTHING RUN -- the card did not reset")
        return 1
    t_start = time.time()
    budget = a.minutes * 60.0
    rows, done, fails_in_a_row = [], {"qwen": [], "uc1": []}, 0
    stop = None
    for i, (part, arch) in enumerate(sel):
        name = arch[0]
        if stop:
            rows.append((part, arch, "SKIPPED", 0.0, stop))
            continue
        guess = (sum(done[part]) / len(done[part])) if done[part] else est[part]
        left = budget - (time.time() - t_start)
        if guess + 30 > left:
            stop = "time guard: %.0f s left, a %s build takes ~%.0f s" % (left, part, guess)
            log("%s %s -- skipping the rest" % (now(), stop))
            rows.append((part, arch, "SKIPPED", 0.0, stop))
            continue
        log("%s [%d/%d] %s %s ..." % (now(), i + 1, len(sel), part, name))
        status, dur, note = run_build(part, arch, env, win, log, a)
        done[part].append(dur)
        log("%s [%d/%d] %s %s: %s in %.1f min%s" % (now(), i + 1, len(sel), part, name, status,
                                                    dur / 60.0, (" -- " + note) if note else ""))
        rows.append((part, arch, status, dur, note))
        if status == "OK":
            fails_in_a_row = 0
        else:
            fails_in_a_row += 1
            reset_card(log)
            if fails_in_a_row >= 3:
                stop = "three builds failed in a row -- something systematic; stopped"
                log("%s %s" % (now(), stop))
    reset_card(log)
    total = (time.time() - t_start) / 60.0
    with io.open(os.path.join(win, "window_summary.csv"), "w", encoding="utf-8",
                 newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["part", "arch", "shape", "engines", "clock_mhz", "folder", "status", "minutes",
                    "note"])
        for part, x, status, dur, note in rows:
            w.writerow([part, x[0], x[1], x[2], x[3], x[4], status, "%.2f" % (dur / 60.0), note])
    ok = sum(1 for r in rows if r[2] == "OK")
    log("")
    log("=== WINDOW DONE %s: %d of %d builds measured in %.1f min (%d failed, %d skipped)"
        % (time.strftime("%H:%M:%S"), ok, len(rows), total,
           sum(1 for r in rows if r[2] == "FAIL"), sum(1 for r in rows if r[2] == "SKIPPED")))
    for part, x, status, dur, note in rows:
        if status != "OK":
            log("  %-7s %-5s %-14s %s" % (status, part, x[0], note))
    log("copy home (PowerShell, from the repo root):")
    log('scp -r "%s:~/GEMV_Sparse/%s" results/' % (SERVER, os.path.basename(win)))
    return 0 if ok == len(rows) else 2


if __name__ == "__main__":
    sys.exit(main())
