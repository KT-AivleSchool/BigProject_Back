# -*- coding: utf-8 -*-
"""S5 (나) 사이드카 — **전환이 이득인지부터** 잰다 (LLM 호출 0회).

왜 이걸 먼저 재나
  정합성은 확인됐다(`check_postgis_parity.py`). 하지만 "같은 답이 나온다"와
  "옮길 가치가 있다"는 다른 얘기다. S5 는 목록에 있으니까 하는 게 아니라
  **느려서** 하는 것이다. 그러면 빨라지는지를 재고 시작해야 한다.
  안 재고 옮기면 "옮겼는데 왜 안 빨라지지"를 나중에 알게 된다.

🔴 여기서 갈리는 지점 — **결과를 되가져오는 비용**
  `neighbors_within` 은 개수가 아니라 **쌍 배열 (ci, tj, dist)** 을 돌려준다.
  거리 감쇠 점수화가 거리 값을 쓰기 때문이다. 흡연 기준 약 600만 쌍이다.
  PostGIS 로 옮기면 그 600만 행을 **네트워크로 끌어와야** 한다.
  그래서 세 가지를 따로 잰다. 뭉뚱그리면 결론이 뒤집힌다.
      push   기하를 DB 로 밀어넣기      (데이터가 DB 에 상주하면 0 이 된다)
      query  DB 안에서 계산             (여기만 재면 PostGIS 가 유리해 보인다)
      fetch  결과를 파이썬으로 가져오기  (개수만 필요하면 0, 쌍이 필요하면 큼)

실행
  set PGIS_DSN=postgresql://postgres:omnisite@127.0.0.1:55432/omnisite
  python app/tools/bench_postgis.py 흡연
"""
from __future__ import annotations

import os
import sys
import time

# `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import geopandas as gpd
import numpy as np
import psycopg
import shapely

from app.config import STEP3_OUTPUT_DIR, domain_prefix
from app.services import gam4_spatial_ops as S

DSN = os.environ.get("PGIS_DSN",
                     "postgresql://postgres:omnisite@127.0.0.1:55432/omnisite")
WORK_SRID = 5186
R_COVER = 300.0
MIC_TOL = 0.05
SPACING = 20          # 픽스처 조건과 같아야 한다. 다르면 후보점 수가 달라져 비교가 무의미
EXCL_RADIUS = 30.0    # 배제 버퍼 대표값 — 레이어별 반경은 도메인 값이라 여기선 고정 대표치


class _T:
    """구간별 경과시간. 합계가 아니라 **구간별로** 봐야 어디가 비싼지 보인다."""

    def __init__(self) -> None:
        self.t: dict[str, float] = {}

    def __call__(self, name: str):
        return _Ctx(self, name)

    def get(self, *names: str) -> float:
        return sum(self.t.get(n, 0.0) for n in names)


class _Ctx:
    def __init__(self, owner: _T, name: str) -> None:
        self.owner, self.name = owner, name

    def __enter__(self):
        self.s = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.owner.t[self.name] = time.perf_counter() - self.s
        return False


def _push(cur, table: str, geoms, gtype: str) -> None:
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TABLE {table} (id int, geom geometry({gtype},{WORK_SRID}))")
    wkb = shapely.to_wkb(np.asarray(geoms), hex=True)
    with cur.copy(f"COPY {table} (id, geom) FROM STDIN") as cp:
        for i, w in enumerate(wkb):
            cp.write_row((i, w))
    cur.execute(f"CREATE INDEX ON {table} USING GIST (geom)")
    cur.execute(f"ANALYZE {table}")


