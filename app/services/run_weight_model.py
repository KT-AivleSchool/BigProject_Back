# -*- coding: utf-8 -*-
"""
가중치 모델 실행 (흡연 도메인)
==============================
  python run_weight_model.py 흡연 --candidates 국유부동산_위경도_v2.csv

흐름: 로드 -> [A]지표정의 -> [A2]레이어부착 -> [R]반경제안(mini+HITL) ->
      [B]행렬 -> 희소판정 -> 정규화 -> [D]CRITIC+부트스트랩 -> [C]human -> [E]합성 -> [F]저장

거리 감쇠
  --decay gaussian     반경 안이면 1(기존) 대신 exp(-d²/2σ²) 로 거리 가중
  --sigma-ratio 0.333  σ = R * ratio (기본 1/3)

반경 고정
  --radius "07+02=150,06+03=300,08=50,09=50,10=250"
    mini 제안이 실행마다 흔들리므로(temperature=0 인데도), 비교 실험에서는
    R 을 고정해야 감쇠 효과만 분리된다. 지정한 지표는 HITL 을 건너뛴다.
"""
import os, sys, json, re, argparse, time
_T_START = time.perf_counter()
_T_IMPORT = time.perf_counter()
import numpy as np, pandas as pd, geopandas as gpd

# 프로젝트 루트를 sys.path 에 추가 → `app.xxx` 절대 임포트가 되게.
#   이 파일: BigProject_Back/app/services/run_weight_model.py
#   루트   : parent.parent.parent
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services import gam2_weight_model as W
IMPORT_SEC = time.perf_counter() - _T_IMPORT
from app.config import (STEP1_OUTPUT_DIR, STEP2_OUTPUT_DIR, STEP3_OUTPUT_DIR,
                        ADM_DONG_SHP, REGION_DATA_DIR, domain_prefix)


def _resolve_candidates(path: str, domain: str = "") -> str:
    """후보 파일 경로 해석.

    탐색 순서: 직접 경로 → STEP3_OUTPUT_DIR(프리픽스 붙인 이름) →
               STEP3_OUTPUT_DIR(원래 이름) → REGION_DATA_DIR(구버전 호환)

    후보 gpkg 는 make_parcel_candidates.py 산출물이며 **도메인 프리픽스**가 붙는다.
    후보 집합이 지목 판정(시설별)에 의존하므로 도메인마다 달라지기 때문이다.
    """
    base = os.path.basename(path)
    pfx = domain_prefix(domain) if domain else ""
    cands = [path]
    if pfx and not base.startswith(f"{pfx}_"):
        cands.append(os.path.join(STEP3_OUTPUT_DIR, f"{pfx}_{base}"))
    cands += [os.path.join(STEP3_OUTPUT_DIR, base),
              os.path.join(REGION_DATA_DIR, base)]   # 구버전 위치
    for p in cands:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "후보 파일 없음. 다음 경로를 찾았습니다:\n  "
        + "\n  ".join(os.path.abspath(p) for p in cands)
        + f"\n\n  먼저 생성하세요: python app\\services\\make_parcel_candidates.py {domain or '<도메인>'}")


def _parse_radius_arg(s: str) -> dict:
    """'07+02=150,08=50' -> {'07+02':150,'08':50}. 형식 오류는 즉시 중단."""
    out = {}
    if not s:
        return out
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"--radius 형식 오류: '{tok}' (지표ID=미터)")
        k, v = tok.split("=", 1)
        k = k.strip()
        try:
            r = int(v.strip())
        except ValueError:
            raise ValueError(f"--radius 반경은 정수여야 합니다: '{tok}'")
        if not (1 <= r <= 5000):
            raise ValueError(f"--radius 반경은 1~5000m 범위: '{tok}'")
        out[k] = r
    return out


def _parse_weight_arg(s: str) -> dict:
    """'07+02=0.75,09=-0.4' -> {'07+02':0.75,'09':-0.4}. 부호=방향(감점은 음수)."""
    out = {}
    if not s:
        return out
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"--weight 형식 오류: '{tok}' (지표ID=값)")
        k, v = tok.rsplit("=", 1)
        try:
            w = float(v.strip())
        except ValueError:
            raise ValueError(f"--weight 값은 숫자여야 합니다: '{tok}'")
        if not (-1.0 <= w <= 1.0):
            raise ValueError(f"--weight 범위는 -1~+1 입니다: '{tok}'")
        out[k.strip()] = w
    return out


