# -*- coding: utf-8 -*-
"""후보점 주변 POI 문맥 대조 — **무엇을 세고 무엇을 안 셌다고 말하는가**.

    python app\\tools\\check_poi_context.py

🔴 DB 도 uvicorn 도, 진짜 `data_임시/`·`runs/` 도 안 쓴다. 임시 폴더에 `clean_report.json`
   과 `.gpkg` 를 손으로 깔고 `STEP2_OUTPUT_DIR`·`RUNS_ROOT` 를 갈아끼운다 — 확인하려는 건
   「이 컴퓨터에 무엇이 있나」가 아니라 「어떤 입력에 어떤 말을 하는가」다.

여기서 제일 중요한 건 **개수가 아니라 못 센 것을 못 셌다고 말하는가**다. 옛 경로는
예외를 `print` 하고 `""` 를 돌려줘서, POI 가 통째로 빠진 채 5분짜리 토론이 완주했다.
「없다」와 「못 셌다」가 같은 모양으로 나가면 그 토론의 근거는 조용히 거짓이 된다.
"""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from app.services import pipeline_runner as R  # noqa: E402
from app.services import poi_context as P  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


DOMAIN = "대조도메인"
TMP = Path(tempfile.mkdtemp(prefix="omnisite_poi_"))
_REAL_STEP1, _REAL_STEP2, _REAL_RUNS = P.STEP1_OUTPUT_DIR, P.STEP2_OUTPUT_DIR, R.RUNS_ROOT

# 5186 기준 임의 원점. 여기서 100m·500m 떨어진 점을 만들어 반경 300m 판정을 본다.
CX, CY = 197000.0, 448000.0
CENTER = (
    gpd.GeoSeries([Point(CX, CY)], crs=P.CALC_CRS).to_crs(4326).iloc[0]
)
RESOLVED = {"domain": DOMAIN, "run_id": P.FIXED_RUN_ID,
            "lat": CENTER.y, "lng": CENTER.x}


def write_gpkg(path: Path, offsets_m: list[float]) -> None:
    g = gpd.GeoDataFrame(
        {"i": list(range(len(offsets_m)))},
        geometry=[Point(CX + d, CY) for d in offsets_m],
        crs=P.CALC_CRS,
    ).to_crs(4326)
    path.parent.mkdir(parents=True, exist_ok=True)
    g.to_file(path, driver="GPKG")


def build(step2: Path, *, results: list[dict]) -> None:
    step2.mkdir(parents=True, exist_ok=True)
    (step2 / f"{DOMAIN}_clean_report.json").write_text(
        json.dumps({"results": results}, ensure_ascii=False), "utf-8"
    )


def run(coro):
    return asyncio.run(coro)