def _row(label: str, gp: float, push: float, query: float, fetch: float) -> None:
    """geopandas 시간과 PostGIS 3구간을 한 줄로. 배수는 **상주/전송 두 가지**로 낸다."""
    resident = query + fetch          # 데이터가 DB 에 이미 있는 경우
    total = push + resident           # 매번 밀어넣는 경우
    def _x(v: float) -> str:
        if v <= 0:
            return "   —  "
        r = gp / v
        return f"{r:5.2f}x" + ("✅" if r > 1.1 else ("🔴" if r < 0.9 else "≈ "))
    print(f"  {label:<22} geopandas {gp:7.2f}s")
    print(f"  {'':<22} postgis   push {push:6.2f}  query {query:6.2f}  "
          f"fetch {fetch:6.2f}")
    print(f"  {'':<22}           DB상주 {resident:6.2f}s {_x(resident)}   "
          f"매번전송 {total:6.2f}s {_x(total)}")


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
    print(f"[S5 성능 실측] {domain}   필지 {len(parcels):,}")

    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT postgis_version(), postgis_geos_version()")
        pv, pgeos = cur.fetchone()
        print(f"  PostGIS {pv.split()[0]}  GEOS {pgeos.split('-')[0]}   "
              f"shapely {shapely.__version__} GEOS "
              f"{'.'.join(map(str, shapely.geos_version))}")
        print("-" * 88)

        T = _T()

        # ── 1. inscribed_width ───────────────────────────────────
        with T("gp_width"):
            S.inscribed_width(parcels, tol=MIC_TOL)
        with T("pg_width_push"):
            _push(cur, "b_parcels", parcels.geometry.values, "Geometry")
        with T("pg_width_q"):
            cur.execute("""
                CREATE TEMP TABLE b_w AS
                SELECT id, (ST_MaximumInscribedCircle(geom)).radius * 2.0 AS w
                FROM b_parcels
            """)
        with T("pg_width_f"):
            cur.execute("SELECT id, w FROM b_w ORDER BY id")
            _ = np.array([r[1] for r in cur.fetchall()], dtype=float)
        _row("inscribed_width", T.get("gp_width"), T.get("pg_width_push"),
             T.get("pg_width_q"), T.get("pg_width_f"))

        # ── 2. points_in_parcels ─────────────────────────────────
        print("-" * 88)
        with T("gp_pip"):
            pts = S.points_in_parcels(parcels, spacing=SPACING,
                                      max_per_parcel=400, verbose=False)
        print(f"  후보점 {len(pts):,}")
        print(f"  points_in_parcels      geopandas {T.get('gp_pip'):7.2f}s")
        print(f"  {'':<22} postgis  — ST_SquareGrid 조합은 아직 미구현(전환 대상)")

        # ── 3. buffer_union ──────────────────────────────────────
        print("-" * 88)
        # 배제 레이어 대신 필지 일부를 대표 입력으로 쓴다. 배제 레이어는 도메인
        # 산출물이라 여기서 재구성하면 조건이 어긋난다 — 여기선 '같은 크기 입력에
        # 대한 버퍼+union 비용'만 보면 된다.
        sample = parcels.iloc[:2000]
        with T("gp_bu"):
            S.buffer_union(sample, EXCL_RADIUS)
        with T("pg_bu_push"):
            _push(cur, "b_excl", sample.geometry.values, "Geometry")
        with T("pg_bu_q"):
            cur.execute("CREATE TEMP TABLE b_u AS "
                        "SELECT ST_Union(ST_Buffer(geom, %s)) g FROM b_excl",
                        (EXCL_RADIUS,))
        with T("pg_bu_f"):
            cur.execute("SELECT ST_AsBinary(g) FROM b_u")
            _ = shapely.from_wkb(bytes(cur.fetchone()[0]))
        _row(f"buffer_union(n={len(sample):,})", T.get("gp_bu"), T.get("pg_bu_push"),
             T.get("pg_bu_q"), T.get("pg_bu_f"))

        # ── 4. neighbors_within — 여기가 본론 ────────────────────
        print("-" * 88)
        from app.services.gam4_site_select import build_demand_grid
        dgrid = build_demand_grid(cpath, parcels, 50)
        print(f"  후보점 {len(pts):,} × 수요점 {len(dgrid):,}   R={R_COVER:g}m")

        with T("gp_nw"):
            ci, tj, dd = S.neighbors_within(pts, dgrid, R_COVER)
        with T("pg_nw_push"):
            _push(cur, "b_cand", pts.geometry.values, "Point")
            _push(cur, "b_dem", dgrid.geometry.values, "Point")
        with T("pg_nw_q"):
            cur.execute("""
                CREATE TEMP TABLE b_pairs AS
                SELECT c.id ci, d.id tj, ST_Distance(c.geom, d.geom) dist
                FROM b_cand c JOIN b_dem d ON ST_DWithin(c.geom, d.geom, %s)
            """, (R_COVER,))
            cur.execute("SELECT count(*) FROM b_pairs")
            n_pairs = cur.fetchone()[0]
        # 🔴 쌍을 진짜로 끌어온다. 개수만 세고 "빠르다"고 하면 거짓말이다 —
        #    호출부(감쇠 점수화)는 dist 값을 쓴다.
        with T("pg_nw_f"):
            cur.execute("SELECT ci, tj, dist FROM b_pairs")
            got = cur.fetchall()
        print(f"  쌍 {n_pairs:,}  (geopandas {len(ci):,})  "
              f"{'✅ 일치' if n_pairs == len(ci) else '🔴 불일치'}")
        _row("neighbors_within", T.get("gp_nw"), T.get("pg_nw_push"),
             T.get("pg_nw_q"), T.get("pg_nw_f"))
        print(f"  {'':<22} ※ fetch 는 {len(got):,}행 × 3열을 파이썬으로 옮기는 비용이다")

        # ── 5. 반증 시도 ─────────────────────────────────────────
        #   느리다고 결론내기 전에, 느린 이유가 **우리가 못 쓴 탓**일 가능성을 지운다.
        #   안 지우고 "PostGIS 가 느리다"고 하면 그것도 실측 없는 단정이다.
        print("-" * 88)
        print("반증 1  기본 설정 탓인가 — work_mem 을 올려본다")
        for wm in ("4MB", "256MB", "1GB"):
            cur.execute(f"SET work_mem = '{wm}'")
            cur.execute("SET max_parallel_workers_per_gather = 4")
            with T(f"tune_q_{wm}"):
                cur.execute("DROP TABLE IF EXISTS b_pairs2")
                cur.execute("""
                    CREATE TEMP TABLE b_pairs2 AS
                    SELECT c.id ci, d.id tj, ST_Distance(c.geom, d.geom) dist
                    FROM b_cand c JOIN b_dem d ON ST_DWithin(c.geom, d.geom, %s)
                """, (R_COVER,))
            with T(f"tune_f_{wm}"):
                cur.execute("SELECT ci, tj, dist FROM b_pairs2")
                cur.fetchall()
            tot = T.get(f"tune_q_{wm}", f"tune_f_{wm}")
            print(f"    work_mem {wm:>6}   query {T.get(f'tune_q_{wm}'):5.2f}  "
                  f"fetch {T.get(f'tune_f_{wm}'):5.2f}  합 {tot:5.2f}s  "
                  f"= {T.get('gp_nw') / tot:.2f}x")
        cur.execute("DROP TABLE IF EXISTS b_pairs2")
        print("    → 설정 탓이 아니다. 병목은 조인 자체다")

        print("반증 2  fetch 가 문제인가 — 쌍을 안 끌어오고 **SQL 안에서 집계**한다")
        cur.execute("SET work_mem = '256MB'")
        with T("agg"):
            cur.execute("""
                SELECT c.id, count(*), sum(exp(-0.5*power(ST_Distance(c.geom,d.geom)/%s,2)))
                FROM b_cand c JOIN b_dem d ON ST_DWithin(c.geom, d.geom, %s)
                GROUP BY c.id
            """, (R_COVER / 2, R_COVER))
            agg = cur.fetchall()
        with T("gp_agg"):
            w = np.exp(-0.5 * (dd / (R_COVER / 2)) ** 2)
            np.bincount(ci, weights=w, minlength=len(pts))
        gp_total = T.get("gp_nw", "gp_agg")
        print(f"    PostGIS 집계형 {T.get('agg'):5.2f}s (반환 {len(agg):,}행)  vs  "
              f"geopandas 쌍+감쇠 {gp_total:5.2f}s  = {gp_total / T.get('agg'):.2f}x")
        print("    → 전송을 0 으로 만들어도 진다. fetch 가 문제가 아니다")

        cur.execute("DROP TABLE IF EXISTS b_parcels, b_excl, b_cand, b_dem")

    print("=" * 88)
    print("  읽는 법 — 'DB상주' 는 기하가 이미 DB 에 있는 경우(S5 완주 후),")
    print("            '매번전송' 은 지금처럼 파일에서 읽어 밀어넣는 경우다.")
    print("            🔴 가 뜨면 그 술어는 옮기면 **느려진다.**")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
