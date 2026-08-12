# -*- coding: utf-8 -*-
"""후보점 주변 **POI 문맥** — 그 run 이 실제로 쓴 STEP2 정제 데이터에서 만든다.

무엇이 문제였나 (2026-08-11 실측) — 예전 경로 `gis_service.get_poi_context_from_db`
는 파이프라인 산출물을 **한 건도 안 본다.** 별도로 적재된 테이블 6개를 봤다:

    parks 44행 · street_trash_bins 280 · smoking_areas 8 · bus_stop_passenger_stats 304
    cigarette_litter_hotspots **0행** · fire_water_facilities **0행**

⚠ 위 6개는 **이제 DB 에 없다**(2026-08-11 삭제 — 프리셋 원본을 디스크로 옮기면서
   흡연 도메인 데이터셋을 정리했다. 이슈 #215 계층 구분). 행 수는 삭제 직전 실측값이고
   되살리라는 뜻이 아니다 — **왜 이 모듈이 파일을 읽는지**의 근거로만 남긴다.

셋이 겹쳐 있었다.
  ① **도메인 하드코딩**(원칙 2). 테이블 6개도, 붙는 말머리도(`📍 대중교통`·
     `기존흡연구역`·`소방/안전`) 흡연부스 전용이다. 성동구 재활용정거장에서는
     엉뚱한 문맥이 나가거나 통째로 빈다 — **예외가 안 난다.**
  ② **run 과 무관**하다. 이 테이블들엔 `domain`·`run_id` 가 없다(적재 시각
     2026-07-14·08-03 — 파이프라인을 돌리기 전이다). 어떤 run 의 후보점을 물어도
     같은 답이 나온다. 업로드한 데이터로 뽑은 후보지를 **남의 데이터로** 설명한다.
  ③ 대응이 안 맞는다. STEP2 정제 11개 중 대응이 있는 건 2개뿐이고 그나마 행 수가
     다르다(07 버스정류소 314 ↔ 304 · 08 가로휴지통 281 ↔ 280). 2개는 0행이라
     **영원히 안 나오는데 매 토론마다 조회한다.**

여기서는 **그 후보점의 run 이 낸 STEP2 산출물**을 센다. 경로는 후보점 행에서 온다 —
`booth_candidates.run_id` 가 `'정본'` 이면 `datasets/step2_output/`, `r_…` 이면
`runs/<run_id>/step2/` 다. 프리픽스는 `booth_candidates.domain`.
요청으로 안 받는다: 파라미터로 받으면 흡연 후보점에 재활용 run 의 주변 문맥을 넘길 수 있다.

이름표는 **만들지 않는다.** `clean_report.json` 의 `label`(= 감리 `facility_type`)을
읽기만 한다. 감리가 이름을 안 낸 데이터셋은 `label` 이 `null` 인데, 그건 「없다」이지
「못 찾았다」가 아니다 — 그때는 **원본 파일명**을 쓰고 `label_source` 에 그렇게 적는다.
🔴 예전에 소비 측이 `{"01":"금연구역", …}` 고정 사전을 들고 있었고 흡연 실측에서
   **8개 중 4개가 틀렸다.** `dataset_id` 는 업로드 파일명 가나다순이라 도메인이 바뀌면
   번호가 다시 매겨진다 — 어떤 고정 사전도 다음 도메인에서 틀린다.

셀 수 없는 것은 **셌다고 하지 않는다**(원칙 4). 통계 테이블(`format: parquet`)은
좌표가 없어 못 세고, 정리된 run 은 `.gpkg` 가 지워져 있을 수 있다(`run_pruner`).
둘 다 `skipped` 에 사유와 함께 남고, 그 사실이 문자열에도 한 줄로 나간다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from app.config import STEP1_OUTPUT_DIR, STEP2_OUTPUT_DIR

logger = logging.getLogger("uvicorn.error")

# 정본 산출물의 run_id. 두 적재기(`load_audit_data`·`load_topn_candidates`)가 쓰는 값과
# 같아야 한다 — 어휘가 갈리면 후보점은 있는데 폴더를 못 찾는다.
FIXED_RUN_ID = "정본"

# 반경. 도메인 값이 아니라 **문맥의 넓이**라 원칙 2 에 안 걸린다.
# 옛 경로(`gis_service`)가 쓰던 300m 를 그대로 이어받았다 — 값을 바꾸면 토론 문맥이
# 바뀌므로 이어받은 것 자체를 여기 적어둔다.
DEFAULT_RADIUS_M = 300

# 계산 좌표계(저장소 규약: 계산 5186 / 저장 4326).
CALC_CRS = 5186


class Step2NotFound(Exception):
    """그 run 의 STEP2 산출물을 못 찾았다. 문맥을 지어내지 않고 호출자에게 알린다."""


def step2_dir_of(run_id: str) -> Path:
    """`booth_candidates.run_id` → STEP2 산출물 폴더. **판정은 여기 한 곳**이다."""
    if run_id == FIXED_RUN_ID:
        return Path(STEP2_OUTPUT_DIR)
    if run_id.startswith("r_"):
        # 러너와 같은 뿌리를 쓴다. 대조기가 `RUNS_ROOT` 를 갈아끼우면 여기도 따라간다.
        from app.services import pipeline_runner as R

        return R.run_dir(run_id) / "step2"
    raise Step2NotFound(
        f"run_id={run_id!r} 로는 STEP2 폴더를 정할 수 없다. "
        f"{FIXED_RUN_ID!r} 이거나 'r_' 로 시작해야 한다."
    )


def read_clean_report(run_id: str, domain: str) -> tuple[Path, dict]:
    """`<domain>_clean_report.json` 을 읽는다. 없으면 **비슷한 걸 찾지 않는다.**

    폴더에 파일이 하나뿐이어도 그걸 집어오면 안 된다 — 프리픽스가 다르다는 건
    도메인이 다르다는 뜻이고, 그러면 남의 데이터로 이 후보지를 설명하게 된다.
    """
    d = step2_dir_of(run_id)
    p = d / f"{domain}_clean_report.json"
    if not p.is_file():
        raise Step2NotFound(
            f"{p} 가 없다. run_id={run_id!r} · domain={domain!r} 의 STEP2 산출물을 "
            "찾을 수 없다(폴더가 지워졌거나 프리픽스가 다르다)."
        )
    return d, json.loads(p.read_text("utf-8"))


def labels_from_reviewed(run_id: str, domain: str) -> dict[str, str]:
    """`{dataset_id: facility_type}` — `clean_report.label` 의 **출처**를 직접 읽는다.

    다른 값이 아니라 **같은 값**이다. STEP2 가 `label` 을 적기 시작한 건 2026-08-11
    이라 그 전에 만들어진 `clean_report.json`(정본 포함)에는 키가 없다 — 정본을
    다시 정제하면 산출물이 덮이므로(회귀 기준선이 여기다) 되돌려 읽는 쪽을 택했다.

    없으면 **빈 dict**다. 여기서 못 읽었다고 이름을 지어내지 않는다.
    """
    if run_id == FIXED_RUN_ID:
        p = Path(STEP1_OUTPUT_DIR) / f"{domain}_audit_result_reviewed.json"
    else:
        from app.services import pipeline_runner as R

        p = R.run_dir(run_id) / "step1" / f"{domain}_audit_result_reviewed.json"
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text("utf-8"))
    except Exception as ex:  # noqa: BLE001
        logger.warning("[poi] %s 를 못 읽었다: %s", p, ex)
        return {}
    out: dict[str, str] = {}
    for r in doc.get("results", []):
        for role in r.get("roles", []) or []:
            if role.get("facility_type"):
                out[str(r.get("dataset_id"))] = role["facility_type"]
                break
    return out


def _label_of(entry: dict, fallback: dict[str, str]) -> tuple[str, str]:
    """(표시명, 출처). 감리가 이름을 안 냈으면 **파일명**을 쓴다 — 지어내지 않는다."""
    if entry.get("label"):
        return entry["label"], entry.get("label_source") or "audit.facility_type"
    ft = fallback.get(str(entry.get("dataset_id")))
    if ft:
        return ft, "audit.facility_type(reviewed)"
    fname = entry.get("filename")
    if fname:
        return os.path.splitext(fname)[0], "filename"
    return f"데이터셋 {entry.get('dataset_id')}", "dataset_id"


def _count_near(path: Path, buf, crs_hint: int = 4326) -> tuple[int, float | None]:
    """반경 안 개수 + 가장 가까운 거리(m). 거리는 토론에서 개수보다 자주 쓰인다."""
    import geopandas as gpd

    g = gpd.read_file(path)
    if g.empty:
        return 0, None
    if g.crs is None:
        # STEP2 는 4326 으로 저장한다(규약). 없으면 그 규약을 적용하되 추측임을 남긴다.
        g = g.set_crs(crs_hint)
    g = g.to_crs(CALC_CRS)
    hit = g[g.geometry.intersects(buf)]
    center = buf.centroid
    nearest = float(g.geometry.distance(center).min())
    return int(len(hit)), nearest


def _build(resolved: dict, radius_m: int) -> dict:
    from shapely.geometry import Point
    import geopandas as gpd

    domain, run_id = resolved["domain"], resolved["run_id"]
    d, report = read_clean_report(run_id, domain)
    fallback = labels_from_reviewed(run_id, domain)

    center = gpd.GeoSeries(
        [Point(resolved["lng"], resolved["lat"])], crs=4326
    ).to_crs(CALC_CRS).iloc[0]
    buf = center.buffer(radius_m)

    items: list[dict] = []
    skipped: list[dict] = []
    for e in report.get("results", []):
        did = e.get("dataset_id")
        label, src = _label_of(e, fallback)
        if not e.get("gis_input"):
            skipped.append({"dataset_id": did, "label": label,
                            "reason": "reference_only — 위치선정 입력이 아니다"})
            continue
        if e.get("format") != "gpkg":
            # 통계 테이블(승하차·생활인구)은 좌표가 없다. 다른 데이터셋에 조인되어
            # 이미 반영돼 있으므로 "0개"가 아니라 "못 셌다"가 맞다.
            skipped.append({"dataset_id": did, "label": label,
                            "reason": f"좌표 없음(format={e.get('format')})"})
            continue

        # `output` 의 절대경로를 그대로 쓰지 않는다 — 저장소를 옮기거나 run 폴더를
        # 복사하면 **다른 run 의 파일**을 가리킬 수 있다. 폴더는 우리가 정한 것으로 고정.
        f = d / os.path.basename(e.get("output") or "")
        if not f.is_file():
            skipped.append({"dataset_id": did, "label": label,
                            "reason": f"{f.name} 없음 — 정리(prune)됐거나 만들어지지 않았다"})
            continue
        try:
            n, near = _count_near(f, buf)
        except Exception as ex:  # noqa: BLE001 — 사유를 산출물에 남기고 계속한다
            logger.warning("[poi] %s 읽기 실패: %s", f, ex)
            skipped.append({"dataset_id": did, "label": label,
                            "reason": f"읽기 실패: {type(ex).__name__}"})
            continue
        items.append({"dataset_id": did, "label": label, "label_source": src,
                      "count": n, "nearest_m": None if near is None else round(near, 1)})

    lines = []
    for it in items:
        if it["count"]:
            lines.append(
                f"- {it['label']}: 반경 {radius_m}m 안 {it['count']}개"
                f" (가장 가까운 것 약 {int(it['nearest_m'])}m)"
            )
        else:
            near = it["nearest_m"]
            lines.append(
                f"- {it['label']}: 반경 {radius_m}m 안에 없음"
                + (f" (가장 가까운 것 약 {int(near)}m)" if near is not None else "")
            )
    if skipped:
        # 못 센 것을 안 적으면 "그 시설이 주변에 없다"로 읽힌다(원칙 4).
        lines.append(
            "- (세지 못한 데이터: "
            + " · ".join(f"{s['label']}({s['reason']})" for s in skipped)
            + ")"
        )

    return {
        "text": "\n".join(lines),
        "items": items,
        "skipped": skipped,
        "error": None,
        "source": {"run_id": run_id, "domain": domain,
                   "step2_dir": str(d), "radius_m": radius_m},
    }


async def build_poi_context(
    resolved: dict, *, radius_m: int = DEFAULT_RADIUS_M
) -> dict:
    """후보점 주변 문맥. 돌려주는 값:

        {"text": str, "items": [...], "skipped": [...], "error": str|None, "source": {...}}

    `text` 는 프롬프트에 그대로 들어가는 줄글이다. `items`·`skipped` 는 **무엇을 세고
    무엇을 못 셌는지**를 그대로 담는다 — 근거 스냅샷(`basis_snapshot`)에 실려
    「이 토론이 무엇을 보고 한 말인가」가 나중에도 읽힌다.

    🔴 `db` 가 아니라 **`resolve_candidate` 의 결과**를 받는다. 두 호출자(A `simulations.py` ·
       B `candidate_context.build_site_context`)가 이미 그 행을 들고 있는데 여기서 다시
       조회하면 **같은 후보점을 두 번 읽는 자리**가 생긴다 — 그 사이에 적재가 끼면
       문맥과 근거가 다른 run 을 가리킨다. 같은 행에서 뽑으면 어긋날 수가 없다.

    파일을 읽으므로 **`to_thread` 로 뺀다**(흡연 실측 0.5~1.3초). 이벤트 루프에서 그대로
    돌면 그 시간만큼 다른 요청·SSE 중계가 멈춘다.

    실패는 삼키지 않는다. 옛 경로는 예외를 `print` 하고 `""` 를 돌려줬는데, 그러면
    POI 가 통째로 빠진 채 5분짜리 토론이 완주하고 **왜 빠졌는지가 아무 데도 없다**.
    산출물 폴더를 못 찾은 경우(`Step2NotFound`)만 토론을 세우지 않고 이어가되,
    **못 만들었다는 사실과 사유를 `text`·`error` 양쪽에 적는다**(원칙 4). 나머지 예외는
    그대로 올린다 — 사유를 모르는 실패를 문맥인 척 내보내지 않는다.
    """
    try:
        return await asyncio.to_thread(_build, resolved, radius_m)
    except Step2NotFound as ex:
        logger.warning("[poi] 주변 문맥을 만들지 못했다: %s", ex)
        return {
            "text": f"- (주변 문맥을 만들지 못했다: {ex})",
            "items": [],
            "skipped": [],
            "error": str(ex),
            "source": {"run_id": resolved.get("run_id"),
                       "domain": resolved.get("domain"),
                       "step2_dir": None, "radius_m": radius_m},
        }
