# TH Network Monitor — Architecture

## 1. Mục tiêu

TH Network Monitor là web GUI nội bộ để monitor WAN/DNS và IP Tunnel cho khoảng 500 cửa hàng TH Truemart.

Phạm vi hiện tại:

- Agentless, không cài agent tại store.
- Import inventory từ Excel.
- Lưu dữ liệu mặc định bằng SQLite.
- Gửi cảnh báo và recovery qua Telegram.
- GUI nội bộ: dashboard, stores, store detail, incidents, import, backup/restore.
- Không triển khai latency/packet loss ở giai đoạn này.

## 2. Stack cố định

- FastAPI + Jinja2 server-side GUI.
- SQLite + SQLAlchemy ORM.
- Async worker cho monitor loop.
- System `ping` command cho ICMP check.
- Telegram Bot API cho alert.
- pandas/openpyxl cho Excel import.
- systemd cho deployment.

Không tự ý đổi stack/database/service layout nếu chưa được xác nhận.

## 3. Service layout

```text
systemd
├── th-network-monitor-web.service
│   └── uvicorn app.main:app
│       ├── Dashboard / Stores / Store detail
│       ├── Incidents / Export report
│       ├── Import preview + confirm
│       ├── Backup / restore
│       ├── Telegram test
│       └── Manual monitor run-once
│
└── th-network-monitor-worker.service
    └── python -m monitor.worker
        └── Periodic monitor loop
```

The production installer copies both unit files, runs `systemctl daemon-reload`, then `systemctl enable --now` for both services. A successful `sudo scripts/install.sh` therefore leaves the web GUI and periodic worker running without a second manual start step.

## 4. Data model

### Store

Inventory cửa hàng:

- `store_code`, `pc_name`
- `ip_local`, `ip_tunnel`, `wan_dns`
- `region`, `area`, `address`
- `enabled`

### StoreStatus

Trạng thái mới nhất:

- `wan_status`: `UP`, `DOWN`, `UNKNOWN` — latest raw WAN/DNS probe
- `tunnel_status`: `UP`, `DOWN`, `UNKNOWN` — latest raw tunnel probe
- `overall_status`: `UP`, `WAN_DOWN`, `TUNNEL_DOWN`, `DOWN`, `UNKNOWN` — worker-confirmed incident status
- `wan_fail_count`, `tunnel_fail_count`
- `wan_success_count`, `tunnel_success_count`
- `wan_down_window`, `tunnel_down_window`: 5 check gần nhất, `1` là fail, `0` là success
- `last_check_at`, `last_changed_at`, `last_alert_at`

### Incident

Lịch sử sự cố:

- `store_id`, `incident_type`, `status`
- `started_at`, `ended_at`, `duration_seconds`
- `alert_sent`, `recovery_sent`, `alert_sent_at`
- `last_reminder_at`, `reminder_count`, `detail`

Rule: mỗi store chỉ có tối đa 1 incident `OPEN`; nếu đã có thì update type/detail, không tạo duplicate.

## 5. Database workflow

SQLite là mặc định vì đủ nhẹ cho workload hiện tại. Production SQLite phải bật:

- `PRAGMA journal_mode=WAL;`
- `PRAGMA busy_timeout=5000;`

Lý do: web process và worker process có thể đọc/ghi cùng lúc.

SQLite migration rule:

- `create_all()` không tự thêm cột vào DB cũ.
- Nếu thêm cột mới, phải có migration idempotent trong [app/database.py](app/database.py) hoặc script migration.
- Startup phải chạy migration trước khi app/worker dùng schema mới.

## 6. Import workflow

```text
Upload Excel
  → save pending file
  → parse + normalize columns
  → validate store_code/IP/duplicates
  → preview create/update/errors
  → user confirm
  → backup DB nếu import lớn
  → create/update Store
  → ensure StoreStatus exists
  → commit
```

Cột hỗ trợ:

- `Mã CH`, `Ma CH`, `Store Code` → `store_code`
- `PC Name` → `pc_name`
- `IP Local` → `ip_local`
- `IP tunel`, `IP Tunnel` → `ip_tunnel`
- `WAN DNS`, `DNS`, `Domain` → `wan_dns`
- `Miền`, `Mien` → `region`
- `Khu vực`, `Khu vuc` → `area`
- `Địa chỉ`, `Dia chi` → `address`

Safe import rules:

- Sanitize uploaded filename, không cho path traversal.
- Store code phải là 7-8 số và bắt đầu bằng `70000`.
- Báo lỗi store_code trùng trong cùng file Excel.
- Excel thiếu optional column thì không overwrite field cũ bằng `None`.
- Cell blank mặc định không overwrite field cũ.
- Backup DB trước import nếu số dòng hợp lệ lớn hơn 50.

