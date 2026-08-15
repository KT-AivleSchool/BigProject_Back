# -*- coding: utf-8 -*-
"""S5 선행 검증 — geopandas/shapely 와 PostGIS 가 **같은 답을 내는가**.

왜 필요한가
  S5 는 공간 연산을 PostGIS 로 옮기는 작업이다. 옮긴 뒤에 결과가 달라지면
  "DB 로 바꿨더니 Top-N 이 바뀌었는데 왜인지 모른다"가 된다. 그래서 **옮기기 전에**
  술어 단위로 같은 답이 나오는지 재둔다.

무엇을 재나 (S5 문서가 지목한 두 가지)
  T1 `inscribed_width`  ↔ `ST_MaximumInscribedCircle`
     폭 2m 필터가 후보 수를 직접 좌우한다(실측 66,915 → 59,989). 꼬리가 곧 결과다.
  T2 `neighbors_within` ↔ `ST_DWithin`
     "커버 쌍 개수가 가장 예민한 지표다"(S5 문서). 쌍이 어긋나면 그 아래가 전부 무의미.

🔴 진짜 변수는 PostGIS 가 아니라 **GEOS 버전**이다.
  둘 다 GEOS 를 부른다. shapely 2.1 은 GEOS 3.13, postgis/postgis:16-3.4 는 GEOS 3.9.
  차이가 나온다면 "PostGIS 라서"가 아니라 "GEOS 가 4버전 차이라서"다.
  둘을 섞어 결론내면 S5 설계를 잘못 잡는다 — 그래서 양쪽 버전을 같이 찍는다.

실행
  docker run -d --name omnisite-postgis -e POSTGRES_PASSWORD=omnisite \\
      -e POSTGRES_DB=omnisite -p 55432:5432 postgis/postgis:16-3.4
  set PGIS_DSN=postgresql://postgres:omnisite@127.0.0.1:55432/omnisite
  python app/tools/check_postgis_parity.py 흡연
"""

from __future__ import annotations

import os
import sys

# `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import geopandas as gpd
import numpy as np
import psycopg
import shapely

from app.config import STEP3_OUTPUT_DIR, domain_prefix
from app.services import gam4_spatial_ops as S

DSN = os.environ.get(
    "PGIS_DSN", "postgresql://postgres:omnisite@127.0.0.1:55432/omnisite"
)
WORK_SRID = 5186  # 계산 좌표계. 저장 4326 과 다르다(규약)
R_COVER = 300.0  # 흡연 픽스처의 서비스 반경
MIC_TOL = 0.05  # inscribed_width 가 쓰는 tolerance 와 같아야 한다


def _stat(a: np.ndarray) -> str:
    q = np.quantile(a, [0.0, 0.5, 0.95, 1.0])
    return (
        f"min {q[0]:.4f}  중앙 {q[1]:.4f}  p95 {q[2]:.4f}  "
        f"max {q[3]:.4f}  합 {a.sum():.4f}"
    )