def make_loader(domain: str):
    """dataset_id -> GeoDataFrame(5186)|DataFrame. clean_report 로 파일 경로 해석."""
    prefix = domain_prefix(domain)
    rpt = os.path.join(STEP2_OUTPUT_DIR, f"{prefix}_clean_report.json")
    doc = json.load(open(rpt, encoding="utf-8"))
    files = {}
    for r in doc.get("results", []):
        out = r.get("output")
        if out and os.path.isfile(out):
            files[r["dataset_id"]] = out
        elif out:
            alt = os.path.join(STEP2_OUTPUT_DIR, os.path.basename(out))
            if os.path.isfile(alt):
                files[r["dataset_id"]] = alt

    def loader(did):
        f = files[did]
        if f.endswith(".gpkg"):
            return gpd.read_file(f).to_crs(W.WORK_CRS)
        # parquet 에 좌표 컬럼이 남아 있으면 geometry 로 복원(좌표계는 값으로 판정).
        #   지오코딩이 정제 저장 뒤에 붙는 등으로 gpkg 분기를 놓친 경우 대비.
        return W.as_geodataframe(pd.read_parquet(f), did)
    # rpt 경로도 돌려준다 — weight_set 의 입력 지문에 쓴다.
    return loader, doc, rpt


def _describe_candidates(cand, path, layer, loaded_as) -> dict:
    """후보 집합을 **사실만으로** 기술한다. 의미 부여(라벨)는 하지 않는다.

    과거 build_weight_set 에 "국유부동산 필지" 가 박혀 있었는데, 후보가
    지적도 42,216필지로 바뀐 뒤에도 그대로 찍혀 존재하지 않는 숫자를 주장했다.
    여기서는 파일·레이어·geometry 타입처럼 확인 가능한 것만 남긴다.
    """
    gts = sorted(set(cand.geometry.geom_type))
    return {"file": os.path.basename(path),
            "layer": layer,
            "geom_type": gts[0] if len(gts) == 1 else gts,
            "crs": int(cand.crs.to_epsg()) if cand.crs is not None else None,
            "loaded_as": loaded_as,
            "n": int(len(cand))}

def _print_weight_table(inds, slider, conflicts=None) -> None:
    """[W] 화면. 비중 %는 **abs 기준**이라 감점 지표도 양수 %로 나온다.

    ⚠ 희소 경고는 여기서 낼 수 없다 — detect_sparse 는 [B] 지표 행렬이 있어야
      계산되는데 [W] 는 그 앞이다. 대신 레코드 수를 근거로 보여준다.
    """
    pct = W.slider_pct(slider)
    conf_ids = {i["id"] for i in (conflicts or [])}
    print("-" * 70)
    print(f"{'지표':<10}{'현재':>8}{'방향':>6}{'비중':>9}   데이터")
    for i in inds:
        v = slider[i["id"]]
        arrow = "감점" if v < 0 else ("제외" if v == 0 else "가점")
        mark = " ⚠" if i["id"] in conf_ids else ""
        print(f"{i['id']:<10}{v:>+8.2f}{arrow:>6}{pct[i['id']]:>8.1f}%   "
              f"{W.data_note(i)}{mark}")
    print("-" * 70)
    print(f"{'':<10}{'':>8}{'':>6}{sum(pct.values()):>8.1f}%   (절대값 기준 합계)")
    for i in (conflicts or []):
        c = i["direction_conflict"]
        gd = "가점" if c["geo_direction"] == "benefit" else "감점"
        vd = "가점" if c["val_direction"] == "benefit" else "감점"
        print(f"  ⚠ [{i['id']}] 감리 판정 충돌 — "
              f"{c['geo_dataset']}({gd}) · {c['val_dataset']}({vd})")
        print(f"     병합값은 {c['val_dataset']} 쪽({vd})을 기본으로 뒀습니다. "
              f"부호로 확정하세요.")


