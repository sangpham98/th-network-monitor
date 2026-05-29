from monitor.status_engine import format_current_incidents_summary


def make_event(index: int, status: str = "DOWN"):
    return {
        "store_code": f"CH{index:03d}",
        "pc_name": f"PC{index:03d}",
        "region": "HCM" if index % 2 == 0 else "Ha Noi",
        "area": "Area A" if index % 3 == 0 else "Area B",
        "address": "Test address",
        "status": status,
        "wan_dns": f"wan{index}.example",
        "ip_tunnel": f"10.0.0.{index}",
        "incident_ids": [index],
        "recovered": False,
    }


def test_empty_summary_sends_ok_heartbeat():
    message = format_current_incidents_summary([], "09:00")

    assert "TH NETWORK OK" in message
    assert "09:00" in message
    assert "Không có store đang incident" in message


def test_current_incidents_summary_groups_by_status_and_region():
    events = [make_event(1, status="DOWN"), make_event(2, status="WAN_DOWN"), make_event(3, status="DOWN")]

    message = format_current_incidents_summary(events, "14:00")

    assert "TH NETWORK INCIDENT SUMMARY" in message
    assert "📌 Tổng affected: <b>3</b>" in message
    assert "• DOWN: <b>2</b>" in message
    assert "• WAN_DOWN: <b>1</b>" in message
    assert "🌏 Theo miền:" in message
    assert "🗺️ Theo khu vực:" in message
    assert "CH001" in message
    assert "CH002" in message


def test_current_incidents_summary_limits_store_list():
    message = format_current_incidents_summary([make_event(i) for i in range(1, 32)], "09:00")

    assert "CH001" in message
    assert "CH030" in message
    assert "CH031" not in message
    assert "…and 1 more" in message