try:
    STEP2 = TMP / "step2_output"
    STEP1 = TMP / "step1_output"
    P.STEP2_OUTPUT_DIR = str(STEP2)
    P.STEP1_OUTPUT_DIR = str(STEP1)
    R.RUNS_ROOT = TMP / "runs"

    # ══════════════════════════════════════════════════════════
    print("--- 1) 폴더 판정 — run_id 하나로 정해지는가")
    chk("정본은 step2_output", P.step2_dir_of("정본") == STEP2)
    chk("격리 run 은 runs/<id>/step2",
        P.step2_dir_of("r_20260101_001") == R.RUNS_ROOT / "r_20260101_001" / "step2")
    for bad in ("", "step2_output", "20260101", "정본2"):
        try:
            P.step2_dir_of(bad)
            got = False
        except P.Step2NotFound:
            got = True
        chk(f"{bad!r} 은 폴더를 못 정한다고 말한다", got)

    # ══════════════════════════════════════════════════════════
    print("--- 2) 리포트 — 비슷한 걸 집어오지 않는다")
    build(STEP2, results=[])
    d, rep = P.read_clean_report("정본", DOMAIN)
    chk("맞는 프리픽스는 읽는다", d == STEP2 and rep == {"results": []})
    raised = False
    try:
        P.read_clean_report("정본", "다른도메인")
    except P.Step2NotFound:
        raised = True
    chk("폴더에 파일이 하나뿐이어도 프리픽스가 다르면 안 집어온다", raised)

    # ══════════════════════════════════════════════════════════
    print("--- 3) 이름표 — 지어내지 않고 출처를 남기는가")
    write_gpkg(STEP2 / "a.gpkg", [0.0, 100.0, 500.0])
    write_gpkg(STEP2 / "b.gpkg", [500.0])
    write_gpkg(STEP2 / "c.gpkg", [50.0])
    RESULTS = [
        # ⓐ STEP2 가 적어준 label 을 그대로 쓴다
        {"dataset_id": "01", "filename": "raw_a.csv", "output": str(STEP2 / "a.gpkg"),
         "format": "gpkg", "gis_input": True, "label": "금연구역",
         "label_source": "audit.facility_type"},
        # ⓑ label 이 없다 → reviewed 에서 같은 값을 되읽는다
        {"dataset_id": "05", "filename": "raw_b.csv", "output": str(STEP2 / "b.gpkg"),
         "format": "gpkg", "gis_input": True},
        # ⓒ 둘 다 없다 → 파일명. 이름이 **없는 것**이지 못 찾은 게 아니다
        {"dataset_id": "10", "filename": "raw_c.csv", "output": str(STEP2 / "c.gpkg"),
         "format": "gpkg", "gis_input": True},
        # ⓓ 파일명조차 없다
        {"dataset_id": "12", "output": str(STEP2 / "c.gpkg"),
         "format": "gpkg", "gis_input": True},
        # ⓔ 좌표가 없다(통계 테이블) — "0개"가 아니라 "못 셌다"
        {"dataset_id": "02", "filename": "raw_d.csv", "output": str(STEP2 / "d.parquet"),
         "format": "parquet", "gis_input": True},
        # ⓕ 위치선정 입력이 아니다
        {"dataset_id": "03", "filename": "raw_e.csv", "output": str(STEP2 / "e.gpkg"),
         "format": "gpkg", "gis_input": False},
        # ⓖ 파일이 없다(정리됐거나 안 만들어졌다)
        {"dataset_id": "04", "filename": "raw_f.csv", "output": str(STEP2 / "f.gpkg"),
         "format": "gpkg", "gis_input": True},
    ]
    build(STEP2, results=RESULTS)
    STEP1.mkdir(parents=True, exist_ok=True)
    (STEP1 / f"{DOMAIN}_audit_result_reviewed.json").write_text(
        json.dumps({"results": [
            {"dataset_id": "05", "roles": [{"role": "hard_exclusion",
                                            "facility_type": "어린이집"}]},
            {"dataset_id": "10", "roles": [{"role": "positive_factor"}]},
        ]}, ensure_ascii=False), "utf-8")

    res = run(P.build_poi_context(RESOLVED))
    by = {i["dataset_id"]: i for i in res["items"]}
    sk = {s["dataset_id"]: s for s in res["skipped"]}

    chk("01 은 clean_report 의 label 을 쓴다",
        by["01"]["label"] == "금연구역"
        and by["01"]["label_source"] == "audit.facility_type")
    chk("05 는 reviewed 에서 되읽고 그렇게 적는다",
        by["05"]["label"] == "어린이집"
        and by["05"]["label_source"] == "audit.facility_type(reviewed)",
        f"({by['05']['label']}/{by['05']['label_source']})")
    chk("10 은 이름이 없어 파일명을 쓴다",
        by["10"]["label"] == "raw_c" and by["10"]["label_source"] == "filename")
    chk("12 는 파일명조차 없어 dataset_id 로 말한다",
        by["12"]["label_source"] == "dataset_id", f"({by['12']['label']})")
    chk("이름을 지어낸 항목이 없다",
        all(i["label_source"] in ("audit.facility_type",
                                 "audit.facility_type(reviewed)",
                                 "filename", "dataset_id") for i in res["items"]))

    # ══════════════════════════════════════════════════════════
    print("--- 4) 개수 — 반경 안팎을 가르는가")
    chk("반경 300m 안 2개(0m·100m), 500m 는 뺀다", by["01"]["count"] == 2,
        f"({by['01']['count']})")
    chk("가장 가까운 것은 0m", by["01"]["nearest_m"] == 0.0)
    chk("전부 밖이면 0개인데 최단거리는 말해준다",
        by["05"]["count"] == 0 and round(by["05"]["nearest_m"]) == 500,
        f"({by['05']['count']}/{by['05']['nearest_m']})")
    chk("0개도 items 에 남는다 — 뺐다면 '안 물어봤다'와 구분이 안 된다",
        "05" in by)

    # ══════════════════════════════════════════════════════════
    print("--- 5) 못 센 것 — 없는 것과 구분되는가")
    chk("좌표 없는 통계 테이블은 skipped",
        "02" in sk and "format=parquet" in sk["02"]["reason"], f"({sk.get('02')})")
    chk("reference_only 는 skipped",
        "03" in sk and "reference_only" in sk["03"]["reason"])
    chk("파일이 없으면 skipped 이고 사유가 정리(prune)를 가리킨다",
        "04" in sk and "prune" in sk["04"]["reason"], f"({sk.get('04')})")
    chk("skipped 는 items 에 안 섞인다", not (set(sk) & set(by)))
    chk("못 센 사실이 줄글에도 나간다",
        "세지 못한 데이터" in res["text"]
        and all(s["label"] in res["text"] for s in res["skipped"]))
    chk("0개는 '없음' 으로, 못 센 것은 '없음' 으로 안 말한다",
        "어린이집: 반경 300m 안에 없음" in res["text"]
        and "raw_d: 반경 300m 안에 없음" not in res["text"])
    chk("출처를 같이 낸다",
        res["source"] == {"run_id": "정본", "domain": DOMAIN,
                          "step2_dir": str(STEP2), "radius_m": 300})
    chk("정상일 때 error 는 None", res["error"] is None)

    # ══════════════════════════════════════════════════════════
    print("--- 6) 못 만들었을 때 — 조용히 비우지 않는가")
    gone = run(P.build_poi_context({**RESOLVED, "domain": "없는도메인"}))
    chk("error 에 사유가 남는다", bool(gone["error"]))
    chk("줄글도 '못 만들었다'고 말한다",
        "만들지 못했다" in gone["text"] and gone["text"] != "")
    chk("빈 문자열을 돌려주지 않는다 — 옛 경로가 그래서 안 걸렸다",
        gone["text"].strip() != "")
    chk("셌다고 말하지 않는다", gone["items"] == [])

    # 사유를 모르는 실패는 **올린다.** 문맥인 척 내보내면 그 토론은 조용히 틀린다.
    raised = False
    try:
        run(P.build_poi_context({"domain": DOMAIN, "run_id": "정본",
                                 "lat": None, "lng": None}))
    except Exception:  # noqa: BLE001 — 종류를 안 가린다는 것 자체가 요점이다
        raised = True
    chk("좌표가 없으면 raise 한다(문맥인 척 안 한다)", raised)

    # ══════════════════════════════════════════════════════════
    print("--- 7) 격리 run — 그 run 폴더를 본다")
    rdir = R.run_dir("r_20260101_001") / "step2"
    write_gpkg(rdir / "a.gpkg", [10.0])
    build(rdir, results=[
        {"dataset_id": "01", "filename": "raw_a.csv", "output": str(rdir / "a.gpkg"),
         "format": "gpkg", "gis_input": True, "label": "격리라벨",
         "label_source": "audit.facility_type"}])
    r2 = run(P.build_poi_context({**RESOLVED, "run_id": "r_20260101_001"}))
    chk("run 폴더의 산출물을 센다",
        r2["items"][0]["label"] == "격리라벨" and r2["items"][0]["count"] == 1,
        f"({r2['items'][0]})")
    chk("출처에 그 run 이 적힌다", r2["source"]["run_id"] == "r_20260101_001")

    # 🔴 `output` 의 절대경로를 그대로 믿으면 폴더를 복사·이동했을 때 **남의 run 파일**을
    #    센다. 우리가 정한 폴더 + 파일명으로만 닿는지 본다.
    build(rdir, results=[
        {"dataset_id": "01", "filename": "raw_a.csv",
         "output": str(STEP2 / "a.gpkg"),  # 정본 파일을 가리키게 해둔다(3개 들어 있다)
         "format": "gpkg", "gis_input": True, "label": "격리라벨",
         "label_source": "audit.facility_type"}])
    r3 = run(P.build_poi_context({**RESOLVED, "run_id": "r_20260101_001"}))
    chk("output 의 절대경로가 남의 run 을 가리켜도 자기 폴더에서 찾는다",
        r3["items"][0]["count"] == 1, f"({r3['items'][0]['count']})")

finally:
    P.STEP1_OUTPUT_DIR, P.STEP2_OUTPUT_DIR = _REAL_STEP1, _REAL_STEP2
    R.RUNS_ROOT = _REAL_RUNS
    shutil.rmtree(TMP, ignore_errors=True)

chk("진짜 산출물 폴더는 손대지 않았다",
    P.STEP2_OUTPUT_DIR == _REAL_STEP2 and R.RUNS_ROOT == _REAL_RUNS
    and not TMP.exists())

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
