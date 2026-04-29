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
- `wan_success_count`
- `tunnel_success_count`
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

### SQLite migration rule

Vì SQLite `create_all()` không tự thêm cột mới vào bảng đã tồn tại, mọi thay đổi schema phải có lightweight migration.

Rule bắt buộc:

- Không chỉ sửa SQLAlchemy model rồi kỳ vọng DB cũ tự cập nhật.
- Nếu thêm cột mới, phải có migration trong `app/database.py` hoặc `scripts/migrate_db.py`.
- Migration phải idempotent: chạy nhiều lần không lỗi.
- Startup nên chạy migration nhẹ trước khi app/worker dùng schema mới.

Các cột cần thêm cho workflow hiện tại nếu DB cũ chưa có:

```sql
ALTER TABLE store_status ADD COLUMN wan_success_count INTEGER DEFAULT 0;
ALTER TABLE store_status ADD COLUMN tunnel_success_count INTEGER DEFAULT 0;
```

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

### Safe import rule

Import không được làm mất dữ liệu cũ ngoài ý muốn.

Rule bắt buộc:

- Sanitize uploaded filename, không cho path traversal.
- Nếu Excel thiếu một cột optional, không overwrite field cũ bằng `None`.
- Nếu cell blank, mặc định không overwrite field cũ.
- Chỉ overwrite blank nếu có option explicit như `allow_blank_overwrite=true`.
- Backup DB trước import nếu số dòng lớn hơn 50 hoặc trước mọi import production.

Ví dụ:

```text
File import không có cột WAN DNS
→ không được xoá `store.wan_dns` đang tồn tại trong DB.

File import có cột WAN DNS nhưng cell blank
→ mặc định không được xoá `store.wan_dns` cũ.
```

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

Vì web và worker là 2 process khác nhau, không dùng `asyncio.Lock` làm lock chính.

Implementation đã chốt:

- Dùng cross-process file lock.
- Library khuyến nghị: `filelock`.
- Lock path: `data/monitor.lock`.
- Timeout: `0` hoặc timeout rất ngắn để không block service.
- Nếu lock busy, cycle/request mới phải skip an toàn.

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
wan_success_count >= UP_THRESHOLD
AND tunnel_success_count >= UP_THRESHOLD
```

Mặc định:

```env
UP_THRESHOLD=2
```

Counter rule:

```text
WAN UP:
    wan_success_count += 1
    wan_fail_count = 0
WAN DOWN:
    wan_fail_count += 1
    wan_success_count = 0
WAN UNKNOWN:
    không coi là UP, không được dùng để recovery

Tunnel UP:
    tunnel_success_count += 1
    tunnel_fail_count = 0
Tunnel DOWN:
    tunnel_fail_count += 1
    tunnel_success_count = 0
Tunnel UNKNOWN:
    không coi là UP, không được dùng để recovery
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
Create in-memory alert event
   ↓
Create/update/resolve incident
   ↓
Commit DB state
   ↓
Send Telegram
   ↓
If send success: mark alert_sent/recovery_sent + last_alert_at, then commit again
If send fail: keep sent flags false, log error, retry/manual resend later
```

Rule quan trọng:

- Không mark `alert_sent` trước khi Telegram gửi thành công.
- Không mark `recovery_sent` trước khi Telegram gửi thành công.
- Telegram lỗi không được rollback trạng thái incident đã detect.
- Không để lỗi Telegram làm crash monitor cycle.

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
    gửi 1 summary theo status/region/area + danh sách rút gọn

>30 alerts:
    gửi 1 major incident summary, nhóm theo region/area/status
```

Format summary khuyến nghị:

```text
🔴 TH NETWORK ALERT SUMMARY

Tổng affected: 12

Theo trạng thái:
- DOWN: 5
- WAN_DOWN: 4
- TUNNEL_DOWN: 3

Theo khu vực:
- HCM: 7
- Hà Nội: 5

Danh sách rút gọn:
1. CH001 - DOWN - HCM
2. CH002 - WAN_DOWN - Hà Nội
...
```

Format major incident khuyến nghị:

