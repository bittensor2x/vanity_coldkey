#!/usr/bin/env python3
"""Minimal Telegram notifier (stdlib only — no extra dependency).

Config (env vars, typically via a local `.env` file — see .env.example):
  TELEGRAM_BOT_TOKEN     bot token from @BotFather
  TELEGRAM_CHAT_ID       channel/chat id, e.g. -100xxxxxxxxxx (or a user id)

Standalone test:
    .venv/bin/python notify.py "hello from vanity_coldkey"
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SEC = 10.0


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(
        os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHANNEL_ID")
    )


def send_telegram_message(
    text: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout: float = TIMEOUT_SEC,
) -> bool:
    """Best-effort send. Returns True on success, False otherwise (never raises)."""
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHANNEL_ID")
    if not bot_token or not chat_id:
        print(
            "Telegram not configured (need TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).",
            file=sys.stderr,
            flush=True,
        )
        return False

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        API_URL.format(token=bot_token),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        print(f"Telegram notify failed: {exc}", file=sys.stderr, flush=True)
        return False

    if not body.get("ok"):
        print(f"Telegram API error: {body}", file=sys.stderr, flush=True)
        return False
    return True


if __name__ == "__main__":
    message = " ".join(sys.argv[1:]) or "Test notification from vanity_coldkey/notify.py"
    success = send_telegram_message(message)
    print("Sent." if success else "Failed — check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
    sys.exit(0 if success else 1)
