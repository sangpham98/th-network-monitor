from pathlib import Path
import pandas as pd

rows = [
    {
        "Mã CH": "70000123",
        "PC Name": "VNTRM0123",
        "IP Local": "10.56.10.5",
        "IP tunel": "10.58.2.56",
        "WAN DNS": "store-70000123.example.com",
        "Miền": "MN1",
        "Khu vực": "TM-HCM3",
        "Địa chỉ": "128 Đỗ Xuân Hợp, Phường Phước Long, TP.HCM",
    }
]

out = Path(__file__).resolve().parents[1] / "data" / "sample_stores.xlsx"
out.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_excel(out, index=False)
print(out)
