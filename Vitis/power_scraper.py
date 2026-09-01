"""Read and sample the U280's electrical telemetry. Python 3.6, stdlib only.

Wraps `xbutil examine -d <BDF> --report electrical` and turns it into numbers.
Adapted from the reference power_scraper the professor supplied; two deliberate
departures:

  * PARSING IS KEY-BASED, NOT LINE-INDEXED. The reference reads lines[5], [6],
    [7] and everything past index 9. Those offsets happen to match XRT 2.13 on
    this machine, but a single extra header line would silently turn "Max Power"
    into a voltage. Matching names costs nothing and cannot mis-attribute.

  * NO CPU/pyJoules SIDE. In the reference that half is a stub returning a
    hardcoded 12.3 W. More to the point, host power cannot discriminate dense
    from sparse: the host does identical work in both cases -- same movers, same
    transfers, same OpenCL calls. Reporting it would add a number that looks
    like evidence and is not.

WHAT THE CARD ACTUALLY REPORTS (XRT 2.13, U280, verified 2026-08-27)
Fourteen rails, but only THREE carry a current reading, so only three yield
power:

    12 Volts Auxillary      12.308 V x 1.511 A = 18.60 W
    12 Volts PCI Express    12.337 V x 1.382 A = 17.05 W
    Internal FPGA Vcc        0.851 V x 11.310 A =  9.61 W   <- VCCINT

    reported "Power"                            = 35.647 W

18.60 + 17.05 = 35.65, which is the reported Power exactly. So:

    BOARD POWER  = the two 12 V input rails. Everything on the card: FPGA, HBM
                   stacks, transceivers, regulator losses, fans.
    VCCINT       = the FPGA core rail, DOWNSTREAM of those regulators. It is a
                   SUBSET of board power, NOT an additional term. Never sum the
                   three.

Both are worth reporting. Board power is what a user pays; VCCINT is what the
design itself burns, with HBM and transceiver draw -- identical between dense and
sparse -- largely excluded. A dense-vs-sparse difference will show up far more
clearly in VCCINT than in the board total.

USAGE
    from power_scraper import read_once, Sampler
    print(read_once("0000:af:00.1"))

    s = Sampler("0000:af:00.1", interval=1.0); s.start()
    ...                                        # run the load
    s.stop()
    for t, d in s.samples: ...
"""

import re
import subprocess
import sys
import threading
import time

# "  Max Power              : 225 Watts"
RE_MAXP = re.compile(r"^\s*Max Power\s*:\s*([0-9.]+)")
# "  Power                  : 35.647122 Watts"   (anchored so "Max Power" cannot match)
RE_POW = re.compile(r"^\s*Power\s*:\s*([0-9.]+)")
RE_WARN = re.compile(r"^\s*Power Warning\s*:\s*(\S+)")
# "  Internal FPGA Vcc      :  0.851 V, 11.310 A"   or   "  Mgt Vtt : 1.200 V"
RE_RAIL = re.compile(r"^\s+(.+?)\s*:\s*([0-9.]+)\s*V(?:\s*,\s*([0-9.]+)\s*A)?\s*$")

VCCINT_RAIL = "Internal FPGA Vcc"


def parse_electrical(text):
    """xbutil electrical output -> dict. Raises if the essential fields are absent."""
    out = {"rails": {}}
    for line in text.splitlines():
        m = RE_MAXP.match(line)
        if m:
            out["max_power_w"] = float(m.group(1))
            continue
        m = RE_POW.match(line)
        if m:
            out["board_w"] = float(m.group(1))
            continue
        m = RE_WARN.match(line)
        if m:
            out["warning"] = m.group(1)
            continue
        m = RE_RAIL.match(line)
        if m:
            name = m.group(1).strip()
            if name in ("Power Rails",):        # the column header, not a rail
                continue
            rail = {"V": float(m.group(2))}
            if m.group(3) is not None:
                rail["A"] = float(m.group(3))
                rail["W"] = rail["V"] * rail["A"]
            out["rails"][name] = rail

    if "board_w" not in out:
        raise RuntimeError(
            "could not find a 'Power' line in the xbutil output -- the report format "
            "may have changed:\n" + text[:600])

    v = out["rails"].get(VCCINT_RAIL)
    out["vccint_w"] = v["W"] if (v and "W" in v) else None
    # Sanity: the two 12 V input rails should reconstruct the reported board power.
    # If they do not, something about the report changed and the numbers below
    # should not be trusted silently.
    twelve = sum(r["W"] for n, r in out["rails"].items()
                 if "W" in r and n.startswith("12 Volts"))
    out["rails_12v_w"] = twelve
    out["board_consistent"] = (abs(twelve - out["board_w"]) < 0.5) if twelve else False
    return out


