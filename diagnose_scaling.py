#!/usr/bin/env python3
"""
Diagnose why worker count doesn't scale throughput linearly.

Runs two short benchmarks at increasing process counts:
  1. raw_urandom  — os.urandom(32) only (isolates RNG syscall contention)
  2. full_keypair — os.urandom(32) + sr25519 keypair derivation (the real workload)

If (1) scales linearly but (2) doesn't -> the sr25519/EC math itself is the
bottleneck (expected on hyperthreaded or oversubscribed/shared-core hosts).

If (1) ALSO fails to scale -> the RNG source itself is contended/throttled
(common on some virtualized/cloud kernels), independent of the crypto work.

Also prints `vmstat`/`mpstat`-style CPU steal time if available, since high
steal time means the hypervisor isn't actually giving this VM the cores it
appears to have.

Usage:
    .venv/bin/python diagnose_scaling.py
    .venv/bin/python diagnose_scaling.py --seconds 5 --counts 1,4,8,16,32
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import subprocess
import time

from substrateinterface import Keypair, KeypairType


def raw_urandom_worker(seconds: float, q: mp.Queue) -> None:
    count = 0
    end = time.time() + seconds
    while time.time() < end:
        os.urandom(32)
        count += 1
    q.put(count)


def full_keypair_worker(seconds: float, q: mp.Queue) -> None:
    count = 0
    end = time.time() + seconds
    while time.time() < end:
        kp = Keypair.create_from_seed(
            os.urandom(32), ss58_format=42, crypto_type=KeypairType.SR25519
        )
        _ = kp.ss58_address
        count += 1
    q.put(count)


def run(target, n: int, seconds: float) -> float:
    q: mp.Queue = mp.Queue()
    procs = [mp.Process(target=target, args=(seconds, q)) for _ in range(n)]
    t0 = time.time()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    elapsed = time.time() - t0
    total = sum(q.get() for _ in procs)
    return total / elapsed


def print_steal_time() -> None:
    try:
        out = subprocess.run(
            ["vmstat", "1", "2"], capture_output=True, text=True, timeout=5
        ).stdout
        print("\nvmstat 1 2 (last line's last column ~= %st, CPU steal time):")
        print(out.strip())
        print(
            "(If st is consistently > a few percent under load, the "
            "hypervisor is not giving this VM the cores it claims to have.)"
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        print("\n(vmstat not available — skip steal-time check; try `top` and look at %st)")


def warn_if_search_running() -> None:
    """A live search.py already saturates every core; running this diagnostic
    alongside it will produce misleadingly bad scaling numbers."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", "search.py"], capture_output=True, text=True, timeout=5
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return
    hits = [
        line for line in out.splitlines()
        if "search.py" in line and "diagnose_scaling.py" not in line
    ]
    if hits:
        print(
            "WARNING: search.py appears to already be running (see below). "
            "It is saturating your CPUs right now, so this benchmark's numbers "
            "will look artificially bad. Stop it first (`pm2 stop vanity-coldkey` "
            "or `touch STOP`), then re-run this diagnostic.\n"
        )
        for line in hits:
            print(f"  {line}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=4.0, help="seconds per data point")
    ap.add_argument(
        "--counts",
        default="1,4,8,16,32",
        help="comma-separated worker counts to test",
    )
    args = ap.parse_args()
    counts = [int(c) for c in args.counts.split(",") if c.strip()]

    warn_if_search_running()
    print(f"os.cpu_count() = {os.cpu_count()}")
    try:
        print(f"sched_getaffinity count = {len(os.sched_getaffinity(0))}")
    except AttributeError:
        pass

    print("\n--- Stage 1: raw os.urandom(32) only (isolates RNG contention) ---")
    raw_rates = {}
    for n in counts:
        rate = run(raw_urandom_worker, n, args.seconds)
        raw_rates[n] = rate
        print(f"n={n:3d}: {rate:>12,.0f} calls/s  ({rate/n:>10,.0f}/worker)")

    print("\n--- Stage 2: full sr25519 keypair generation (the real workload) ---")
    kp_rates = {}
    for n in counts:
        rate = run(full_keypair_worker, n, args.seconds)
        kp_rates[n] = rate
        print(f"n={n:3d}: {rate:>12,.0f} keys/s   ({rate/n:>10,.0f}/worker)")

    base = min(counts)
    print("\n--- Scaling efficiency vs smallest count (ideal = 1.00) ---")
    print(f"{'n':>4} {'raw_urandom':>14} {'full_keypair':>14}")
    for n in counts:
        raw_eff = (raw_rates[n] / raw_rates[base]) / (n / base)
        kp_eff = (kp_rates[n] / kp_rates[base]) / (n / base)
        print(f"{n:>4} {raw_eff:>13.2f}x {kp_eff:>13.2f}x")

    print(
        "\nInterpretation:\n"
        "  raw_urandom scales well, full_keypair doesn't  -> CPU/EC-math bound\n"
        "                                                    (SMT siblings or\n"
        "                                                    oversubscribed/shared\n"
        "                                                    vCPUs fighting over\n"
        "                                                    real execution units)\n"
        "  raw_urandom ALSO fails to scale                -> RNG source itself is\n"
        "                                                    contended/throttled\n"
        "                                                    on this kernel/VM"
    )

    print_steal_time()


if __name__ == "__main__":
    main()