def _push(cur, table: str, geoms, gtype: str) -> None:
    """WKB 로 밀어넣는다. 텍스트(WKT)로 오가면 반올림이 끼어 대조가 오염된다."""
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TABLE {table} (id int, geom geometry({gtype},{WORK_SRID}))")
    wkb = shapely.to_wkb(np.asarray(geoms), hex=True)
    with cur.copy(f"COPY {table} (id, geom) FROM STDIN") as cp:
        for i, w in enumerate(wkb):
            cp.write_row((i, w))
    cur.execute(f"CREATE INDEX ON {table} USING GIST (geom)")
    cur.execute(f"ANALYZE {table}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    domain = sys.argv[1]
    pre = domain_prefix(domain)
    cpath = os.path.join(str(STEP3_OUTPUT_DIR), f"{pre}_후보_지적도필지.gpkg")
    if not os.path.exists(cpath):
        raise FileNotFoundError(f"후보 gpkg 없음: {cpath}")

    parcels = gpd.read_file(cpath, layer="parcels").to_crs(WORK_SRID)
    print("=" * 88)
    print(f"[PostGIS 정합성] {domain}   필지 {len(parcels):,}")

    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT postgis_version(), postgis_geos_version(), version()")
        pv, pgeos, pgver = cur.fetchone()
        print("-" * 88)
        print(
            f"  shapely {shapely.__version__}  GEOS "
            f"{'.'.join(map(str, shapely.geos_version))}"
        )
        print(f"  PostGIS {pv.split()[0]}  GEOS {pgeos.split('-')[0]}")
        print(f"  {pgver.split(',')[0]}")
        if pgeos.split("-")[0] != ".".join(map(str, shapely.geos_version)):
            print("  ⚠ GEOS 버전이 다르다 — 아래 차이는 PostGIS 탓이 아닐 수 있다")

        # ── T1 내접폭 ──────────────────────────────────────────────
        print("-" * 88)
        print("T1  inscribed_width ↔ ST_MaximumInscribedCircle")
        _push(cur, "t_parcels", parcels.geometry.values, "Geometry")
        gpd_w = S.inscribed_width(parcels, tol=MIC_TOL).to_numpy(dtype=float)
        # ⚠ 반환은 (center, nearest, radius) 레코드다. `nearest` 는 **경계 위의 점**이라
        #   ST_Length 를 씌우면 전부 0 이 나온다(처음에 그렇게 짜서 42,216건 전부
        #   '불일치'로 보였다 — 술어 차이가 아니라 내 쿼리 오류였다).
        #   shapely 쪽은 중심→최근접점 LineString 이라 length×2 = 지름이고,
        #   PostGIS 는 radius 를 직접 주므로 ×2 하면 같은 축이 된다.
        cur.execute("""
            SELECT id, (ST_MaximumInscribedCircle(geom)).radius * 2.0
            FROM t_parcels ORDER BY id
        """)
        rows = cur.fetchall()
        if len(rows) != len(gpd_w):
            print(f"  🔴 행 수 불일치 {len(rows)} vs {len(gpd_w)}")
            return 1
        pg_w = np.array([r[1] for r in rows], dtype=float)

        d = np.abs(gpd_w - pg_w)
        print(f"  geopandas  {_stat(gpd_w)}")
        print(f"  postgis    {_stat(pg_w)}")
        print(
            f"  |차이|     max {d.max():.6f}m  중앙 {np.median(d):.6f}m  "
            f"평균 {d.mean():.6f}m"
        )
        # tolerance 이내는 '같다'로 본다 — MIC 는 근사 알고리즘이라 정확히 같을 수 없다.
        print(f"  tol({MIC_TOL}m) 초과 {int((d > MIC_TOL).sum()):,} / {len(d):,}")
        for w in (2.0,):
            a, b = int((gpd_w >= w).sum()), int((pg_w >= w).sum())
            mark = "✅" if a == b else "🔴"
            print(
                f"  {mark} 폭 {w:g}m 이상 통과   geopandas {a:,}   postgis {b:,}"
                f"   차이 {b - a:+,}"
            )

        # ── T2 ST_DWithin ────────────────────────────────────────
        print("-" * 88)
        print(f"T2  neighbors_within ↔ ST_DWithin   (R={R_COVER:g}m)")
        # 후보점·수요격자를 실제 파이프라인 함수로 만든다. 산출물에서 읽어 오면
        # 4326 왕복이 끼어 좌표가 미세하게 달라지고, 그 차이를 술어 차이로 오인한다.
        from app.services.gam4_site_select import build_demand_grid

        pts = S.points_in_parcels(
            parcels, spacing=20, max_per_parcel=400, verbose=False
        )
        dgrid = build_demand_grid(cpath, parcels, 50)
        print(f"  후보점 {len(pts):,}  ×  수요점 {len(dgrid):,}")

        ci, tj, _ = S.neighbors_within(pts, dgrid, R_COVER)
        _push(cur, "t_cand", pts.geometry.values, "Point")
        _push(cur, "t_dem", dgrid.geometry.values, "Point")
        cur.execute(
            """
            SELECT count(*) FROM t_cand c JOIN t_dem d
            ON ST_DWithin(c.geom, d.geom, %s)
        """,
            (R_COVER,),
        )
        pg_pairs = cur.fetchone()[0]
        gp_pairs = int(len(ci))
        mark = "✅" if pg_pairs == gp_pairs else "🔴"
        print(
            f"  {mark} 커버 쌍   geopandas {gp_pairs:,}   postgis {pg_pairs:,}"
            f"   차이 {pg_pairs - gp_pairs:+,}"
        )

        # 도달 상한도 같이 본다 — 쌍 개수가 같아도 짝이 다를 수 있다.
        cur.execute(
            """
            SELECT count(DISTINCT d.id) FROM t_cand c JOIN t_dem d
            ON ST_DWithin(c.geom, d.geom, %s)
        """,
            (R_COVER,),
        )
        pg_reached = cur.fetchone()[0]
        gp_reached = int(len(np.unique(tj)))
        mark = "✅" if pg_reached == gp_reached else "🔴"
        print(
            f"  {mark} 도달 수요점  geopandas {gp_reached:,}   "
            f"postgis {pg_reached:,}   차이 {pg_reached - gp_reached:+,}"
        )

    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