```text
🚨 TH NETWORK MAJOR INCIDENT

Tổng affected: 86

Top khu vực:
- HCM: 20
- Hà Nội: 14
- Đà Nẵng: 8

Gợi ý: kiểm tra hạ tầng WAN/VPN/DNS trung tâm.
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

Logging tối thiểu:

- Monitor cycle start/end.
- Số store checked.
- Số alert/recovery generated.
- Cycle skipped do lock busy.
- Telegram send fail.
- Import result.
- Migration result.

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

## 15. Antigravity implementation brief

Dùng đoạn này khi giao task cho Antigravity. Mục tiêu là để code agent sửa đúng phần cần sửa, không rewrite project và không tự đổi kiến trúc.

```text
You are working on TH Network Monitor.
Read README.md and ARCHITECTURE.md first. Treat ARCHITECTURE.md as canonical.

Hard constraints:
- Do not change the chosen stack: FastAPI + Jinja2 + SQLite + SQLAlchemy + async worker + Telegram Bot API.
- Do not implement latency or packet loss.
- Do not rewrite the whole project.
- Preserve existing routes, templates, model names, and service layout unless a small compatibility edit is required.
- Make small targeted changes and keep commits easy to review.
- If a required change conflicts with ARCHITECTURE.md, stop and document the conflict instead of guessing.

Current code gaps to fix for Phase 1:
- app.models.StoreStatus currently lacks wan_success_count and tunnel_success_count.
- monitor.checker.ping_host currently uses ping -c 1 and does not use settings.ping_retry.
- monitor.worker.run_once has no cross-process lock.
- monitor.status_engine.update_status_and_incident can create duplicate OPEN incidents.
- SQLite init must enable WAL and busy_timeout and run idempotent lightweight migrations.

Implement Phase 1 Stability Core only:
1. Add wan_success_count and tunnel_success_count columns to StoreStatus.
2. Add idempotent SQLite migration for those columns before app/worker use the DB.
3. Configure SQLite with WAL and busy_timeout=5000.
4. Use PING_RETRY in ping checks. Either call `ping -c PING_RETRY -W timeout target` or loop attempts; pass if at least one attempt succeeds.
5. Add cross-process monitor lock using filelock at data/monitor.lock. If lock is busy, return {"status":"skipped","reason":"monitor already running"} and do not run checks.
6. Implement UP_THRESHOLD with the explicit success counters. Recovery requires WAN and Tunnel both UP for UP_THRESHOLD consecutive cycles.
7. Prevent duplicate OPEN incidents per store. Reuse/update the existing OPEN incident instead of creating another.
8. Keep Telegram failure isolated from DB state. Do not mark sent flags before successful send.
9. Add/update tests for checker retry, status_engine thresholds/dedup, and monitor lock.

Suggested work order:
1. Inspect current files: app/database.py, app/models.py, monitor/checker.py, monitor/status_engine.py, monitor/worker.py, alerts/telegram.py, tests/ if present.
2. Add database/model/migration changes first.
3. Add checker retry.
4. Add monitor lock.
5. Refactor status engine carefully.
6. Add tests.
7. Run: python -m compileall app monitor alerts importers scripts
8. Run tests if test framework exists; otherwise add pytest tests and run pytest.
9. Update README.md only if setup/test commands changed.

