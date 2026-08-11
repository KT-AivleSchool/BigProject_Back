import json
import os
from pathlib import Path
import pandas as pd
import geopandas as gpd

from app.config import (
    STEP1_OUTPUT_DIR,
    STEP2_OUTPUT_DIR,
    STEP3_OUTPUT_DIR,
    STEP4_OUTPUT_DIR,
    domain_prefix,
)
from app.db.session import AsyncSessionLocal
from app.db.models.pipeline_export import (
    PipelineAuditReview,
    CleanSpatialLayer,
    CleanStatTable,
    PipelineCleanReport,
    PipelineWeightSet,
    CandidateParcel,
    SelectedTopnSite,
    PipelineFinalReport,
)
import redis.asyncio as aioredis
from app.api.deps import redis_pool

redis_client = aioredis.Redis(connection_pool=redis_pool)



def _get_run_dir(run_id: str) -> Path:
    return Path(f"runs/{run_id}")


async def export_step1_to_db(run_id: str, domain: str) -> bool:
    """STEP 1: 감리 확정 정본(reviewed.json) DB 저장 & 1차/증강 감리 Redis 캐시"""
    pre = domain_prefix(domain)
    rdir = _get_run_dir(run_id) / "step1"
    rev_path = rdir / f"{pre}_audit_result_reviewed.json"
    if not rev_path.is_file():
        # 폴백 경로 체킹
        rev_path = Path(STEP1_OUTPUT_DIR) / f"{pre}_audit_result_reviewed.json"

    if rev_path.is_file():
        with open(rev_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        async with AsyncSessionLocal() as session:
            review_rec = PipelineAuditReview(
                run_id=run_id,
                domain=domain,
                reviewed_data=data,
                reviewed_by="HITL_Reviewer",
            )
            session.add(review_rec)
            await session.commit()

    # Redis 중간물 7일 TTL 캐시
    audit_path = rdir / f"{pre}_audit_result.json"
    if audit_path.is_file():
        with open(audit_path, "r", encoding="utf-8") as f:
            await redis_client.set(
                f"omnisite:run:{run_id}:step1:audit_result",
                f.read(),
                ex=604800
            )

    enriched_path = rdir / f"{pre}_audit_result_enriched.json"
    if enriched_path.is_file():
        with open(enriched_path, "r", encoding="utf-8") as f:
            await redis_client.set(
                f"omnisite:run:{run_id}:step1:enriched",
                f.read(),
                ex=604800
            )
    return True


async def export_step2_to_db(run_id: str, domain: str) -> bool:
    """STEP 2: 정제 공간데이터(gpkg) PostGIS, 통계표(parquet) PG & 종합 리포트 DB 적재"""
    pre = domain_prefix(domain)
    rdir = _get_run_dir(run_id) / "step2"
    rpt_path = rdir / f"{pre}_clean_report.json"
    if not rpt_path.is_file():
        rpt_path = Path(STEP2_OUTPUT_DIR) / f"{pre}_clean_report.json"

    async with AsyncSessionLocal() as session:
        if rpt_path.is_file():
            with open(rpt_path, "r", encoding="utf-8") as f:
                rpt_data = json.load(f)
            datasets = rpt_data.get("datasets", [])
            clean_rpt = PipelineCleanReport(
                run_id=run_id,
                domain=domain,
                total_datasets=len(datasets),
                report_data=rpt_data,
            )
            session.add(clean_rpt)

        # 디렉터리 내 .gpkg 및 .parquet 데이터셋 DB 이식
        step2_dir = rdir if rdir.is_dir() else Path(STEP2_OUTPUT_DIR)
        for fpath in step2_dir.glob("*.gpkg"):
            fname = fpath.name
            did = fname.split("_clean_")[-1].replace(".gpkg", "") if "_clean_" in fname else "unknown"
            try:
                gdf = gpd.read_file(fpath)
                gdf = gdf.to_crs(epsg=4326)
                for _, row in gdf.iterrows():
                    geom_wkt = row.geometry.wkt if row.geometry else None
                    if not geom_wkt:
                        continue
                    props = {k: v for k, v in row.items() if k != "geometry"}
                    # serializable 변환
                    props_json = json.loads(pd.Series(props).to_json())
                    layer_rec = CleanSpatialLayer(
                        run_id=run_id,
                        dataset_id=did,
                        layer_name=fname,
                        geom=f"SRID=4326;{geom_wkt}",
                        properties=props_json,
                    )
                    session.add(layer_rec)
            except Exception as e:
                print(f"[export_step2] gpkg {fname} 이식 경고: {e}")

        for fpath in step2_dir.glob("*.parquet"):
            fname = fpath.name
            did = fname.split("_clean_")[-1].replace(".parquet", "") if "_clean_" in fname else "unknown"
            try:
                df = pd.read_parquet(fpath)
                for _, row in df.iterrows():
                    stat_json = json.loads(row.to_json())
                    dong_cd = str(stat_json.get("adm_dong_cd") or stat_json.get("dong_cd") or "")
                    stat_rec = CleanStatTable(
                        run_id=run_id,
                        dataset_id=did,
                        adm_dong_cd=dong_cd if dong_cd else None,
                        stat_data=stat_json,
                    )
                    session.add(stat_rec)
            except Exception as e:
                print(f"[export_step2] parquet {fname} 이식 경고: {e}")

        await session.commit()
    return True


async def export_step3_to_db(run_id: str, domain: str) -> bool:
    """STEP 3: 가중치 세트(weight_set.json) & 후보 필지(gpkg) DB/PostGIS 적재"""
    pre = domain_prefix(domain)
    rdir = _get_run_dir(run_id) / "step3"
    ws_path = rdir / f"{pre}_weight_set.json"
    if not ws_path.is_file():
        ws_path = Path(STEP3_OUTPUT_DIR) / f"{pre}_weight_set.json"

    async with AsyncSessionLocal() as session:
        if ws_path.is_file():
            with open(ws_path, "r", encoding="utf-8") as f:
                ws_data = json.load(f)
            decay_val = ws_data.get("decay")
            weight_rec = PipelineWeightSet(
                run_id=run_id,
                domain=domain,
                alpha=ws_data.get("alpha"),
                decay=decay_val,
                weight_data=ws_data,
            )

            session.add(weight_rec)

        # 후보 필지 gpkg 적재
        cands_gpkg = rdir / f"{pre}_후보_지적도필지.gpkg"
        if not cands_gpkg.is_file():
            cands_gpkg = Path(STEP3_OUTPUT_DIR) / f"{pre}_후보_지적도필지.gpkg"

        if cands_gpkg.is_file():
            try:
                gdf = gpd.read_file(cands_gpkg, layer="parcels")
                for _, row in gdf.iterrows():
                    geom_wkt = row.geometry.wkt if row.geometry else None
                    if not geom_wkt:
                        continue
                    pnu = str(row.get("pnu") or row.get("PNU") or "unknown")
                    attrs = {k: v for k, v in row.items() if k != "geometry"}
                    attrs_json = json.loads(pd.Series(attrs).to_json())
                    cand_rec = CandidateParcel(
                        run_id=run_id,
                        pnu=pnu,
                        geom=f"SRID=5186;{geom_wkt}",
                        attributes=attrs_json,
                    )
                    session.add(cand_rec)
            except Exception as e:
                print(f"[export_step3] 후보 필지 적재 경고: {e}")

        await session.commit()
    return True


async def export_step4_to_db(run_id: str, domain: str) -> bool:
    """STEP 4: 추천 입지(topN.geojson) PostGIS & 최종 심의 보고서 DB 적재"""
    pre = domain_prefix(domain)
    rdir = _get_run_dir(run_id) / "step4"
    rpt_path = rdir / f"{pre}_report.json"
    if not rpt_path.is_file():
        rpt_path = Path(STEP4_OUTPUT_DIR) / f"{pre}_report.json"

    async with AsyncSessionLocal() as session:
        if rpt_path.is_file():
            with open(rpt_path, "r", encoding="utf-8") as f:
                rpt_data = json.load(f)
            topn_list = rpt_data.get("topn", [])
            top1_cand = topn_list[0] if topn_list and isinstance(topn_list, list) else {}
            top_pnu = str(top1_cand.get("PNU") or top1_cand.get("pnu") or "") if top1_cand else None
            final_rpt = PipelineFinalReport(
                run_id=run_id,
                domain=domain,
                facility=rpt_data.get("facility", domain),
                top_candidate_pnu=top_pnu if top_pnu else None,
                report_data=rpt_data,
            )
            session.add(final_rpt)


        # topN.geojson PostGIS 적재
        topn_path = rdir / f"{pre}_topN.geojson"
        if not topn_path.is_file():
            topn_path = Path(STEP4_OUTPUT_DIR) / f"{pre}_topN.geojson"

        if topn_path.is_file():
            try:
                gdf = gpd.read_file(topn_path)
                for idx, row in gdf.iterrows():
                    geom_wkt = row.geometry.wkt if row.geometry else None
                    if not geom_wkt:
                        continue
                    rank = int(row.get("rank") or (idx + 1))
                    pnu = str(row.get("pnu") or row.get("PNU") or "")
                    score = float(row.get("total_score") or row.get("score") or 0.0)
                    scores_json = json.loads(pd.Series({k: v for k, v in row.items() if k != "geometry"}).to_json())
                    site_rec = SelectedTopnSite(
                        run_id=run_id,
                        rank=rank,
                        pnu=pnu,
                        total_score=score,
                        geom=f"SRID=5186;{geom_wkt}",
                        indicator_scores=scores_json,
                    )
                    session.add(site_rec)
            except Exception as e:
                print(f"[export_step4] topN 적재 경고: {e}")

        await session.commit()
    return True
