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
  → 4-of-5 DOWN window / UP_THRESHOLD recovery
  → incident open/update/resolve
  → double-check DOWN before Telegram batching
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

Run this on the target Linux machine and replace `change-this-strong-password` first:

```bash
sudo apt update && sudo apt install -y git python3 python3-venv python3-dev rsync iputils-ping curl build-essential && curl -fsSL https://raw.githubusercontent.com/sangpham98/th-network-monitor/main/scripts/bootstrap.sh | sudo ADMIN_PASSWORD='change-this-strong-password' bash -s -- https://github.com/sangpham98/th-network-monitor.git
```

Verify services:

```bash
thnm status
```

Both `th-network-monitor-web.service` and `th-network-monitor-worker.service` should show `active (running)`.

Access GUI at `http://<server-ip>:8080`. Default login:
- Username: `admin`
- Password: value passed as `ADMIN_PASSWORD` in the install command.

**Troubleshooting Python 3.14+**

If services fail with SQLAlchemy typing errors on Python 3.14+:

```bash
sudo -u thnm /opt/th-network-monitor/.venv/bin/pip uninstall -y sqlalchemy
sudo -u thnm /opt/th-network-monitor/.venv/bin/pip install 'sqlalchemy @ git+https://github.com/sqlalchemy/sqlalchemy.git@main'
sudo systemctl restart th-network-monitor-web th-network-monitor-worker
```

## Production deployment

Installed layout:

```text
/opt/th-network-monitor          application code + .venv
/etc/th-network-monitor/.env     runtime configuration
/var/lib/th-network-monitor      SQLite DB, uploads, previews, backups, lock
/var/log/th-network-monitor      optional file logs
/usr/local/bin/thnm              service helper
```

Services:

```text
th-network-monitor-web.service     uvicorn app.main:app on APP_PORT
th-network-monitor-worker.service  python -m monitor.worker periodic loop
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

Update an existing bootstrap install after changes are pushed to GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/sangpham98/th-network-monitor/main/scripts/bootstrap.sh | sudo bash -s -- https://github.com/sangpham98/th-network-monitor.git
sudo systemctl daemon-reload
sudo systemctl restart th-network-monitor-web th-network-monitor-worker
```

Get Telegram chat ID after setting `TELEGRAM_BOT_TOKEN`:

```bash
TOKEN=$(sudo grep '^TELEGRAM_BOT_TOKEN=' /etc/th-network-monitor/.env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

For direct bot messages, open the bot in Telegram, press **Start**, send `test`, then use the `chat.id` value from `getUpdates`. For group alerts, add the bot to the group, send `test` in the group, then use the group `chat.id` value; group IDs usually start with `-100`.

Set the chat ID and restart services:

```bash
sudo nano /etc/th-network-monitor/.env
sudo systemctl restart th-network-monitor-web th-network-monitor-worker
```

Test Telegram sending:

```bash
sudo -u thnm -H env THNM_ENV_FILE=/etc/th-network-monitor/.env PYTHONPATH=/opt/th-network-monitor /opt/th-network-monitor/.venv/bin/python -c 'import asyncio; from alerts.telegram import send_telegram; print(asyncio.run(send_telegram("THNM telegram test")))'
```

Uninstall completely:

```bash
sudo systemctl stop th-network-monitor-worker th-network-monitor-web
sudo systemctl disable th-network-monitor-worker th-network-monitor-web
sudo rm -f /etc/systemd/system/th-network-monitor-worker.service /etc/systemd/system/th-network-monitor-web.service /etc/logrotate.d/th-network-monitor /usr/local/bin/thnm
sudo systemctl daemon-reload
sudo rm -rf /opt/th-network-monitor /etc/th-network-monitor /var/lib/th-network-monitor /var/log/th-network-monitor
sudo userdel thnm 2>/dev/null || true
sudo groupdel thnm 2>/dev/null || true
```

The installer preserves an existing `/etc/th-network-monitor/.env` and never deletes `/var/lib/th-network-monitor`. Installed services set `THNM_ENV_FILE=/etc/th-network-monitor/.env`; local runs use `.env` unless `THNM_ENV_FILE` is explicitly set.

## Current capability summary

Implemented and verified capabilities:

- One-command systemd install with dedicated `thnm` service user, runtime directories, virtualenv, config preservation, web service, worker service, logrotate, and `thnm` helper command.
- Auth-protected FastAPI/Jinja2 web GUI for dashboard, stores, store detail, import, incidents, backups, Telegram test, and manual monitor run.
- Excel import preview/confirm flow with column normalization, duplicate detection, safe optional-field handling, and SQLite backup before large imports.
- Periodic async monitor worker with cross-process lock, max concurrency, ping retry, 4-of-5 down window, incident open/update/resolve, pre-alert double-check, and Telegram alert/recovery batching.
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
- `wan_status` and `tunnel_status` show the latest raw probe result.
- `overall_status` is the confirmed GUI/filter status, not the latest raw probe result.
- `DOWN_THRESHOLD` controls how many failures are required in the last 5 known checks; default `4`.
- A down incident opens/updates only when the target is currently failing and its 5-check window reaches `DOWN_THRESHOLD`.
- Pending raw failures keep the previous confirmed `overall_status` visible in dashboard/store tables.
- `UP_THRESHOLD` controls consecutive successful required-target checks before recovery; default `2`.
- DOWN alerts are double-checked before Telegram batching; if the recheck is UP, the alert is suppressed and retried next cycle if needed.
- Recovery notifications are sent only for incidents whose DOWN alert was sent successfully.
- Each store should have at most one active `OPEN` incident.
- Telegram sent flags are marked only after Telegram send succeeds.
- Each worker cycle retries old `OPEN` incidents with `alert_sent=false` when Telegram is configured.

## Tests

```bash
. .venv/bin/activate
python -m compileall app monitor alerts importers scripts tests
python -m pytest
```

## Ops notes

- SQLite is acceptable for the current scale; production must use WAL + busy timeout.
- If Telegram is noisy, increase `DOWN_THRESHOLD`, `PING_TIMEOUT_SECONDS`, `PING_RETRY`, or `UP_THRESHOLD`; avoid lowering the 4-of-5 down window unless false positives are acceptable.
- For larger future workloads, consider PostgreSQL + Alembic only after approval.
- Runtime config is explicit: installed services use `/etc/th-network-monitor/.env`; local/dev uses repo `.env`.
