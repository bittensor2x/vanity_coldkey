#!/usr/bin/env python3
"""Bittensor (SS58 format 42) vanity coldkey search."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import signal
import time
from pathlib import Path

from substrateinterface import Keypair, KeypairType

BASE_DIR = Path(__file__).resolve().parent
STATS_DIR = BASE_DIR / "stats"
STOP_FILE = BASE_DIR / "STOP"
FOUND_FILE = BASE_DIR / "found.jsonl"
PROGRESS_FILE = BASE_DIR / "progress.json"

# Simple fixed CPU presets (also set via env CPU=16)
ALLOWED_CPUS = (4, 8, 16, 32, 64)

HEARTBEAT_EVERY = 3000
SS58_FORMAT = 42  # Bittensor


def default_cpu() -> int:
    """Largest allowed preset that fits this machine."""
    available = os.cpu_count() or 4
    fits = [c for c in ALLOWED_CPUS if c <= available]
    return fits[-1] if fits else ALLOWED_CPUS[0]


def resolve_cpu(value: int | str | None) -> int:
    if value is None or value == "":
        raw = os.environ.get("CPU") or os.environ.get("WORKERS")
        if raw is None or raw == "":
            return default_cpu()
        value = raw
    n = int(value)
    if n not in ALLOWED_CPUS:
        raise SystemExit(f"CPU must be one of {list(ALLOWED_CPUS)}, got {n}")
    return n


def normalize_target(prefix: str, suffix: str, case_insensitive: bool) -> tuple[str, str]:
    """Strip the fixed leading '5' from prefix if the user included it."""
    if prefix.startswith("5"):
        prefix = prefix[1:]
    if case_insensitive:
        return prefix.lower(), suffix.lower()
    return prefix, suffix


def score(address: str, prefix: str, suffix: str, case_insensitive: bool) -> int:
    pre = address[1 : 1 + len(prefix)]
    suf = address[-len(suffix) :] if suffix else ""
    if case_insensitive:
        pre, suf = pre.lower(), suf.lower()
    matched = sum(1 for a, b in zip(pre, prefix) if a == b)
    matched += sum(1 for a, b in zip(suf, suffix) if a == b)
    return matched


def is_full_match(address: str, prefix: str, suffix: str, case_insensitive: bool) -> bool:
    pre = address[1 : 1 + len(prefix)]
    suf = address[-len(suffix) :] if suffix else ""
    if case_insensitive:
        pre, suf = pre.lower(), suf.lower()
    return pre == prefix and suf == suffix


def worker(
    idx: int,
    prefix: str,
    suffix: str,
    case_insensitive: bool,
    max_score: int,
) -> None:
    count = 0
    best_score = -1
    best_address: str | None = None
    stats_path = STATS_DIR / f"worker_{idx}.json"
    last_write = time.time()

    while True:
        if STOP_FILE.exists():
            break

        seed = os.urandom(32)
        kp = Keypair.create_from_seed(
            seed, ss58_format=SS58_FORMAT, crypto_type=KeypairType.SR25519
        )
        addr = kp.ss58_address
        count += 1

        sc = score(addr, prefix, suffix, case_insensitive)
        if sc > best_score:
            best_score = sc
            best_address = addr

        if is_full_match(addr, prefix, suffix, case_insensitive):
            with FOUND_FILE.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "address": addr,
                            "seed_hex": seed.hex(),
                            "public_key_hex": kp.public_key.hex(),
                            "found_by_worker": idx,
                            "found_at": time.time(),
                            "prefix": prefix,
                            "suffix": suffix,
                            "case_insensitive": case_insensitive,
                        }
                    )
                    + "\n"
                )
            stats_path.write_text(
                json.dumps(
                    {
                        "idx": idx,
                        "count": count,
                        "best_score": best_score,
                        "best_address": best_address,
                        "max_score": max_score,
                        "ts": time.time(),
                        "done": True,
                    }
                ),
                encoding="utf-8",
            )
            STOP_FILE.touch()
            return

        now = time.time()
        if count % HEARTBEAT_EVERY == 0 or now - last_write > 10:
            stats_path.write_text(
                json.dumps(
                    {
                        "idx": idx,
                        "count": count,
                        "best_score": best_score,
                        "best_address": best_address,
                        "max_score": max_score,
                        "ts": now,
                        "done": False,
                    }
                ),
                encoding="utf-8",
            )
            last_write = now


def monitor(n_workers: int, start_time: float, max_score: int) -> None:
    while True:
        time.sleep(15)
        total = 0
        best_score = -1
        best_address = None
        alive = 0

        for i in range(n_workers):
            path = STATS_DIR / f"worker_{i}.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            total += data.get("count", 0)
            alive += 1
            if data.get("best_score", -1) > best_score:
                best_score = data["best_score"]
                best_address = data.get("best_address")

        elapsed = time.time() - start_time
        rate = total / elapsed if elapsed > 0 else 0.0
        progress = {
            "elapsed_sec": round(elapsed, 1),
            "total_attempts": total,
            "rate_per_sec": round(rate, 1),
            "best_score": best_score,
            "max_score": max_score,
            "best_address_so_far": best_address,
            "workers_reporting": alive,
            "cpu": n_workers,
        }
        PROGRESS_FILE.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
        print(
            f"[progress] cpu={n_workers} attempts={total:,} rate={rate:,.0f}/s "
            f"elapsed={elapsed / 3600:.2f}h best={best_score}/{max_score} "
            f"best_addr={best_address}",
            flush=True,
        )

        if FOUND_FILE.exists():
            print("FOUND: match written to found.jsonl", flush=True)
            print(FOUND_FILE.read_text(encoding="utf-8"), flush=True)
            return
        if STOP_FILE.exists():
            print("STOPPED (STOP file present).", flush=True)
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Bittensor vanity coldkey (SS58 format 42)."
    )
    parser.add_argument(
        "--cpu",
        type=int,
        choices=ALLOWED_CPUS,
        default=None,
        help=f"Worker count. One of {list(ALLOWED_CPUS)}. "
        f"Env: CPU=16. Default: largest preset ≤ machine cores ({default_cpu()} here).",
    )
    parser.add_argument(
        "--prefix",
        default=os.environ.get("PREFIX", "Ev3R"),
        help="Chars after the fixed leading '5' (default/env PREFIX: Ev3R).",
    )
    parser.add_argument(
        "--suffix",
        default=os.environ.get("SUFFIX", "rDEND"),
        help="Trailing address characters (default/env SUFFIX: rDEND).",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        default=os.environ.get("CASE_SENSITIVE", "").lower() in ("1", "true", "yes"),
        help="Exact case match (much harder). Env: CASE_SENSITIVE=1.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cpu = resolve_cpu(args.cpu)
    case_insensitive = not args.case_sensitive
    prefix, suffix = normalize_target(args.prefix, args.suffix, case_insensitive)
    max_score = len(prefix) + len(suffix)

    # Exit 0 on purpose so PM2 (stop_exit_codes) does not autorestart.
    if FOUND_FILE.exists():
        print(
            f"{FOUND_FILE.name} already exists — remove it to search again.\n"
            f"{FOUND_FILE.read_text(encoding='utf-8')}",
            flush=True,
        )
        raise SystemExit(0)

    STATS_DIR.mkdir(parents=True, exist_ok=True)
    if STOP_FILE.exists():
        STOP_FILE.unlink()

    display_prefix = f"5{args.prefix[1:] if args.prefix.startswith('5') else args.prefix}"
    mode = "case-sensitive" if args.case_sensitive else "case-insensitive"
    print(
        f"Starting vanity search with CPU={cpu}. "
        f"Target: {display_prefix}...{args.suffix} ({mode}). "
        f"Match file: {FOUND_FILE}",
        flush=True,
    )

    def request_stop(signum, _frame) -> None:
        print(f"Signal {signum} — writing STOP.", flush=True)
        STOP_FILE.touch()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    start_time = time.time()
    procs = [
        mp.Process(
            target=worker,
            args=(i, prefix, suffix, case_insensitive, max_score),
            daemon=True,
        )
        for i in range(cpu)
    ]
    for p in procs:
        p.start()

    try:
        monitor(cpu, start_time, max_score)
    except KeyboardInterrupt:
        STOP_FILE.touch()

    for p in procs:
        p.join(timeout=5)
    for p in procs:
        if p.is_alive():
            p.terminate()

    # Clean exit → PM2 will not restart (see stop_exit_codes in ecosystem.config.js)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