# ── 값의 출처 라벨 ────────────────────────────────────────────────────
#   STEP1 감리가 이미 쓰는 어휘(`human_confirmed`, gam2_audit_judgment_test.py:697)에
#   맞춘다. 같은 뜻을 단계마다 다른 이름으로 남기면 대조할 때 걸린다.
SRC_RADIUS = {"human": "human_confirmed", "fixture": "fixture", "cli": "cli_fixed"}
SRC_WEIGHT = {"human": "human_confirmed", "fixture": "fixture", "cli": "cli"}
#   사람이 정한 값으로 볼 출처.
#     human_confirmed = HITL 게이트에서 사람이 확정 · hitl = 대화형 루프에서 숫자 수정
#     cli_fixed·cli   = 사람이 명령줄에 직접 지정
#   `fixture`(픽스처 재생)·`llm`(모델 제안)·`none`(반경 없는 admin 지표)은 사람이 아니다.
HUMAN_SRC = {"human_confirmed", "hitl", "cli_fixed", "cli"}


def build_hitl_record(radius_conf: dict, weight_sources: dict, value_source: str,
                      radius_asked: bool, weight_asked: bool) -> dict:
    """`weight_set.json` 의 `hitl` 블록. **실행 방식이 아니라 값의 출처로 판정한다.**

    🔴 예전엔 `not args.auto_radius` / `not args.auto_weight` 였다. `--auto-*` 는
       "대화형 `input()` 루프를 건너뛴다"(실행 방식)는 뜻이지 "사람이 확정하지
       않았다"(사실 기록)는 뜻이 아니다. CLI 직접 실행에서만 두 뜻이 겹친다.

       API 게이트 방식에서는 앞만 참이다 — 사람은 게이트B 에서 답했고 그 답이
       `--weight` 로 들어온다. 그래서 mode 가 fixture 든 hitl 이든 네 run 이 전부
       `{radius: True, weight: False}` 로 똑같이 찍혔다. 사람이 개입한 run 과
       안 한 run 이 산출물에서 구분되지 않았다(2026-08-05 프런트 제보,
       `runs/r_20260805_010~014` 실측). 절대원칙 4 위반이다.

    `*_asked` 는 사람이 프롬프트를 본 경우다. **엔터로 제안값을 승인한 것도 확정**인데
    그때는 출처가 `llm` 그대로 남아서, 출처만 봐서는 안 잡힌다.
    """
    r_src = {v.get("source") for k, v in radius_conf.items()
             if not k.startswith("_") and isinstance(v, dict)}
    w_src = set(weight_sources.values())
    return {
        "radius_confirmed": bool(radius_asked or (r_src & HUMAN_SRC)),
        "weight_confirmed": bool(weight_asked or (w_src & HUMAN_SRC)),
        # 판정 근거를 같이 남긴다 — 불리언만 있으면 왜 그 값인지 되짚을 수 없다.
        "value_source": value_source,
        "radius_sources": sorted(s for s in r_src if s),
        "weight_sources": sorted(w_src),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--candidates", required=True, help="후보지 CSV(경도·위도 포함)")
    ap.add_argument("--reviewed", help="reviewed.json (기본: STEP2 옆)")
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--auto-radius", action="store_true",
                    help="mini 제안값 자동 사용(HITL 생략, 테스트용)")
    ap.add_argument("--bootstrap", type=int, default=200,
                    help="CRITIC 95%% CI 부트스트랩 반복수 (기본 200, 0이면 생략). "
                         "가중치 계산과 무관한 진단이다")
    ap.add_argument("--no-diag", action="store_true",
                    help="표본 대표성·alpha 민감도 진단 생략")
    # --- 거리 감쇠 ---
    ap.add_argument("--decay", choices=["gaussian", "linear"], default=None,
                    help="거리 감쇠. 미지정이면 기존 binary(반경 안=1)")
    ap.add_argument("--sigma-ratio", type=float, default=1/3,
                    help="가우시안 σ = R * ratio (기본 1/3)")
    ap.add_argument("--scale", choices=["minmax", "log"], default="minmax",
                    help="정규화. log 는 롱테일(서울역 등) 지배를 완화")
    # --- 반경 고정 ---
    ap.add_argument("--radius", default=None,
                    help='R 고정. 예: "07+02=150,06+03=300,08=50,09=50,10=250"')
    # --- [W] 가중치 HITL ---
    ap.add_argument("--auto-weight", action="store_true",
                    help="LLM seed_weight 자동 사용([W] HITL 생략, 테스트용). "
                         "방향 판정 충돌이 있으면 중단한다")
    ap.add_argument("--weight", default=None,
                    help='가중치 고정. 부호=방향. 예: "07+02=0.75,09=-0.4"')
    # --- 게이트B(HITL) 제안 단계 ---
    #   [A]지표정의 → [A2]레이어부착 → [R]반경제안 → 슬라이더 초기값 까지만 하고
    #   제안 산출물을 남긴 뒤 끝낸다. 후보 로드·[B]행렬·CRITIC·[F]저장은 **하지 않는다.**
    #   왜 필요한가 — 제안값은 이 프로세스 안에서만 만들어진다. 사람에게 보여주려면
    #   한 번은 여기까지 돌려야 하고, 끝까지 돌리면 확정 전 weight_set 이 생겨
    #   산출물이 "확정됐다"고 거짓말한다(원칙 4).
    ap.add_argument("--propose-only", action="store_true",
                    help="[R]·[W] 제안값만 만들고 종료(HITL 게이트B 화면용)")
    ap.add_argument("--run-id", default=None,
                    help="제안 산출물 파일명에 붙일 실행 id (API 러너가 준다)")
    ap.add_argument("--candidate-unit", default=None,
                    help="후보 1건이 무엇인지(설명책임용). 예: \"지적도 필지\". "
                         "미지정 시 후보 파일에서 사실만 자동 기술한다")
    # --- 값의 출처 (산출물 설명책임) ---
    #   🔴 `--radius`/`--weight` 로 들어온 값을 **누가 정했는지는 이 프로세스가 알 수 없다.**
    #   같은 `--radius 07+02=150` 이 (a) API 게이트B 에서 사람이 답한 값일 수도,
    #   (b) 회귀 픽스처를 재생한 값일 수도, (c) 사람이 명령줄에 직접 친 값일 수도 있다.
    #   추측하면 산출물이 거짓말한다(원칙 4·5) — 그래서 호출자가 알려준다.
    ap.add_argument("--value-source", choices=["human", "fixture", "cli"], default="cli",
                    help="--radius/--weight 값의 출처. "
                         "human=사람이 HITL 게이트에서 확정 · fixture=픽스처 재생(사람 개입 0) · "
                         "cli=명령줄에서 직접 지정(기본)")
    args = ap.parse_args()

    # 사람이 실제로 프롬프트를 보고 승인했는가(엔터=승인도 확정이다).
    # 값의 출처만으로는 "제안값을 그대로 승인" 을 잡을 수 없어 따로 센다.
    radius_asked = weight_asked = False

    radius_fix = _parse_radius_arg(args.radius)
    weight_fix = _parse_weight_arg(args.weight)

    T = W.Timer()
    loader, report, report_path = make_loader(args.domain)
    reviewed_path = args.reviewed or os.path.join(
        STEP1_OUTPUT_DIR, f"{domain_prefix(args.domain)}_audit_result_reviewed.json")
    if not os.path.isfile(reviewed_path):
        raise FileNotFoundError(
            f"감리 결과(reviewed) 없음: {reviewed_path}\n"
            f"  --reviewed <경로> 로 직접 지정하거나, STEP1_OUTPUT_DIR 를 확인하세요.")
    reviewed = json.load(open(reviewed_path, encoding="utf-8"))
    facility = reviewed.get("facility_inference", {}).get("facility", args.domain)
    region = report.get("region", "")
    T.lap("감리·정제 결과 로드")

    # [A] 지표 정의
    print("="*70, "\n[A] 지표 정의")
    inds = W.define_indicators(reviewed, report)
    for i in inds:
        print(f"  {i['id']:<8} seed={i['seed_weight']} dir={i['direction']} "
              f"geo={i['geo_dataset']} val={i['val_dataset']}")

    T.lap("[A] 지표 정의")

    # [A2] 레이어 부착 (kind 확정)
    print("\n[A2] 레이어 부착")
    W.attach_layers(inds, loader, region=region)
    T.lap("[A2] 레이어 부착")

    # [R] 반경 제안 (mini) -> HITL
    #   🔴 --radius 가 비-admin 지표를 **전부** 덮는 실행에서는 제안을 부르지 않는다.
    #      부르면 LLM 값을 만들자마자 아래 fix 루프가 전부 덮어쓴다 — 순수 낭비이고,
    #      쓰지도 않은 제안의 rationale 이 산출물에 남아 "이 근거로 정했다"고
    #      주장하게 된다(원칙 4). admin 지표는 반경 개념 자체가 없어 대상이 아니다.
    non_admin = {i["id"] for i in inds if i["kind"] != "admin"}
    if non_admin and non_admin <= set(radius_fix):
        print("\n[R] 집계반경 — --radius 가 전 지표를 덮으므로 LLM 제안을 건너뜁니다.")
        radius_conf = {
            i["id"]: ({"radius_m": None, "rationale": "행정동 단위 지표(반경 무관)",
                       "source": "none"} if i["kind"] == "admin"
                      else {"radius_m": None, "rationale": "", "source": "none"})
            for i in inds}
        radius_conf["_confirmed"] = False
    else:
        print("\n[R] 집계반경 제안 (mini)")
        radius_conf = W.suggest_radius(facility, inds)
        for _rc in radius_conf.values():      # 기본 출처 — 이후 CLI/HITL 이 덮어쓴다
            if isinstance(_rc, dict):
                _rc.setdefault("source", "llm")
    for i in inds:
        rc = radius_conf.get(i["id"], {})
        print(f"  {i['id']:<8} R={rc.get('radius_m')}  {rc.get('rationale','')}")

    # --radius 로 지정된 지표는 제안값을 덮어쓰고 HITL 에서 제외
    if radius_fix:
        unknown = set(radius_fix) - {i["id"] for i in inds}
        if unknown:
            raise ValueError(f"--radius 에 없는 지표ID: {sorted(unknown)}\n"
                             f"  사용 가능: {[i['id'] for i in inds]}")
        print(f"\n  [고정] --radius 로 지정된 반경 (HITL 생략) · 출처={args.value_source}")
        for k, v in radius_fix.items():
            old = radius_conf.get(k, {}).get("radius_m")
            radius_conf.setdefault(k, {})["radius_m"] = v
            # 대화형 루프를 건너뛴다는 사실과, 그 값을 누가 정했는지는 별개다.
            radius_conf[k]["source"] = SRC_RADIUS[args.value_source]
            print(f"     [{k}] {old} -> {v}m")

    # ── 게이트B 제안만 만들고 종료 ────────────────────────────────────
    #   여기서 끊는 이유: 슬라이더 초기값은 seed_weight·direction 만 쓰고(:1068),
    #   그 둘은 [A] define_indicators 가 reviewed.json 에서 뽑는다. 반경·행렬·CRITIC 과
    #   접점이 없다 — 즉 **반경 확정 전에도 슬라이더를 보여줄 수 있다.**
    #   그래서 [R]·[W] 를 한 화면(게이트 하나)에 올린다.
    if args.propose_only:
        slider = W.slider_from_indicators(inds)
        prop = W.build_weight_proposal(args.domain, facility, region,
                                       inds, radius_conf, slider, run_id=args.run_id)
        path = W.save_weight_proposal(prop, args.domain, args.run_id)
        print(f"\n[P] 제안 저장: {path}  (지표 {len(inds)} · 충돌 {len(prop['conflicts'])})")
        T.report(import_sec=IMPORT_SEC, start=_T_START)
        return

    if not args.auto_radius:
        todo = [i for i in inds if i["kind"] != "admin" and i["id"] not in radius_fix]
        if todo:
            radius_asked = True        # 사람이 프롬프트를 본다 = 확정 절차를 거친다
            print("\n  >> HITL: 위 반경을 확인/수정하세요. 엔터=승인, 숫자입력=수정")
            for i in todo:
                cur = radius_conf[i["id"]]["radius_m"]
                while True:                   # 잘못된 입력에 파이프라인이 죽지 않게 재입력
                    v = input(f"     [{i['id']}] R({cur}m)= ").strip()
                    if not v:                 # 엔터 = 제안값 승인
                        break
                    try:
                        r = int(v)
                    except ValueError:
                        print(f"        숫자만 입력하세요 (엔터=승인). 입력값: {v[:40]}")
                        continue
                    if not (1 <= r <= 5000):
                        print("        1~5000m 범위로 입력하세요.")
                        continue
                    radius_conf[i["id"]]["radius_m"] = r
                    radius_conf[i["id"]]["source"] = "hitl"
                    break
        radius_conf["_confirmed"] = True
    radius_m = {k: v.get("radius_m") for k, v in radius_conf.items() if not k.startswith("_")}
    T.lap("[R] 반경 제안(LLM)+HITL")

    # ════════════════════════════════════════════════════════════════
    # [W] 가중치 HITL — 지표 N개, 슬라이더 -1 ~ +1
    #   부호=방향(가점/감점), 크기=중요도. 확정 시 코드가 (abs, 부호) 로 분해한다.
    #   설계: STEP3_가중치_설계 11·12절
    # ════════════════════════════════════════════════════════════════
    print("\n[W] 가중치 확인  (슬라이더 -1 ~ +1 · 부호=방향 · 크기=중요도)")
    slider = W.slider_from_indicators(inds)
    sources = {i["id"]: "llm" for i in inds}

    if weight_fix:
        unknown = set(weight_fix) - {i["id"] for i in inds}
        if unknown:
            raise ValueError(f"--weight 에 없는 지표ID: {sorted(unknown)}\n"
                             f"  사용 가능: {[i['id'] for i in inds]}")
        print(f"\n  [고정] --weight 로 지정 (HITL 생략) · 출처={args.value_source}")
        for k, v in weight_fix.items():
            print(f"     [{k}] {slider[k]:+.2f} -> {v:+.2f}")
            slider[k] = v
            # 대화형 루프를 건너뛴다는 사실과, 그 값을 누가 정했는지는 별개다.
            sources[k] = SRC_WEIGHT[args.value_source]

    conflicts = [i for i in inds if i.get("direction_conflict")]
    _print_weight_table(inds, slider, conflicts)

    if args.auto_weight:
        # 충돌을 자동으로 넘기면 감지한 의미가 없다 — 사람이 확정하거나 --weight 로 못박아야 한다.
        unresolved = [i for i in conflicts if i["id"] not in weight_fix]
        if unresolved:
            raise ValueError(
                "방향 판정 충돌이 있어 --auto-weight 로 진행할 수 없습니다: "
                f"{[i['id'] for i in unresolved]}\n"
                "  [W] HITL 로 확정하거나 --weight 로 부호를 지정하세요.")
    else:
        weight_asked = True            # 사람이 프롬프트를 본다 = 확정 절차를 거친다
        print("\n  >> HITL: 엔터=승인, 숫자입력=수정 (-1 ~ +1). 음수로 넣으면 감점으로 바뀝니다.")
        while True:
            for i in inds:
                cur = slider[i["id"]]
                while True:
                    v = input(f"     [{i['id']}] w({cur:+.2f})= ").strip()
                    if not v:
                        break
                    try:
                        w = float(v)
                    except ValueError:
                        print(f"        숫자만 입력하세요 (엔터=승인). 입력값: {v[:40]}")
                        continue
                    if not (-1.0 <= w <= 1.0):
                        print("        -1 ~ +1 범위로 입력하세요.")
                        continue
                    slider[i["id"]] = w
                    sources[i["id"]] = "hitl"
                    break
            _print_weight_table(inds, slider, conflicts)
            if input("\n  확정하시겠습니까? (엔터=확정, r=다시 입력): ").strip().lower() != "r":
                break

    W.apply_weight_hitl(inds, slider, sources=sources)
    T.lap("[W] 가중치 HITL")

    # 후보 로드 — .gpkg/.geojson 은 geometry 그대로, .csv 는 경위도에서 생성
    cand_path = _resolve_candidates(args.candidates, args.domain)
    _lyr = None                      # gpkg 가 아니면 레이어 개념이 없다
    if cand_path.lower().endswith((".gpkg", ".geojson", ".shp")):
        # gpkg 는 candidates(Point)/parcels(Polygon) 2개 레이어다.
        # STEP3 는 필지당 1점이어야 하므로 candidates 를 명시한다.
        _lyr = "candidates" if cand_path.lower().endswith(".gpkg") else None
        cand = (gpd.read_file(cand_path, layer=_lyr) if _lyr
                else gpd.read_file(cand_path)).to_crs(W.WORK_CRS)
        c = pd.DataFrame(cand.drop(columns="geometry"))
        src_kind = "geometry"
    else:
        c = pd.read_csv(cand_path, encoding="utf-8")
        lon = next(col for col in c.columns if "경도" in col or col.lower() == "lon")
        lat = next(col for col in c.columns if "위도" in col or col.lower() == "lat")
        cand = gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c[lon], c[lat]),
                                crs=4326).to_crs(W.WORK_CRS)
        src_kind = "경위도(4326->%d)" % W.WORK_CRS

    # 후보 1건이 무엇인지는 코드가 알 수 없다 — 주입(--candidate-unit)하거나 사실만 기술한다.
    cand_src = _describe_candidates(cand, cand_path, _lyr, src_kind)
    cand_unit = args.candidate_unit or (
        f"{cand_src['layer']} 레이어 1행 ({cand_src['geom_type']})" if cand_src["layer"]
        else f"{cand_src['file']} 1행 ({cand_src['geom_type']})")
    print(f"\n[후보] {len(cand):,}개 (EPSG:{W.WORK_CRS}, {src_kind})"
          f"  {os.path.basename(cand_path)}")
    T.lap("후보 로드")

    # 행정동 경계 (admin 지표용)
    admin_gdf = None
    if any(i["kind"] == "admin" for i in inds):
        if not ADM_DONG_SHP or not os.path.exists(ADM_DONG_SHP):
            print("  ⚠ ADM_DONG_SHP 없음 — admin 지표 계산 불가. config 확인 필요.")
        else:
            admin_gdf = gpd.read_file(ADM_DONG_SHP).to_crs(W.WORK_CRS)

    # [B] 행렬
    if args.decay:
        print(f"\n[B] 지표 행렬  (감쇠={args.decay}, σ=R×{args.sigma_ratio:.3f})")
    else:
        print("\n[B] 지표 행렬  (감쇠 없음 — 반경 안=1)")
    mat = W.build_matrix(cand, inds, radius_m, admin_gdf=admin_gdf,
                         decay=args.decay, sigma_ratio=args.sigma_ratio)

    T.lap("[B] 지표 행렬")

    # 희소 판정 (방향 인지 — cost 지표는 양끝 모두 희소)
    sparse = W.detect_sparse(mat, inds)
    print("\n[희소성] 비영 비율")
    for c_ in mat.columns:
        ratio = (mat[c_] > 0).mean()
        print(f"  {c_:<8} {ratio*100:5.1f}%" + ("  <- 희소(CRITIC 제외)" if c_ in sparse else ""))

    # 정규화 -> CRITIC -> human -> 합성
    norm = W.normalize_matrix(mat, inds, scale=args.scale)
    T.lap("정규화·희소판정")
    w_h = W.human_weights(inds)
    w_c = W.critic_weights(norm, sparse_ids=sparse)
    T.lap("[D] CRITIC")
    boot = ({} if args.bootstrap <= 0
            else W.critic_bootstrap(norm, sparse_ids=sparse, B=args.bootstrap))
    T.lap(f"[D] 부트스트랩 B={args.bootstrap}")
    w_f = W.synthesize(w_h, w_c, alpha=args.alpha, sparse_ids=sparse)

    # [진단] 표본 대표성 · alpha 민감도  (--no-diag 로 생략 가능)
    if not args.no_diag:
        # 후보의 계층(법정동) — 주소에서 추출. 없으면 층화 검사는 생략된다.
        strata, strata_src = None, None
        if "법정동코드" in c.columns:              # 지적도 후보(gpkg)
            strata, strata_src = c["법정동코드"].astype(str), "법정동코드"
        else:                                       # 국유부동산 CSV — 주소에서 추출
            addr_col = next((col for col in c.columns if "소재지" in str(col)), None)
            if addr_col:
                strata = c[addr_col].astype(str).str.extract(r"구\s+(\S+?)\s")[0]
                if strata.isna().mean() > 0.5:
                    strata = None
                else:
                    strata_src = f"{addr_col}(정규식 추출)"
        bias = W.diagnose_sample_bias(mat, inds, sparse, strata=strata)
        da = W.diagnose_alpha(w_h, w_c, sparse)
        # table 은 DataFrame — 콘솔용이라 산출물에서 뺀다(gam4 의 geom 처리와 같은 이유).
        # strata 는 **실제로 쓴 컬럼명**을 남긴다. 층화 검사가 생략됐으면 null 이다 —
        # 라벨을 고정해두면 CSV 후보에서 하지도 않은 검사를 했다고 주장하게 된다.
        diagnostics = {"sample_bias": bias,
                       "alpha_sensitivity": {k: v for k, v in da.items()
                                             if k != "table"},
                       "strata": strata_src}
        T.lap("[진단] 표본·alpha")
    else:
        # 안 한 것은 "안 했다"고 남긴다 — 없으면 산출물만 보고 검증한 줄 안다(절대원칙 4).
        diagnostics = {"skipped": True, "reason": "--no-diag"}

    print("\n" + "="*70)
    print(f"[가중치] alpha={args.alpha}  (사람 {1-args.alpha:.0%} / 데이터 {args.alpha:.0%})"
          + (f"  감쇠={args.decay}" if args.decay else "  감쇠=없음")
          + f"  정규화={args.scale}")
    print("-"*70)
    print(f"{'지표':<10}{'w_human':>9}{'w_critic':>9}{'w_final':>9}   95% CI")
    for i in inds:
        iid = i["id"]; ci = boot.get(iid)
        # boot 가 비면(--bootstrap 0) 전 지표가 "(희소)" 로 찍혀 거짓 경고가 된다.
        # 희소 판정(detect_sparse)과 CI 미산출을 구분해서 표시한다.
        cis = (f"[{ci['ci_low']:.3f},{ci['ci_high']:.3f}]" if ci
               else "(희소)" if iid in sparse else "(생략)")
        wc = f"{w_c.get(iid,0):.3f}" if iid not in sparse else "  -  "
        print(f"{iid:<10}{w_h[iid]:>9.3f}{wc:>9}{w_f[iid]:>9.3f}   {cis}")
    print("-"*70)

    # [F] 저장
    hitl_rec = build_hitl_record(radius_conf, sources, args.value_source,
                                 radius_asked, weight_asked)
    ws = W.build_weight_set(args.domain, facility, region, inds, radius_conf,
                            args.alpha, w_h, w_c, w_f, boot, sparse, len(cand),
                            candidate_unit=cand_unit, candidate_source=cand_src,
                            inputs={"reviewed": W.fingerprint(reviewed_path),
                                    "clean_report": W.fingerprint(report_path),
                                    "candidates": W.fingerprint(cand_path)},
                            hitl=hitl_rec)
    # 재현성 메타 — 감쇠 설정을 산출물에 남긴다(같은 결과를 다시 못 만드는 일 방지)
    ws["decay"] = {"func": args.decay, "sigma_ratio": args.sigma_ratio if args.decay else None}
    ws["scale"] = args.scale        # 재현성 — 어떤 정규화로 뽑은 가중치인지
    # S4 — 진단을 산출물에 남긴다. "가중치가 후보 집합에 의존하지 않는다"는 주장의 증빙이
    #      지금까지는 콘솔 출력을 손으로 옮겨적은 노트뿐이었다.
    ws["diagnostics"] = diagnostics
    path = W.save_weight_set(ws, args.domain)
    print(f"\n[F] weight_set 저장: {path}")
    T.lap("[F] 저장")
    T.report(import_sec=IMPORT_SEC, start=_T_START)


if __name__ == "__main__":
    main()
