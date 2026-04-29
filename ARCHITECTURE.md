# TH Network Monitor — Architecture & Workflow

## 1. Mục tiêu

TH Network Monitor là hệ thống web GUI nhẹ để monitor kết nối mạng cho khoảng 500 cửa hàng TH Truemart.

Phạm vi đã chốt:

- Monitor agentless, không cài agent tại store.
- Monitor bằng WAN/DNS và IP Tunnel.
- Import inventory từ Excel.
- Lưu dữ liệu mặc định bằng SQLite.
- Gửi cảnh báo và recovery qua Telegram.
- Web GUI nội bộ để xem dashboard, stores, incidents và import dữ liệu.
- Không triển khai latency/packet loss ở giai đoạn này.

## 2. Stack đã chốt

- FastAPI cho web app/API.
- Jinja2 cho GUI server-side rendering.
- SQLite mặc định cho database.
- SQLAlchemy ORM.
- Async worker cho monitor loop.
- System `ping` command cho ICMP check.
- Telegram Bot API cho alert.
- pandas/openpyxl cho Excel import.
- systemd cho service deployment.

Không tự ý đổi stack nếu chưa được xác nhận.

## 3. Service layout

```text
systemd
├── th-network-monitor-web.service
│   └── uvicorn app.main:app
│       ├── Dashboard
│       ├── Stores
│       ├── Incidents
│       ├── Import Excel
│       ├── Telegram test
│       └── Manual monitor run-once
│
└── th-network-monitor-worker.service
    └── python -m monitor.worker
        └── Periodic monitor loop
```

## 4. Data model

### Store

Lưu inventory cửa hàng:

- `store_code`
- `pc_name`
- `ip_local`
- `ip_tunnel`
- `wan_dns`
- `region`
- `area`
- `address`
- `enabled`

### StoreStatus

Lưu trạng thái mới nhất:

- `wan_status`: `UP`, `DOWN`, `UNKNOWN`
- `tunnel_status`: `UP`, `DOWN`, `UNKNOWN`
- `overall_status`: `UP`, `WAN_DOWN`, `TUNNEL_DOWN`, `DOWN`, `UNKNOWN`
- `wan_fail_count`
- `tunnel_fail_count`
- success/up counter hoặc equivalent state để áp dụng `UP_THRESHOLD`
- `last_check_at`
- `last_changed_at`
- `last_alert_at`

### Incident

Lưu sự cố:

- `store_id`
- `incident_type`
- `status`: `OPEN`, `RESOLVED`
- `started_at`
- `ended_at`
- `duration_seconds`
- `alert_sent`
- `recovery_sent`
- `detail`

Rule quan trọng:

- Mỗi store chỉ nên có tối đa 1 active incident `OPEN` tại một thời điểm.
- Nếu store đang có incident `OPEN`, không tạo duplicate incident; chỉ update type/detail nếu cần.

## 5. Database workflow

SQLite vẫn là mặc định vì nhẹ và đủ cho ~500 stores.

Production SQLite phải cấu hình:

- `PRAGMA journal_mode=WAL;`
- `PRAGMA busy_timeout=5000;`

Lý do:

- Web process và worker process có thể đọc/ghi cùng lúc.
- WAL giúp giảm lock giữa read/write.
- busy timeout giúp tránh lỗi `database is locked` khi import hoặc monitor update.

Nếu sau này dữ liệu lớn hơn hoặc nhiều user truy cập đồng thời hơn, mới cân nhắc PostgreSQL kèm migration Alembic.

## 6. Import workflow

```text
Upload Excel
   ↓
Save uploaded file vào data/uploads
   ↓
Parse Excel
   ↓
Normalize column name
   ↓
Validate required fields và IP format
   ↓
Create/update Store theo store_code
   ↓
Create StoreStatus nếu chưa có
   ↓
Commit DB
   ↓
Return summary: created / updated / errors
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

Tối ưu workflow import nên làm:

- Backup DB trước khi import lớn.
- Lưu import file theo timestamp.
- Có import summary rõ lỗi dòng nào.
- Phase sau có thể thêm preview + confirm trước commit.

## 7. Monitor workflow

```text
Start monitor cycle
   ↓
Acquire monitor lock
   ↓
Load enabled stores
   ↓
Async check each store với max concurrency
   ├── WAN/DNS check
   └── Tunnel check
   ↓
Apply retry per target theo PING_RETRY
   ↓
Update StoreStatus counters
   ↓
Apply DOWN_THRESHOLD / UP_THRESHOLD
   ↓
Open / update / resolve Incident
   ↓
Commit DB
   ↓
Send Telegram alerts/recoveries
   ↓
Release monitor lock
   ↓
Sleep MONITOR_INTERVAL_SECONDS
```

### Monitor lock

Phải có lock chống chạy trùng giữa:

- worker loop
- manual `/monitor/run-once`
- nhiều request run-once đồng thời

Nếu monitor đang chạy, request mới nên trả về trạng thái kiểu:

```json
{"status": "skipped", "reason": "monitor already running"}
```

## 8. Check logic

### WAN/DNS

```text
Nếu có wan_dns:
    resolve DNS/hostname nếu có thể
    ping target