Do not start Phase 2 or Phase 3 in this task.
```

## 16. File-level implementation map for Phase 1

Antigravity/code agent nên bám theo map này để tránh sửa lan man:

| File | Việc cần làm | Không làm |
|---|---|---|
| `app/models.py` | Thêm `wan_success_count`, `tunnel_success_count` vào `StoreStatus` | Không rename bảng/cột cũ |
| `app/database.py` | Bật SQLite WAL/busy_timeout, thêm migration idempotent | Không đưa Alembic/PostgreSQL vào Phase 1 |
| `monitor/checker.py` | Cho `ping_host()` nhận retry và dùng thật `PING_RETRY` | Không tính latency/packet loss |
| `monitor/worker.py` | Truyền retry vào checker, bọc `run_once()` bằng `filelock` | Không đổi service layout |
| `monitor/status_engine.py` | Counter success/fail, UP_THRESHOLD, dedup OPEN incident | Không tạo nhiều incident OPEN cùng store |
| `alerts/telegram.py` | Giữ lỗi Telegram không crash cycle nếu cần harden nhẹ | Không đổi provider alert |
| `requirements.txt` | Thêm `filelock`, `pytest` nếu thiếu | Không thêm framework lớn |
| `tests/` | Test retry, threshold, dedup, lock | Không phụ thuộc Telegram thật |

## 17. Acceptance criteria

### Phase 1 — Stability Core

```text
[ ] PING_RETRY thật sự được dùng.
[ ] UP_THRESHOLD thật sự được dùng.
[ ] Recovery không xảy ra chỉ sau 1 lần UP nếu UP_THRESHOLD > 1.
[ ] Worker và /monitor/run-once không chạy trùng được.
[ ] Existing OPEN incident không bị duplicate.
[ ] SQLite bật WAL.
[ ] SQLite có busy_timeout.
[ ] Schema migration thêm cột mới nếu DB cũ đã tồn tại.
[ ] Tests pass.
[ ] `python -m compileall app monitor alerts importers scripts` pass.
```

### Phase 2 — Alert Quality

```text
[ ] 1-5 alerts gửi chi tiết từng store.
[ ] 6-30 alerts gửi summary.
[ ] >30 alerts gửi major incident summary.
[ ] Recovery chỉ gửi 1 lần / incident.
[ ] Telegram fail không làm mất incident state.
[ ] Alert sent flags chỉ set true sau khi gửi thành công.
```

### Phase 3 — Import Safety

```text
[ ] Missing Excel column không xoá field cũ.
[ ] Blank cell không overwrite dữ liệu cũ mặc định.
[ ] Upload filename được sanitize.
[ ] Import lớn backup DB trước khi commit.
[ ] Import summary hiển thị created/updated/errors.
```

## 18. Test matrix bắt buộc

### Status engine

```text
[ ] UNKNOWN → UP.
[ ] UNKNOWN → WAN_DOWN sau đủ DOWN_THRESHOLD.
[ ] UNKNOWN → TUNNEL_DOWN sau đủ DOWN_THRESHOLD.
[ ] UNKNOWN → DOWN sau đủ DOWN_THRESHOLD.
[ ] DOWN chưa recovery nếu mới UP 1 lần và UP_THRESHOLD=2.
[ ] DOWN recovery sau UP đủ 2 lần.
[ ] Không duplicate OPEN incident khi vẫn down.
[ ] Resolve đúng duration_seconds.
[ ] WAN missing / Tunnel missing trả UNKNOWN hợp lý.
```

### Monitor lock

```text
[ ] Lock busy → run_once skipped.
[ ] Lock free → run_once runs.
[ ] Worker loop và manual run-once không chạy đồng thời.
```

### Checker retry

```text
[ ] PING_RETRY=3 gọi ping với 3 attempts hoặc equivalent `ping -c 3`.
[ ] Có ít nhất 1 reply thì target considered UP.
[ ] Toàn bộ retry fail thì target considered DOWN.
```

### Import safety

```text
[ ] Missing optional column không xoá dữ liệu cũ.
[ ] Blank cell không overwrite dữ liệu cũ mặc định.
[ ] Invalid IP bị đưa vào errors.
[ ] Duplicate store_code update existing.
```

## 19. Definition of done for an Antigravity PR

Một PR/patch đạt chuẩn khi có đủ:

- Scope chỉ nằm trong Phase được giao.
- Không đổi stack/DB/service layout.
- Có test hoặc tối thiểu có manual verification rõ ràng.
- Không commit `.env`, DB runtime, upload files, logs, `__pycache__`.
- README/ARCHITECTURE được cập nhật nếu behavior thực tế khác tài liệu.
- Commit message rõ: ví dụ `Implement Phase 1 stability core`.

## 20. Những thứ không làm ở giai đoạn này

- Không đo latency.
- Không tính packet loss.
- Không cài endpoint agent tại store.
- Không đổi SQLite sang DB khác nếu chưa có nhu cầu rõ ràng.
- Không đổi stack web/API nếu chưa được xác nhận.