## 7. Monitor workflow

```text
Start monitor cycle
  → acquire cross-process file lock data/monitor.lock
  → load enabled stores
  → async check WAN/DNS + IP Tunnel with max concurrency
  → apply PING_RETRY per target
  → update raw target status + counters + down windows
  → apply 4-of-5 DOWN window before changing confirmed overall status
  → open/update/resolve Incident
  → commit DB state
  → collect old OPEN incidents with alert_sent=false
  → collect due reminders for OPEN incidents with alert_sent=true
  → double-check DOWN alerts/reminders before batching
  → send Telegram alerts/reminders/recoveries
  → mark sent/reminder flags only after Telegram success
  → release lock
```

Nếu lock busy, cycle/request mới skip an toàn:

```json
{"status":"skipped","reason":"monitor already running"}
```

## 8. Check và status logic

### Check target

- WAN/DNS: ping trực tiếp giá trị cấu hình, giống tunnel; DNS/IP đều chỉ cần pass/fail theo `ping`.
- Tunnel: ping `ip_tunnel`.
- `PING_RETRY` được dùng thật; pass nếu có ít nhất 1 reply.

### Derived status

Raw target status:

```text
WAN probe success/fail/unknown    → wan_status UP/DOWN/UNKNOWN
Tunnel probe success/fail/unknown → tunnel_status UP/DOWN/UNKNOWN
```

Worker-confirmed `overall_status` for incident/alert flow:

```text
WAN confirmed UP    + Tunnel confirmed UP    → UP
WAN confirmed DOWN  + Tunnel confirmed UP    → WAN_DOWN
WAN confirmed UP    + Tunnel confirmed DOWN  → TUNNEL_DOWN
WAN confirmed DOWN  + Tunnel confirmed DOWN  → DOWN
Thiếu target / chưa confirmed                  → giữ overall_status trước đó hoặc UNKNOWN
```

GUI display status is computed at render time from stored raw target status + counters/windows:

```text
WAN/Tunnel display DOWN → latest raw status DOWN and down window reaches DOWN_THRESHOLD
WAN/Tunnel display UP   → latest raw status UP and success_count reaches UP_THRESHOLD
GUI Overall             → derive from displayed WAN + displayed Tunnel
```

Nếu store chỉ cấu hình WAN hoặc chỉ cấu hình Tunnel, status được derive theo target đang có sau khi target đó đạt threshold tương ứng.

### Threshold

- Raw `wan_status`/`tunnel_status` cập nhật theo probe mới nhất.
- Worker-confirmed `overall_status` chỉ đổi sang DOWN/WAN_DOWN/TUNNEL_DOWN khi target hiện tại vẫn DOWN và số lần fail trong 5 lần check gần nhất đạt `DOWN_THRESHOLD`.
- GUI keeps the previous displayed state while a target is below DOWN/UP threshold, then derives Overall from displayed WAN/Tunnel.
- Resolve incident khi các target bắt buộc UP liên tiếp đạt `UP_THRESHOLD`.
- `UNKNOWN` không được tính là UP để recovery.

Counter/window rule:

```text
Target UP:      success_count += 1, fail_count = 0, down_window += 0
Target DOWN:    fail_count += 1, success_count = 0, down_window += 1
Target UNKNOWN: không tính recovery, không đổi down_window
```

## 9. Alert workflow

```text
Status transition / incident event
  → build in-memory alert/recovery event
  → commit DB state
  → add old OPEN incidents with alert_sent=false when Telegram is configured
  → add due reminder events for OPEN + alert_sent=true incidents
  → double-check DOWN alerts/reminders before batching
  → send Telegram
  → if success: mark alert_sent/recovery_sent/reminder timestamp + last_alert_at
  → if fail/suppressed: keep sent/reminder flags unchanged, do not rollback incident state
```

Batch alert:

- 1-5 alerts: gửi chi tiết từng store.
- 6-30 alerts: gửi 1 summary theo status/region/area.
- >30 alerts: gửi 1 major incident summary.
- Recovery 1-5: gửi chi tiết từng store; recovery >5: gửi 1 recovery summary.
- Reminder 1-5: gửi chi tiết từng store; reminder >5: gửi 1 unresolved summary.
- Chỉ gửi recovery notification cho incident đã gửi DOWN alert thành công (`alert_sent=true`).
- Mỗi `TELEGRAM_REMINDER_INTERVAL_SECONDS` giây, worker scan incident `OPEN + alert_sent=true`; default `21600` (6h), `0` disables reminders.
- Reminder dùng anchor `last_reminder_at` hoặc `alert_sent_at`; nếu gửi fail thì không update timestamp để retry cycle sau.
- Nếu Telegram chưa cấu hình, worker không query retry pending alert/reminder để tránh scan lặp vô ích.

