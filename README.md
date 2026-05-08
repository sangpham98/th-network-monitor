# TH Network Monitor

Web GUI nội bộ để monitor WAN/DNS và IP Tunnel cho ~500 cửa hàng TH Truemart, import inventory từ Excel, quản lý incidents và gửi cảnh báo Telegram.

## Governance

[ARCHITECTURE.md](ARCHITECTURE.md) là tài liệu canonical cho kiến trúc và workflow.

Nguyên tắc khi phát triển:

- Không tự ý đổi stack, database, service layout, monitor logic, alert flow hoặc import workflow.
- Nếu thay đổi có thể ảnh hưởng kiến trúc/workflow, phải phân tích và xác nhận trước.
- Code thay đổi behavior phải đi kèm test hoặc manual verification rõ ràng.
- Không commit runtime/cache/secrets: `.env`, `.pytest_cache/`, DB, upload files, logs, `__pycache__`.

## Stack

- FastAPI + Jinja2 GUI
- SQLite + SQLAlchemy ORM
- Async monitor worker + system `ping`
- Telegram Bot API
- pandas/openpyxl cho Excel import
- systemd deployment

## Main workflow

```text
Excel inventory
  → GUI import preview/confirm
  → SQLite stores + status + incidents
  → monitor.worker hoặc /monitor/run-once
  → cross-process monitor lock
  → WAN/DNS + IP Tunnel check with retry
  → DOWN_THRESHOLD / UP_THRESHOLD
  → incident open/update/resolve
  → Telegram alert/recovery batching
  → Dashboard / Stores / Incidents GUI
```

## Project layout

```text
app/              FastAPI app, auth, config, DB, reports, backups
web/templates/    Jinja2 pages
web/static/       CSS
monitor/          checker, status engine, worker
alerts/           Telegram sender
importers/        Excel importer
systemd/          service + logrotate samples
scripts/          helper scripts
tests/            pytest suite
data/             runtime SQLite DB/uploads/backups (not committed)
logs/             runtime logs (not committed)
```

## One-command install

Recommended production install from a local checkout on a systemd Linux host:

```bash
sudo scripts/install.sh
```

If the target machine only has a repository URL, install with a bootstrap one-liner:

```bash
git clone <REPO_URL> /tmp/th-network-monitor && sudo /tmp/th-network-monitor/scripts/install.sh
```

Or, if `scripts/bootstrap.sh` is hosted at a raw URL:

```bash
curl -fsSL <BOOTSTRAP_RAW_URL> | sudo bash -s -- <REPO_URL>
```

The bootstrap script accepts an optional checkout directory:

```bash
sudo scripts/bootstrap.sh <REPO_URL> [/tmp/th-network-monitor-bootstrap]
```

Install prerequisites for a minimal Debian/Ubuntu host:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-dev rsync iputils-ping curl build-essential
```

The `build-essential` package provides gcc/g++ needed to compile pandas and other native dependencies. `python3-dev` provides Python headers.

Installed layout:

```text
/opt/th-network-monitor          application code + .venv
/etc/th-network-monitor/.env     runtime configuration
/var/lib/th-network-monitor      SQLite DB, uploads, previews, backups, lock
/var/log/th-network-monitor      optional file logs
/usr/local/bin/thnm              service helper
```

Useful commands:

```bash
thnm status
thnm logs
thnm edit-config
thnm restart
thnm run-once
thnm backup
```

The installer preserves an existing `/etc/th-network-monitor/.env` and never deletes `/var/lib/th-network-monitor`. Installed services set `THNM_ENV_FILE=/etc/th-network-monitor/.env`; local runs use `.env` unless `THNM_ENV_FILE` is explicitly set.

The install script enables and starts both services immediately:

```text
th-network-monitor-web.service     uvicorn app.main:app on APP_PORT
th-network-monitor-worker.service  python -m monitor.worker periodic loop
```

So a production install is a one-command app setup: after `sudo scripts/install.sh`, the GUI and periodic monitor worker should both be running.

## Current capability summary

Implemented and verified capabilities:

- One-command systemd install with dedicated `thnm` service user, runtime directories, virtualenv, config preservation, web service, worker service, logrotate, and `thnm` helper command.
- Auth-protected FastAPI/Jinja2 web GUI for dashboard, stores, store detail, import, incidents, backups, Telegram test, and manual monitor run.
- Excel import preview/confirm flow with column normalization, duplicate detection, safe optional-field handling, and SQLite backup before large imports.
- Periodic async monitor worker with cross-process lock, max concurrency, ping retry, status counters, incident open/update/resolve, and Telegram alert/recovery batching.
- Dashboard/Stores display `Last Check` from the database in configured local timezone; pages refresh on request, not realtime websocket polling.
- Manual **Check now** runs `/monitor/run-once`; when submitted from the GUI it redirects back to the current page after completion, while direct API calls still receive JSON.
- SQLite backup/restore UI and Excel incident export.
- Pytest coverage for auth, store operations, import safety, status thresholds, worker lock, alert batching, backup/restore, and incident export.

## Local development setup

```bash
cd /home/phamsang/Documents/th-network-monitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-this-password
SESSION_SECRET=change-this-random-secret
```

Auth mặc định bật bằng `AUTH_ENABLED=true`. Chỉ đặt `AUTH_ENABLED=false` khi chạy dev/local tin cậy.

## Run web GUI

```bash
. .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open:

