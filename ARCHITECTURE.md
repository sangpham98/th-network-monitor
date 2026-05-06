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

- `wan_status`: `UP`, `DOWN`, `UNKNOWN`
- `tunnel_status`: `UP`, `DOWN`, `UNKNOWN`
- `overall_status`: `UP`, `WAN_DOWN`, `TUNNEL_DOWN`, `DOWN`, `UNKNOWN`
- `wan_fail_count`, `tunnel_fail_count`
- `wan_success_count`, `tunnel_success_count`
- `last_check_at`, `last_changed_at`, `last_alert_at`

### Incident

Lịch sử sự cố:

- `store_id`, `incident_type`, `status`
- `started_at`, `ended_at`, `duration_seconds`
- `alert_sent`, `recovery_sent`, `detail`

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
  → update status counters
  → apply DOWN_THRESHOLD / UP_THRESHOLD
  → open/update/resolve Incident
  → commit DB state
  → collect old OPEN incidents with alert_sent=false
  → send Telegram alerts/recoveries
  → mark sent flags only after Telegram success
  → release lock
```

Nếu lock busy, cycle/request mới skip an toàn:

```json
{"status":"skipped","reason":"monitor already running"}
```

## 8. Check và status logic

### Check target

- WAN/DNS: resolve nếu có thể, rồi ping target; nếu resolve fail vẫn thử ping vì target có thể là IP.
- Tunnel: ping `ip_tunnel`.
- `PING_RETRY` được dùng thật; pass nếu có ít nhất 1 reply.

### Derived status

```text
WAN UP    + Tunnel UP    → UP
WAN DOWN  + Tunnel UP    → WAN_DOWN
WAN UP    + Tunnel DOWN  → TUNNEL_DOWN
WAN DOWN  + Tunnel DOWN  → DOWN
Thiếu target / không check được → UNKNOWN
```

Nếu store chỉ cấu hình WAN hoặc chỉ cấu hình Tunnel, status được derive theo target đang có.

### Threshold

- Mở/chuyển incident khi fail liên tiếp đạt `DOWN_THRESHOLD`.
- Resolve incident khi các target bắt buộc UP liên tiếp đạt `UP_THRESHOLD`.
- `UNKNOWN` không được tính là UP để recovery.

Counter rule:

```text
Target UP:      success_count += 1, fail_count = 0
Target DOWN:    fail_count += 1, success_count = 0
Target UNKNOWN: không tính recovery
```

## 9. Alert workflow

```text
Status transition / incident event
  → build in-memory alert event
  → commit DB state
  → add old OPEN incidents with alert_sent=false when Telegram is configured
  → send Telegram
  → if success: mark alert_sent/recovery_sent + last_alert_at
  → if fail: keep sent flags false, log error, do not rollback incident state
```

Batch alert:

- 1-5 alerts: gửi chi tiết từng store.
- 6-30 alerts: gửi 1 summary theo status/region/area.
- >30 alerts: gửi 1 major incident summary.
- Recovery 1-5: gửi chi tiết từng store; recovery >5: gửi 1 recovery summary.
- Nếu Telegram chưa cấu hình, worker không query retry pending alert để tránh scan lặp vô ích.

## 10. GUI workflow

Tất cả GUI/admin routes được bảo vệ bằng auth khi `AUTH_ENABLED=true`:

- `/`, `/stores`, `/stores/{id}`
- `/import`, `/import/preview`, `/import/confirm`, `/import/cancel`
- `/incidents`, `/incidents/export`
- `/backups`, backup create/download/delete/restore
- `/monitor/run-once`, `/telegram/test`, `/api/stores`

GUI hiện có:

- Dashboard: tổng store, count theo status, bảng 100 stores đầu.
- Stores: search/filter, status hiện tại, xóa store khi cần.
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
MONITOR_INTERVAL_SECONDS=60
PING_TIMEOUT_SECONDS=1
PING_RETRY=2
DOWN_THRESHOLD=3
UP_THRESHOLD=2
MAX_CONCURRENCY=150
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
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
- Status engine threshold/recovery/dedup.
- Alert batching/dedup.
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