## 10. GUI workflow

Tất cả GUI/admin routes được bảo vệ bằng auth khi `AUTH_ENABLED=true`:

- `/`, `/stores`, `/stores/{id}`
- `/import`, `/import/preview`, `/import/confirm`, `/import/cancel`
- `/incidents`, `/incidents/export`
- `/backups`, backup create/download/delete/restore
- `/monitor/run-once`, `/telegram/test`, `/api/stores`

GUI hiện có:

- Dashboard: tổng store, count theo GUI display status, bảng 100 stores đầu.
- Stores: search/filter theo GUI display status, status hiện tại, xóa store khi cần.
- Store detail: thông tin store + incidents gần nhất.
- Incidents: filter và export Excel.
- Import: preview + confirm/cancel.
- Backups: SQLite backup/restore UI.
- Check now: submit manual `/monitor/run-once`; GUI form gửi `return_to` nên sau khi check xong redirect lại trang hiện tại. Direct API call không có `return_to` vẫn trả JSON.

Dashboard/Stores refresh rule:

- Worker cập nhật `StoreStatus.last_check_at` trong DB mỗi monitor cycle thành công.
- GUI đọc `last_check_at` khi render trang; không có realtime websocket/polling, nên người dùng cần refresh hoặc dùng Check now để thấy dữ liệu mới.

Datetime display rule:

- DB timestamps are stored as UTC-naive datetimes.
- GUI renders status/incident timestamps through `TIMEZONE`, default `Asia/Ho_Chi_Minh`.

Delete store rule:

- Xóa store là hard delete.
- Xóa kèm `StoreStatus` và các `Incident` liên quan để tránh dữ liệu mồ côi.
- Chỉ hiển thị action xóa ở trang `/stores`, không hiển thị trên dashboard.

## 11. Security/Ops

Production checklist:

- Chạy bằng systemd service riêng, không chạy root nếu không cần.
- `.env` riêng, không commit token/password/session secret.
- Bật `AUTH_ENABLED=true`.
- Bật SQLite WAL + busy timeout.
- Có monitor lock.
- Có log rotation.
- Backup DB trước import lớn hoặc thao tác production rủi ro.

Runtime files không commit:

- `.env`
- `.pytest_cache/`
- `__pycache__/`
- `data/*.db`, upload/import preview files
- `logs/*.log`

## 12. Config chuẩn

```env
APP_HOST=0.0.0.0
APP_PORT=8080
DATABASE_URL=sqlite:///./data/network_monitor.db
MONITOR_INTERVAL_SECONDS=30
PING_TIMEOUT_SECONDS=2
PING_RETRY=3
DOWN_THRESHOLD=4
UP_THRESHOLD=2
MAX_CONCURRENCY=100
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_REMINDER_INTERVAL_SECONDS=21600
TIMEZONE=Asia/Ho_Chi_Minh
LOG_LEVEL=INFO
DATA_DIR=./data
LOG_DIR=./logs
AUTH_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-this-password
SESSION_SECRET=change-this-random-secret
SESSION_COOKIE_NAME=thnm_session
SESSION_MAX_AGE_SECONDS=28800
```

Config loading rule:

- Local/dev loads repo `.env` by default.
- Installed systemd services and `thnm` set `THNM_ENV_FILE=/etc/th-network-monitor/.env` explicitly.
- The app does not auto-probe `/etc` during local runs.

## 13. Test / verification

```bash
python -m compileall app monitor alerts importers scripts tests
python -m pytest
```

Current expected result: full pytest suite passes.

Focused areas covered by tests:

- Auth/session protection.
- Store list/detail/delete.
- Import preview/confirm/cancel and import safety.
- Status engine 4-of-5 threshold, GUI threshold sync, recovery, and dedup.
- Alert batching/dedup, 6-hour reminders, and DOWN double-check suppression.
- Backup/restore.
- Incident export.

## 14. Known non-blocking cleanup

- Starlette test client cookie deprecation: informational only.

## 15. Rules for future code agents

Before editing, read [README.md](README.md) and this file. Treat this file as canonical.

Hard constraints:

- Do not change FastAPI + Jinja2 + SQLite + SQLAlchemy + async worker + Telegram stack without approval.
- Do not implement latency/packet loss unless explicitly requested.
- Do not rewrite the project; make small targeted changes.
- Preserve routes/templates/model names unless a small compatibility edit is required.
- If a required change conflicts with this architecture, stop and report the conflict.
- Add/update tests for behavior changes.
- Do not commit runtime/cache files.