```text
http://localhost:8080
```

## Run monitor worker

```bash
. .venv/bin/activate
python -m monitor.worker
```

Manual run once from Python:

```bash
python -c "import asyncio; from monitor.worker import run_once; print(asyncio.run(run_once()))"
```

Manual run once from GUI/API requires login:

```text
POST /monitor/run-once
```

## GUI features

- Dashboard: total stores, count by status, top 100 stores.
- Stores: search/filter, current status, store detail link, delete store when needed.
- Store detail: inventory, status, recent incidents.
- Incidents: list/filter/export Excel.
- GUI timestamps display in `TIMEZONE` (default `Asia/Ho_Chi_Minh`) while DB timestamps are stored as UTC-naive values.
- Import: Excel preview + confirm/cancel.
- Backups: SQLite backup/restore UI.
- Admin actions: Telegram test and manual monitor run.

## Import Excel

Supported columns:

- `Mã CH`, `Ma CH`, `Store Code`
- `PC Name`
- `IP Local`
- `IP tunel`, `IP Tunnel`
- `WAN DNS`, `DNS`, `Domain`
- `Miền`, `Mien`
- `Khu vực`, `Khu vuc`
- `Địa chỉ`, `Dia chi`

Import safety:

- Store code must be 7-8 digits and start with `70000`.
- Duplicate store codes in one Excel file are reported before confirm.
- Missing optional columns and blank cells do not clear existing values.
- Large imports create a SQLite backup before commit.

Create sample Excel:

```bash
python scripts/create_sample_excel.py
```

## Status logic

Statuses:

- `UP`
- `WAN_DOWN`
- `TUNNEL_DOWN`
- `DOWN`
- `UNKNOWN`

Rules:

- `PING_RETRY` is applied per target.
- `DOWN_THRESHOLD` controls when incidents open/update.
- `UP_THRESHOLD` controls when incidents recover.
- Each store should have at most one active `OPEN` incident.
- Telegram sent flags are marked only after Telegram send succeeds.
- Each worker cycle retries old `OPEN` incidents with `alert_sent=false` when Telegram is configured.

## Tests

```bash
. .venv/bin/activate
python -m compileall app monitor alerts importers scripts tests
python -m pytest
```

## systemd deployment

Use the installer for production systemd deployment:

```bash
sudo scripts/install.sh
```

Logs:

```bash
thnm logs
thnm web-logs
thnm worker-logs
```

## Ops notes

- SQLite is acceptable for the current scale; production must use WAL + busy timeout.
- If Telegram is noisy, increase `DOWN_THRESHOLD`, `UP_THRESHOLD`, or `MONITOR_INTERVAL_SECONDS`.
- For larger future workloads, consider PostgreSQL + Alembic only after approval.
- Runtime config is explicit: installed services use `/etc/th-network-monitor/.env`; local/dev uses repo `.env`.
