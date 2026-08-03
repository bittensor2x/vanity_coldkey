# Bittensor vanity coldkey generator

Brute-force search for a Bittensor coldkey (SS58 network format `42`) whose address matches a chosen **prefix** and **suffix**.

Default target:

```text
5Ev3R...............................rDEND
```

Matching is **case-insensitive on letters** by default (digits still must match exactly). Use `--case-sensitive` / `CASE_SENSITIVE=1` for an exact match (much harder).

## Requirements

- Python 3.10+
- [PM2](https://pm2.keymetrics.io/) (optional, recommended for background runs)

## Install

```bash
cd vanity_coldkey
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## CPU usage

Worker count is **auto-detected from the machine's CPU cores** — no setting needed. It spawns one worker process per core.

If you ever want to override it (e.g. leave a core free for other work), pass `--cpu N`:

```bash
python search.py --cpu 8
```

## Run with PM2 (recommended)

`ecosystem.config.js` only needs `PREFIX` / `SUFFIX` (CPU is automatic):

```js
env: {
  PREFIX: "Ev3R",
  SUFFIX: "rDEND",
}
```

Then:

```bash
pm2 start ecosystem.config.js
pm2 status
pm2 logs vanity-coldkey
pm2 restart vanity-coldkey
pm2 stop vanity-coldkey
pm2 delete vanity-coldkey
pm2 save                    # persist process list
pm2 startup                 # optional: start on reboot
```

PM2 runs **one** Python process; that process auto-spawns one worker per CPU core (do not set PM2 `instances` > 1).

On a match (or clean stop), the process exits with code `0` and PM2 **does not** autorestart (`stop_exit_codes: [0]`). Crashes still restart.

To search again after a hit:

```bash
rm found.jsonl STOP
pm2 restart vanity-coldkey
```

## Run without PM2

```bash
# Auto-detected CPU cores
python search.py

# Custom pattern / explicit worker count
python search.py --prefix Ev3R --suffix rDEND --cpu 8
```

Stop:

```bash
touch STOP
# or Ctrl+C / kill the process (SIGTERM also writes STOP)
```

## Where a match is saved

On a full match, the worker appends one JSON line to **`found.jsonl`**, then creates `STOP` so all workers exit.

Example line:

```json
{
  "address": "5Ev3R...rDEND",
  "seed_hex": "<64 hex chars = 32-byte seed>",
  "public_key_hex": "<public key hex>",
  "found_by_worker": 3,
  "found_at": 1735689600.0,
  "prefix": "ev3r",
  "suffix": "rdend",
  "case_insensitive": true
}
```

Import into a Bittensor wallet (exact `btcli` flags can vary by version):

```bash
btcli wallet regen_coldkey --seed <seed_hex>
```

**Security:** `found.jsonl` contains private key material. It is gitignored — never commit or publish it.

## Telegram notification on match

Get a match alert the moment `search.py` finds one:

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Add the bot to your channel/group as an **admin** (needed to post).
3. Get the chat id — for a channel it looks like `-100xxxxxxxxxx` (e.g. via [@userinfobot](https://t.me/userinfobot) or the `getUpdates` API).
4. Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

```env
TELEGRAM_BOT_TOKEN=123456:ABC-your-token
TELEGRAM_CHAT_ID=-1001234567890
```

`search.py` auto-loads `.env` from this directory at startup (existing environment variables always win, so PM2/shell env can still override it). No PM2 config changes needed — and no secrets end up in the git-tracked `ecosystem.config.js`.

Test your credentials any time:

```bash
.venv/bin/python notify.py "test message"
```

On a match, you'll get a message with the address and pattern. **The seed hex is *not* included by default** — Telegram isn't a secure place to store a private key. It stays in `found.jsonl` on the host. Set `TELEGRAM_INCLUDE_SEED=1` in `.env` to include it anyway (not recommended).

If Telegram isn't configured, the search still works exactly the same — it just skips the notification and logs that it did.

## Progress files (local only)

| Path | Purpose |
|------|---------|
| `progress.json` | Attempts / rate / best partial match |
| `stats/worker_*.json` | Per-worker heartbeats |
| `STOP` | Presence of this file stops all workers |

These are gitignored. With PM2, logs also go to `~/.pm2/logs/`.

## Difficulty (rough)

Bittensor addresses are 48-character SS58 strings that always start with `5`. Matching more characters grows exponentially. A full 9-character case-insensitive pattern can take a very long time — expect partial “best” scores in `progress.json` long before a full hit.

## Project layout

```text
vanity_coldkey/
├── README.md
├── requirements.txt
├── search.py              # main miner
├── notify.py              # Telegram notifier (stdlib only)
├── diagnose_scaling.py    # CPU scaling diagnostic tool
├── ecosystem.config.js    # PM2
├── .env.example
├── .env                   # your real secrets (gitignored, not committed)
├── .gitignore
└── .venv/                 # local virtualenv (not committed)
```
