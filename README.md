# TH Network Monitor

Web GUI nhẹ để monitor WAN/DNS và IP Tunnel cho ~500 cửa hàng TH Truemart, import dữ liệu từ Excel và gửi cảnh báo Telegram.

## Project governance rule

Trong suốt quá trình xây dựng và vận hành, project phải bám sát kiến trúc và workflow đã thống nhất:

- Không tự ý đổi stack, database, service layout, monitor logic, alert flow hoặc import workflow.
- Nếu gặp lỗi/bug/conflict cần thay đổi kiến trúc hoặc workflow, phải dừng lại để:
  1. Phân tích nguyên nhân.
  2. Đề xuất các phương án xử lý.
  3. Phân tích rủi ro/tác động từng phương án.
  4. Báo anh xác nhận trước khi thực thi thay đổi.
- Chỉ được tự xử lý các lỗi implementation nhỏ không làm đổi kiến trúc/flow đã chốt.
- Mọi thay đổi lớn phải cập nhật README/progress log để tránh lệch thiết kế.

## Stack

- FastAPI + Jinja2 GUI
- SQLite mặc định
- Async ping monitor
- Telegram Bot API
- pandas/openpyxl để import Excel
- systemd service mẫu

## Cấu trúc

```text
app/              FastAPI app, config, DB models
web/templates/    GUI HTML
web/static/       CSS
monitor/          ping/DNS checker + worker
alerts/           Telegram sender
importers/        Excel importer
systemd/          service mẫu
scripts/          helper scripts
data/             SQLite DB + uploads
logs/             log runtime
```

## Cài đặt nhanh

```bash
cd /home/phamsang/.openclaw/workspace/th-network-monitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Sửa `.env`:

```env
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
```

## Chạy web GUI

```bash
. .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Mở:

```text
http://<linux-ip>:8080
```

## Chạy worker monitor

Terminal khác:

```bash
cd /home/phamsang/.openclaw/workspace/th-network-monitor
. .venv/bin/activate
python -m monitor.worker
```

## Import Excel

Vào GUI:

```text
/import
```

Cột hỗ trợ:

- `Mã CH`
- `PC Name`
- `IP Local`
- `IP tunel` hoặc `IP Tunnel`
- `WAN DNS`, `DNS`, hoặc `Domain`
- `Miền`
- `Khu vực`
- `Địa chỉ`

Tạo file mẫu:

```bash
python scripts/create_sample_excel.py
```

File mẫu nằm ở:

```text
data/sample_stores.xlsx
```

## Logic monitor

- WAN/DNS: resolve DNS nếu có, sau đó ping target.
- Tunnel: ping IP Tunnel.
- Mặc định check mỗi 60 giây.
- Chỉ mở incident khi fail liên tiếp >= `DOWN_THRESHOLD`.
- Khi WAN + Tunnel cùng UP lại, incident được resolve và gửi recovery.

Status:

- `UP`
- `WAN_DOWN`
- `TUNNEL_DOWN`
- `DOWN`
- `UNKNOWN`

## Test thủ công

Gửi Telegram test:

```bash
curl -X POST http://127.0.0.1:8080/telegram/test
```

Chạy monitor một lượt:

```bash
curl -X POST http://127.0.0.1:8080/monitor/run-once
```

## Cài systemd

> Cần quyền sudo/root.

```bash
sudo cp systemd/th-network-monitor-web.service /etc/systemd/system/
sudo cp systemd/th-network-monitor-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now th-network-monitor-web.service
sudo systemctl enable --now th-network-monitor-worker.service
```

Xem log:

```bash
journalctl -u th-network-monitor-web.service -f
journalctl -u th-network-monitor-worker.service -f
```

## Ghi chú vận hành

- 500 cửa hàng x 2 target vẫn nhẹ với `MAX_CONCURRENCY=150`.
- Nếu Telegram spam do line chập chờn, tăng `DOWN_THRESHOLD` lên 5 hoặc tăng `MONITOR_INTERVAL_SECONDS` lên 120.
- Nếu cần DB mạnh hơn sau này, đổi sang PostgreSQL và thêm migration bằng Alembic.

## Phase tiếp theo

- Auth login cho GUI.
- Store detail page.
- Export incident report Excel.
- Batch alert khi nhiều store down cùng lúc.
- Latency/packet loss thay vì chỉ UP/DOWN.