def read_once(bdf, timeout=30):
    """One telemetry snapshot. ~1 s: xbutil is a process launch, not a register read."""
    cmd = ["xbutil", "examine", "-d", bdf, "--report", "electrical"]
    try:
        if sys.version_info >= (3, 7):
            r = subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True,
                               timeout=timeout)
        else:
            r = subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("'{}' failed (code {}):\n{}".format(
            " ".join(cmd), e.returncode, e.output))
    return parse_electrical(r.stdout)


class Sampler(object):
    """Background telemetry sampler.

    Every sample carries the wall-clock time it was TAKEN, so readings can later
    be clipped to the exact load window the host reports. That matters: a run
    spends most of its wall time in H2D transfer and setup, and averaging those
    in would understate compute power badly.

    xbutil takes roughly a second per call, so ~1 Hz is the practical ceiling.
    Over a 60 s soak that is ~60 samples, which is plenty for a mean but far too
    few to resolve anything transient -- this measures steady state only.
    """

    def __init__(self, bdf, interval=1.0):
        self.bdf = bdf
        self.interval = interval
        self.samples = []          # list of (epoch_seconds, parsed_dict)
        self.errors = 0
        self._stop = threading.Event()
        self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                d = read_once(self.bdf)
                self.samples.append((time.time(), d))
            except Exception:
                self.errors += 1
            # Sleep only the remainder: xbutil itself already ate ~1 s, so a flat
            # sleep(interval) would halve the sample rate.
            rest = self.interval - (time.time() - t0)
            if rest > 0:
                self._stop.wait(rest)

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=40)

    def window(self, t0, t1):
        """Samples taken within [t0, t1]."""
        return [(t, d) for (t, d) in self.samples if t0 <= t <= t1]


def summarise(samples):
    """Mean / min / max board and VCCINT power over a list of (t, dict)."""
    if not samples:
        return None
    board = [d["board_w"] for _, d in samples]
    vcc = [d["vccint_w"] for _, d in samples if d.get("vccint_w") is not None]
    vcc_a = [d["rails"][VCCINT_RAIL]["A"] for _, d in samples
             if VCCINT_RAIL in d["rails"] and "A" in d["rails"][VCCINT_RAIL]]
    def sd(xs):
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5

    out = {
        "n": len(samples),
        "board_mean": sum(board) / len(board),
        "board_min": min(board),
        "board_max": max(board),
        # Standard deviation and standard error, because the card's telemetry is
        # noisy relative to the signal: a 3 W dynamic delta sits under a ~4 W
        # sample-to-sample spread. The MEAN is fine -- error falls as 1/sqrt(n),
        # so ~60 samples gets it to a few hundred mW -- but the spread has to be
        # reported or the precision is being overstated.
        "board_sd": sd(board),
        "board_se": sd(board) / (len(board) ** 0.5) if board else 0.0,
    }
    if vcc:
        out.update({"vccint_mean": sum(vcc) / len(vcc),
                    "vccint_min": min(vcc), "vccint_max": max(vcc),
                    "vccint_sd": sd(vcc),
                    "vccint_se": sd(vcc) / (len(vcc) ** 0.5)})
    if vcc_a:
        out["vccint_a_mean"] = sum(vcc_a) / len(vcc_a)
    return out


if __name__ == "__main__":
    bdf = sys.argv[1] if len(sys.argv) > 1 else "0000:af:00.1"
    d = read_once(bdf)
    print("board power : {:.3f} W  (max {:.0f} W, warning={})".format(
        d["board_w"], d.get("max_power_w", 0), d.get("warning")))
    print("VCCINT      : {:.3f} W".format(d["vccint_w"] or 0.0))
    print("12 V rails  : {:.3f} W   consistent with board total: {}".format(
        d["rails_12v_w"], d["board_consistent"]))
    print("")
    for name in sorted(d["rails"]):
        r = d["rails"][name]
        if "W" in r:
            print("  {:<24} {:>7.3f} V {:>8.3f} A {:>8.3f} W".format(
                name, r["V"], r["A"], r["W"]))
        else:
            print("  {:<24} {:>7.3f} V".format(name, r["V"]))
