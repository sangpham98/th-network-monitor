from io import BytesIO

import pandas as pd


def _workbook_bytes(data: list[dict], sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


def build_incident_report(rows) -> bytes:
    data = []
    for incident, store in rows:
        data.append(
            {
                "Incident ID": incident.id,
                "Store Code": store.store_code,
                "PC Name": store.pc_name,
                "Region": store.region,
                "Area": store.area,
                "Incident Type": incident.incident_type,
                "Status": incident.status,
                "Started At": incident.started_at,
                "Ended At": incident.ended_at,
                "Duration Seconds": incident.duration_seconds,
                "Detail": incident.detail,
            }
        )
    return _workbook_bytes(data, "Incidents")


def build_store_report(rows) -> bytes:
    return _workbook_bytes(rows, "Stores")
