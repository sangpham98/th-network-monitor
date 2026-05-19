from monitor.status_engine import format_alert_summary, format_major_incident
from monitor.worker import build_telegram_batches


def make_event(index: int, recovered: bool = False, status: str = "DOWN", kind: str | None = None):
    event = {
        "store_code": f"CH{index:03d}",
        "pc_name": f"PC{index:03d}",
        "region": "HCM" if index % 2 == 0 else "Ha Noi",
        "area": "Area A" if index % 3 == 0 else "Area B",
        "address": "Test address",
        "status": status,
        "wan_dns": f"wan{index}.example",
        "ip_tunnel": f"10.0.0.{index}",
        "incident_ids": [index],
        "recovered": recovered,
        "started_at": f"2026-01-01 00:0{index}:00",
        "reminder_count": index,
    }
    if kind is not None:
        event["kind"] = kind
    return event


def test_one_to_five_alerts_send_detail_messages():
    batches = build_telegram_batches([make_event(i) for i in range(1, 6)])

    assert len(batches) == 5
    assert all(batch["kind"] == "alert" for batch in batches)
    assert all(batch["recovered"] is False for batch in batches)
    assert all("TH TRUEMART ALERT" in batch["message"] for batch in batches)
    assert [batch["incident_ids"] for batch in batches] == [[1], [2], [3], [4], [5]]


def test_six_to_thirty_alerts_send_one_summary():
    batches = build_telegram_batches([make_event(i) for i in range(1, 7)])

    assert len(batches) == 1
    assert batches[0]["kind"] == "alert"
    assert batches[0]["recovered"] is False
    assert batches[0]["incident_ids"] == [1, 2, 3, 4, 5, 6]
    assert "TH NETWORK ALERT SUMMARY" in batches[0]["message"]
    assert "Tổng affected: 6" in batches[0]["message"]


def test_more_than_thirty_alerts_send_major_incident():
    batches = build_telegram_batches([make_event(i) for i in range(1, 32)])

    assert len(batches) == 1
    assert batches[0]["kind"] == "alert"
    assert batches[0]["recovered"] is False
    assert batches[0]["incident_ids"] == list(range(1, 32))
    assert "TH NETWORK MAJOR INCIDENT" in batches[0]["message"]
    assert "Tổng affected: 31" in batches[0]["message"]


def test_one_to_five_recoveries_send_detail_messages():
    batches = build_telegram_batches([make_event(i, recovered=True, status="UP") for i in range(1, 6)])

    assert len(batches) == 5
    assert all(batch["kind"] == "recovery" for batch in batches)
    assert all(batch["recovered"] is True for batch in batches)
    assert all("TH TRUEMART RECOVERY" in batch["message"] for batch in batches)


def test_recovery_more_than_five_sends_one_summary():
    batches = build_telegram_batches([make_event(i, recovered=True, status="UP") for i in range(1, 7)])

    assert len(batches) == 1
    assert batches[0]["kind"] == "recovery"
    assert batches[0]["recovered"] is True
    assert batches[0]["incident_ids"] == [1, 2, 3, 4, 5, 6]
    assert "TH NETWORK RECOVERY SUMMARY" in batches[0]["message"]


def test_one_to_five_reminders_send_detail_messages():
    batches = build_telegram_batches([make_event(i, kind="reminder") for i in range(1, 6)])

    assert len(batches) == 5
    assert all(batch["kind"] == "reminder" for batch in batches)
    assert all(batch["recovered"] is False for batch in batches)
    assert all("TH TRUEMART REMINDER" in batch["message"] for batch in batches)
    assert [batch["incident_ids"] for batch in batches] == [[1], [2], [3], [4], [5]]


def test_more_than_five_reminders_send_one_summary():
    batches = build_telegram_batches([make_event(i, kind="reminder") for i in range(1, 7)])

    assert len(batches) == 1
    assert batches[0]["kind"] == "reminder"
    assert batches[0]["recovered"] is False
    assert batches[0]["incident_ids"] == [1, 2, 3, 4, 5, 6]
    assert "TH NETWORK REMINDER SUMMARY" in batches[0]["message"]
    assert "Tổng unresolved: 6" in batches[0]["message"]


def test_mixed_alert_reminder_and_recovery_batches_do_not_bleed_ids():
    batches = build_telegram_batches(
        [
            make_event(1, kind="alert"),
            make_event(2, kind="reminder"),
            make_event(3, recovered=True, status="UP", kind="recovery"),
        ]
    )

    assert [batch["kind"] for batch in batches] == ["alert", "reminder", "recovery"]
    assert [batch["incident_ids"] for batch in batches] == [[1], [2], [3]]


def test_summary_groups_by_status_and_region():
    events = [make_event(1, status="DOWN"), make_event(2, status="WAN_DOWN"), make_event(3, status="DOWN")]

    message = format_alert_summary(events)

    assert "- DOWN: 2" in message
    assert "- WAN_DOWN: 1" in message
    assert "Theo miền:" in message
    assert "Theo khu vực:" in message
    assert "Area A" in message or "Area B" in message


def test_major_incident_contains_top_regions_and_guidance():
    message = format_major_incident([make_event(i) for i in range(1, 32)])

    assert "Top khu vực:" in message
    assert "Gợi ý: kiểm tra hạ tầng WAN/VPN/DNS trung tâm." in message
