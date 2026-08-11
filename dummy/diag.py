# 🔴 `dummy/` 안에 있으므로 저장소 루트는 **두 단계 위**다(2026-08-10 이동).
#    `python dummy/diag.py` 로 부르면 sys.path[0] 이 `dummy/` 라 `import app…` 이 안 된다.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.run_weight_model import make_loader  # noqa: E402

loader, report, report_path = make_loader("흡연")
print("REPORT PATH:", report_path)
for r in report.get("results", []):
    wr = r.get("whitelist_resolved")
    if wr:
        print(r["dataset_id"], "| len:", len(wr), "| keys:", list(wr[0].keys()))
