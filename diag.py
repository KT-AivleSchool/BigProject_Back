from app.services.run_weight_model import make_loader

loader, report, report_path = make_loader("흡연")
print("REPORT PATH:", report_path)
for r in report.get("results", []):
    wr = r.get("whitelist_resolved")
    if wr:
        print(r["dataset_id"], "| len:", len(wr), "| keys:", list(wr[0].keys()))