Nếu resolve fail:
    vẫn thử ping target vì target có thể là IP
Nếu không có wan_dns:
    status UNKNOWN
```

### Tunnel

```text
Nếu có ip_tunnel:
    ping IP Tunnel
Nếu không có ip_tunnel:
    status UNKNOWN
```

### Retry

`PING_RETRY` phải được dùng thật.

Khuyến nghị:

```text
ping -c PING_RETRY -W PING_TIMEOUT_SECONDS target
Pass nếu có ít nhất 1 reply.
Fail nếu toàn bộ retry fail.
```

Không cần tính latency/packet loss trong giai đoạn này.

## 9. Status engine workflow

### Derived status

```text
WAN UP    + Tunnel UP    → UP
WAN DOWN  + Tunnel UP    → WAN_DOWN
WAN UP    + Tunnel DOWN  → TUNNEL_DOWN
WAN DOWN  + Tunnel DOWN  → DOWN
Thiếu dữ liệu / không check được → UNKNOWN
```

### Down threshold

Chỉ mở hoặc chuyển incident khi fail liên tiếp đạt ngưỡng:

```text
fail_count >= DOWN_THRESHOLD
```

Mặc định:

```env
DOWN_THRESHOLD=3
```

### Up threshold

Chỉ resolve incident khi cả WAN và Tunnel UP liên tiếp đạt ngưỡng:

```text
up_count >= UP_THRESHOLD
```

Mặc định:

```env
UP_THRESHOLD=2
```

Mục tiêu: giảm alert flapping khi line chập chờn.

### Incident rules

```text
Nếu confirmed down:
    Nếu chưa có OPEN incident:
        tạo incident OPEN
        gửi alert
    Nếu đã có OPEN incident:
        update incident_type/detail nếu trạng thái nặng hơn hoặc thay đổi
        không gửi duplicate alert liên tục

Nếu confirmed up:
    Resolve tất cả OPEN incident của store
    set ended_at + duration_seconds
    gửi recovery một lần
```

## 10. Alert workflow

```text
State transition detected
   ↓
Create/update/resolve incident
   ↓
Build alert event
   ↓
Commit DB
   ↓
Send Telegram
   ↓
Mark alert_sent/recovery_sent
```

### Alert dedup

Không gửi lại cùng một alert nếu:

- store vẫn đang trong cùng incident `OPEN`
- trạng thái chưa chuyển đáng kể
- alert đã gửi rồi

### Batch alert

Khi nhiều cửa hàng đổi trạng thái trong cùng cycle:

```text
1-5 alerts:
    gửi chi tiết từng cửa hàng

6-30 alerts:
    gửi summary theo status/region/area + danh sách rút gọn

>30 alerts:
    gửi major incident summary, nhóm theo region/area/status
```

Mục tiêu: tránh spam Telegram khi mất diện rộng.

## 11. GUI workflow

### Dashboard

- Tổng số store.
- Count theo status.
- Danh sách store mới nhất/quan trọng.

### Stores

- Search theo store code, PC name, IP tunnel, area.
- Filter theo status.
- Hiển thị status hiện tại.

### Incidents

- Danh sách incident mới nhất.
- Trạng thái OPEN/RESOLVED.
- Thời gian bắt đầu/kết thúc/duration.

### Import

- Upload Excel.
- Hiển thị summary created/updated/errors.

### Admin actions

Các action sau nên được bảo vệ bằng auth khi triển khai ngoài localhost/LAN tin cậy:

- `/import`
- `/monitor/run-once`
- `/telegram/test`

## 12. Security/Ops workflow

Production checklist:

- Chạy bằng systemd service riêng.
- Không chạy bằng root nếu không cần.
- Có `.env` riêng, không commit token Telegram.
- Bật SQLite WAL/busy timeout.
- Có monitor lock.
- Có log rotation.
- Backup DB trước import lớn.
- Thêm GUI auth nếu expose ngoài máy local/LAN tin cậy.

## 13. Config chuẩn

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
```

## 14. Roadmap đã cập nhật

### Phase 1 — Stability Core

- Dùng thật `PING_RETRY`.
- Implement `UP_THRESHOLD`.
- Thêm monitor lock.
- Deduplicate active incident.
- Bật SQLite WAL + busy timeout.

### Phase 2 — Alert Quality

- Batch alert khi nhiều store down.
- Alert dedup theo incident.
- Recovery chỉ gửi một lần.
- Alert summary theo region/area/status.

### Phase 3 — GUI/Ops

- Auth login cho GUI.
- Store detail page.
- Export incident report Excel.
- Import history/backup trước import.
- Log rotation/systemd hardening.

## 15. Những thứ không làm ở giai đoạn này

- Không đo latency.
- Không tính packet loss.
- Không cài endpoint agent tại store.
- Không đổi SQLite sang DB khác nếu chưa có nhu cầu rõ ràng.
- Không đổi stack web/API nếu chưa được xác nhận.
