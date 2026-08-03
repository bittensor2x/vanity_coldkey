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

import notify

BASE_DIR = Path(__file__).resolve().parent
STATS_DIR = BASE_DIR / "stats"
STOP_FILE = BASE_DIR / "STOP"
FOUND_FILE = BASE_DIR / "found.jsonl"
PROGRESS_FILE = BASE_DIR / "progress.json"
ENV_FILE = BASE_DIR / ".env"

HEARTBEAT_EVERY = 3000
SS58_FORMAT = 42  # Bittensor


def load_dotenv(path: Path) -> None:
    """Tiny .env loader (stdlib only). Existing env vars always win, so PM2's
    `env` block or the shell can still override anything in the file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def notify_found(found_jsonl_text: str) -> None:
    """Send a Telegram alert for the last match in found.jsonl, if configured."""
    if not notify.is_configured():
        print(
            "Telegram not configured (set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
            "in .env) — skipping notification.",
            flush=True,
        )
        return

    lines = [ln for ln in found_jsonl_text.splitlines() if ln.strip()]
    if not lines:
        return
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return

    include_seed = os.environ.get("TELEGRAM_INCLUDE_SEED", "").lower() in ("1", "true", "yes")
    text_lines = [
        "🎉 <b>Bittensor vanity coldkey FOUND</b>",
        f"Address: <code>{data.get('address', '?')}</code>",
        f"Pattern: 5{data.get('prefix', '?')}...{data.get('suffix', '?')}",
    ]
    if include_seed:
        text_lines.append(f"Seed (hex): <code>{data.get('seed_hex', '?')}</code>")
    else:
        text_lines.append(
            "Seed hex is saved locally in <code>found.jsonl</code> on the host "
            "(not included here — set TELEGRAM_INCLUDE_SEED=1 to include it, "
            "not recommended)."
        )
    ok = notify.send_telegram_message("\n".join(text_lines))
    print(f"Telegram notification {'sent' if ok else 'FAILED'}.", flush=True)


def cpu_diagnostics() -> dict:
    """Report every CPU-count signal we can find, to explain scaling surprises.

    - os_cpu_count: total logical CPUs the OS reports (ignores cgroup quotas).
    - affinity_count: CPUs this process is actually allowed to run on
      (respects `--cpuset-cpus` / `taskset`, but NOT `--cpus=N` quotas).
    - cgroup_quota_cpus: effective CPU budget from a cgroup v1/v2 CFS quota
      (e.g. Docker `--cpus=16`, Kubernetes `resources.limits.cpu`), which
      silently throttles far below `os_cpu_count` without changing affinity.
    """
    info: dict = {"os_cpu_count": os.cpu_count()}

    try:
        info["affinity_count"] = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except AttributeError:
        info["affinity_count"] = None

    info["cgroup_quota_cpus"] = None
    try:
        quota_raw, period_raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota_raw != "max":
            info["cgroup_quota_cpus"] = round(int(quota_raw) / int(period_raw), 2)
    except (OSError, ValueError):
        try:
            quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if quota > 0:
                info["cgroup_quota_cpus"] = round(quota / period, 2)
        except (OSError, ValueError):
            pass

    return info


def detect_cpu() -> int:
    """Auto-detect worker count from CPUs actually available to this process.

    Prefers the sched affinity mask (correct under `--cpuset-cpus`/`taskset`)
    over the raw OS core count (which ignores such restrictions).
    """
    try:
        affinity = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
        if affinity > 0:
            return affinity
    except AttributeError:
        pass
    return os.cpu_count() or 4


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
            found_text = FOUND_FILE.read_text(encoding="utf-8")
            print(found_text, flush=True)
            notify_found(found_text)
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
        default=None,
        help="Worker count. Default: auto-detected CPU cores "
        f"({detect_cpu()} on this machine).",
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
    load_dotenv(ENV_FILE)
    args = parse_args()
    cpu = args.cpu if args.cpu and args.cpu > 0 else detect_cpu()
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
    diag = cpu_diagnostics()
    print(
        f"Starting vanity search with CPU={cpu}. "
        f"Target: {display_prefix}...{args.suffix} ({mode}). "
        f"Match file: {FOUND_FILE}",
        flush=True,
    )
    print(
        f"CPU diagnostics: os_cpu_count={diag['os_cpu_count']} "
        f"affinity_count={diag['affinity_count']} "
        f"cgroup_quota_cpus={diag['cgroup_quota_cpus']}",
        flush=True,
    )
    quota = diag["cgroup_quota_cpus"]
    if quota is not None and quota < cpu:
        print(
            f"WARNING: this process is CPU-throttled to ~{quota} cores by a "
            f"cgroup quota (Docker --cpus, Kubernetes cpu limit, etc.), but "
            f"{cpu} workers were started. Extra workers beyond ~{quota:.0f} "
            f"will just add contention, not throughput. Set --cpu to match "
            f"the quota for best results.",
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
