# -*- coding: utf-8 -*-
"""
파이프라인 실행시간 실측 (기본은 읽기 전용 · LLM 호출 0회)
============================================================
  python app\\tools\\measure_runtime.py --domain 재활용
  python app\\tools\\measure_runtime.py --domain 흡연 --run           # 새 실행 (y/n 확인)
  python app\\tools\\measure_runtime.py --domain 흡연 --run --cold    # LLM 캐시 비우고 (y/n 확인)

무엇을 재는가
  발표에서 "실무를 얼마나 줄이는가"를 말하려면 **우리 파이프라인 자체**를 재야 한다.
  외부 대조군(용역 기간 6개월 등)은 다른 도메인의 규정에서 온 숫자라 인용하면 반박당한다.

🔴 계측 층이 둘이고, 각각이 놓치는 것이 다르다
  ⓐ `status.json` 의 `steps[].sec` — 러너가 부모 쪽 `perf_counter` 로 잰 **자식 프로세스
     구간**. 10ms 정밀도. 단계 경계는 자식 stdout 의 **마커 문자열**로 잡는다.
     🔴 `_RUNPIPE_MARKERS` 에 STEP 0.5 마커가 **없어서** 단계 "0" 이 STEP 0.5 를 흡수한다.
     그래서 「감리 판정이 STEP0~1 의 88.3%」 같은 문장은 여기서 **안 나온다**.
  ⓑ `run.log` 의 `[소요 시간]` 표 — 자식이 스스로 찍은 세부 내역. ⓐ 가 못 가르는
     STEP 0 / 0.5 / 1 / 2 가 여기 있다. **텍스트 표라 깨지기 쉽다** → 파싱 실패는
     조용히 넘기지 않고 `ParseError` 로 터뜨린다(원칙 1). 이 숫자가 없으면 핵심 주장이 사라진다.

🔴 헤드라인 두 개를 절대 섞지 않는다
  · **무인 구간 합계 (Σsec)** — 발표에 쓰는 숫자. 사람이 개입하지 않는 계산 시간이다.
  · **게이트 포함 벽시계** — `finished_at - started_at`. 여기엔 **사람이 화면을 보는
    시간**이 들어 있고 그건 애초에 측정 대상이 아니다.
  둘의 차이는 ① 게이트 대기 ② 프로세스 기동·전환 ③ `--propose-only` 제안 패스
  (그 자식은 `step_ids` 가 비어 있어 `steps` 에 **안 들어간다**) 셋이 합쳐진 것이다.

🔴 적재 단계는 무인 합계에서 뺀다
  `적재-감리`·`적재-후보` 는 판단 파이프라인이 아니라 **DB 인프라**다. 따로 표기한다.

측정 못 하는 것 (지어내지 않는다 — `null` + 사유 문자열로 남긴다)
  · 지오코딩 **소요 시간** — 건수만 로그에 있고 시간은 안 찍힌다
  · LLM 호출 **횟수·토큰** — 공통 카운터가 없다
  · `--propose-only` 패스의 step 귀속 — `step_ids` 가 비어 있다
  · 게이트 **대기 시간** — 답변 시각(`hitl/*_answer.json`)은 있으나 질문 제시 시각이 없다
  · 사람의 HITL **검토 시간** — 측정 대상이 아니다(범위 밖)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # 콘솔이 cp949 면 ✅·🔴 에서 터진다. 값이 아니라 출력에서 터지는 것이라 회귀로 오인된다.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

try:
    from app.config import (
        DATA_ROOT,
        SEARCH_CACHE_DIR,
        JIMOK_CACHE_PATH,
        FACILITY_PARAM_CACHE_PATH,
        GEOCODE_CACHE_DIR,
        domain_prefix,
    )
except Exception:  # 단독 실행 폴백 (app.config 는 DATABASE_URL 이 없으면 raise 한다)
    DATA_ROOT = os.path.join(_ROOT, "datasets")
    SEARCH_CACHE_DIR = os.path.join(str(DATA_ROOT), "search_cache")
    JIMOK_CACHE_PATH = os.path.join(SEARCH_CACHE_DIR, "jimok_role_cache.json")
    FACILITY_PARAM_CACHE_PATH = os.path.join(SEARCH_CACHE_DIR, "facility_params_cache.json")
    GEOCODE_CACHE_DIR = os.path.join(SEARCH_CACHE_DIR, "geocode")

    def domain_prefix(d):  # type: ignore[misc]
        return d


RUNS_ROOT = os.path.join(_ROOT, "runs")

# 적재는 판단 파이프라인이 아니다 — 무인 합계에서 뺀다.
LOAD_STEP_IDS = ("적재-감리", "적재-후보")


class ParseError(RuntimeError):
    """run.log 텍스트 표를 못 읽었다. 조용히 넘기면 핵심 숫자가 사라진다."""


# ─────────────────────────────────────────────────────────────
# run.log 파싱
# ─────────────────────────────────────────────────────────────
_DASH_RE = re.compile(r"^-{5,}\s*$")
_TIME_HEADER = "[소요 시간]"
# `  STEP 1  감리 판정 (gpt-4o)   56.46s   88.3%  ███`
#  라벨 안에 공백이 있으므로 **초 토큰**을 기준으로 자른다.
_ROW_RE = re.compile(
    r"^\s{2,}(?P<label>\S.*?)\s+(?P<sec>\d+(?:\.\d+)?)s"
    r"(?:\s+(?P<pct>\d+(?:\.\d+)?)%)?\s*█*\s*$"
)
_TOTAL_RE = re.compile(r"^\s{2,}(?:처리\s+)?합계\s+(\d+(?:\.\d+)?)s\s*$")
_WALL_RE = re.compile(r"^\s{2,}체감\(기동~종료\)\s+(\d+(?:\.\d+)?)s\s*$")

_GEO_RE = re.compile(
    r"\[지오코딩\]\s*([\d,]+)행\s*·\s*고유\s*([\d,]+)건"
    r"\s*·\s*캐시 적중\s*([\d,]+)건\s*·\s*신규 호출\s*([\d,]+)건"
)
_CLEAN_RE = re.compile(
    r"^\s*\[(\d+)\]\s*(.+?):\s*([\d,]+)→([\d,]+)행,\s*flag\s*(\d+)개"
    r".*?(?:\[(\d+(?:\.\d+)?)s\])?\s*$"
)
_PARCEL_RE = re.compile(r"^\[A\]\s*후보 필지\s+([\d,]+)")
_LOADED_RE = re.compile(r"^\[LOADED\]\s*table=(\S+)\s+run_id=(\S+)\s+rows=(\d+)")
_CASCADED_RE = re.compile(r"^\[CASCADED\]\s*table=(\S+)\s+run_id=(\S+)\s+(.*)$")
_CACHE_LINE_RE = re.compile(r"\[(지목 판정|시설 파라미터)\]")


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _parse_timing_block(lines: list[str], i: int, where: str) -> tuple[dict, int]:
    """`[소요 시간]` 한 덩어리를 읽는다. 모양이 다르면 **터진다**.

    반환 = ({"rows": {라벨: {"sec","pct"}}, "합계": float, "체감": float|None}, 다음 줄 index)
    """
    j = i + 1
    if j >= len(lines) or not _DASH_RE.match(lines[j]):
        raise ParseError(f"{where}: '[소요 시간]' 다음 줄이 구분선이 아니다 → {lines[j:j+1]!r}")
    j += 1

    rows: dict[str, dict] = {}
    while j < len(lines) and not _DASH_RE.match(lines[j]):
        m = _ROW_RE.match(lines[j].rstrip("\n"))
        if not m:
            raise ParseError(f"{where}: 세부 항목 줄을 못 읽었다 (line {j+1}) → {lines[j]!r}")
        label = m.group("label").strip()
        rows[label] = {
            "sec": float(m.group("sec")),
            "pct": float(m.group("pct")) if m.group("pct") else None,
        }
        j += 1
    if not rows:
        raise ParseError(f"{where}: '[소요 시간]' 표에 항목이 하나도 없다 (line {i+1})")
    if j >= len(lines):
        raise ParseError(f"{where}: '[소요 시간]' 표가 닫히지 않았다 (line {i+1})")
    j += 1  # 닫는 구분선

    if j >= len(lines) or not _TOTAL_RE.match(lines[j].rstrip("\n")):
        raise ParseError(f"{where}: 합계 줄이 없다 (line {j+1}) → {lines[j:j+1]!r}")
    total = float(_TOTAL_RE.match(lines[j].rstrip("\n")).group(1))  # type: ignore[union-attr]
    j += 1

    wall = None
    if j < len(lines):
        mw = _WALL_RE.match(lines[j].rstrip("\n"))
        if mw:
            wall = float(mw.group(1))
            j += 1

    return {"rows": rows, "합계": total, "체감": wall}, j


def parse_run_log(path: str, *, require_runpipe: bool) -> dict:
    """run.log 를 자식 프로세스 단위로 쪼개 `[소요 시간]` 표와 약속된 줄을 뽑는다."""
    if not os.path.isfile(path):
        raise ParseError(f"run.log 가 없다: {path}")
    lines = open(path, encoding="utf-8", errors="replace").readlines()

    sections: list[dict] = []
    cur: dict | None = None
    extras = {
        "지오코딩": None,
        "캐시_문장": [],
        "정제_데이터셋": [],
        "후보_필지": None,
        "loaded": [],
        "cascaded": [],
    }

    i = 0
    while i < len(lines):
        raw = lines[i].rstrip("\n")

        if raw.startswith("$ "):
            argv = raw[2:].strip()
            script = None
            for tok in argv.split():
                if tok.lower().endswith(".py"):
                    script = os.path.basename(tok)
                    break
            cur = {"script": script, "argv": argv, "타이밍": None}
            sections.append(cur)
            i += 1
            continue

        if raw.strip() == _TIME_HEADER:
            where = (cur or {}).get("script") or f"line {i+1}"
            block, i = _parse_timing_block(lines, i, str(where))
            if cur is None:
                raise ParseError(f"{path}: 자식 명령줄('$ ...') 앞에 '[소요 시간]' 이 나왔다")
            if cur["타이밍"] is not None:
                raise ParseError(f"{where}: 한 자식에 '[소요 시간]' 표가 둘 이상이다")
            cur["타이밍"] = block
            continue

        mg = _GEO_RE.search(raw)
        if mg:
            extras["지오코딩"] = {
                "행": _num(mg.group(1)),
                "고유": _num(mg.group(2)),
                "캐시_적중": _num(mg.group(3)),
                "신규_호출": _num(mg.group(4)),
                "소요_시간_sec": None,
                "소요_시간_사유": "지오코딩 구간에 계측이 없다 — 건수만 찍는다",
            }
        if _CACHE_LINE_RE.search(raw):
            extras["캐시_문장"].append(raw.strip())
        mc = _CLEAN_RE.match(raw)
        if mc:
            extras["정제_데이터셋"].append({
                "dataset_id": mc.group(1),
                "파일": mc.group(2).strip(),
                "rows_before": _num(mc.group(3)),
                "rows_after": _num(mc.group(4)),
                "n_flags": int(mc.group(5)),
                "sec": float(mc.group(6)) if mc.group(6) else None,
            })
        mp = _PARCEL_RE.match(raw)
        if mp:
            extras["후보_필지"] = _num(mp.group(1))
        ml = _LOADED_RE.match(raw)
        if ml:
            extras["loaded"].append({"table": ml.group(1), "run_id": ml.group(2),
                                     "rows": int(ml.group(3))})
        mx = _CASCADED_RE.match(raw)
        if mx:
            extras["cascaded"].append({"table": mx.group(1), "run_id": mx.group(2),
                                       "raw": mx.group(3).strip()})
        i += 1

    by_script: dict[str, list[dict]] = {}
    for s in sections:
        by_script.setdefault(s["script"] or "?", []).append(s)

    runpipe = [s for s in sections
               if s["script"] == "gam2_run_pipeline.py" and s["타이밍"]]
    if require_runpipe and not runpipe:
        raise ParseError(
            f"{path}: gam2_run_pipeline.py 의 '[소요 시간]' 표를 못 찾았다. "
            "STEP 0/0.5/1/2 구분은 여기에만 있다 — 이 숫자가 없으면 문서의 핵심 주장이 사라진다"
        )

    return {"sections": sections, "by_script": by_script, "extras": extras}


# ─────────────────────────────────────────────────────────────
# 산출물에서 읽는 값
# ─────────────────────────────────────────────────────────────
def _load_json(path: str):
    if not os.path.isfile(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception as e:
        raise ParseError(f"산출물 JSON 을 못 읽었다: {path} — {e}")


def collect_artifacts(run_dir: str, domain: str) -> dict:
    pre = domain_prefix(domain)
    out: dict = {}

    rev = _load_json(os.path.join(run_dir, "step1", f"{pre}_audit_result_reviewed.json"))
    if rev is None:
        out["감리"] = {"값": None, "사유": "step1 reviewed.json 없음 (이 모드는 STEP1 을 안 돈다)"}
    else:
        roles: dict[str, int] = {}
        flags: dict[str, int] = {}
        n_flags = 0
        ref_only = 0
        for r in rev.get("results") or []:
            for role in r.get("roles") or []:
                key = role.get("role") or "미상"
                roles[key] = roles.get(key, 0) + 1
            for f in r.get("hitl_flags") or []:
                n_flags += 1
                k = f.get("type") or "미상"
                flags[k] = flags.get(k, 0) + 1
        out["감리"] = {
            "데이터셋_수": len(rev.get("results") or []),
            "role별": roles,
            "hitl_flags_총건수": n_flags,
            "hitl_flags_유형별": flags,
            "facility_inference": rev.get("facility_inference"),
        }
        out["감리"]["reference_only"] = ref_only or None

    cr = _load_json(os.path.join(run_dir, "step2", f"{pre}_clean_report.json"))
    if cr is None:
        out["정제"] = {"값": None, "사유": "step2 clean_report.json 없음"}
    else:
        res = cr.get("results") or []
        out["정제"] = {
            "데이터셋_수": len(res),
            "rows_before_합": sum(int(r.get("rows_before") or 0) for r in res),
            "rows_after_합": sum(int(r.get("rows_after") or 0) for r in res),
            "n_flags_합": sum(int(r.get("n_flags") or 0) for r in res),
            "reference_only_수": sum(1 for r in res if r.get("reference_only")),
            "gis_input_수": sum(1 for r in res if r.get("gis_input")),
        }

    ws = _load_json(os.path.join(run_dir, "step3", f"{pre}_weight_set.json"))
    if ws is None:
        out["가중치"] = {"값": None, "사유": "step3 weight_set.json 없음"}
    else:
        out["가중치"] = {
            "지표_수": len(ws.get("indicators") or []),
            "scale": ws.get("scale"),
            "decay": ws.get("decay"),
        }

    rp = _load_json(os.path.join(run_dir, "step4", f"{pre}_report.json"))
    if rp is None:
        out["선정"] = {"값": None, "사유": "step4 report.json 없음"}
    else:
        counts = rp.get("counts") or {}
        cov = rp.get("coverage") or {}
        sp = rp.get("spatial") or {}
        gaps: dict[str, int] = {}
        for g in rp.get("data_gap") or []:
            k = g.get("kind") or "미상"
            gaps[k] = gaps.get(k, 0) + 1
        out["선정"] = {
            "후보_필지": counts.get("parcels"),
            "후보점": counts.get("points"),
            "생존": counts.get("survive"),
            "배제된_후보점": (counts.get("points") - counts.get("survive"))
            if counts.get("points") is not None and counts.get("survive") is not None else None,
            "배제_union_km2": sp.get("exclusion_union_km2"),
            "최종_선정_지점": len(rp.get("topn") or []),
            "수요점": cov.get("n_demand"),
            "커버_쌍": cov.get("cover_pairs"),
            "커버리지_reach": cov.get("reach"),
            "커버리지_knee": cov.get("knee"),
            "커버리지_ceiling": cov.get("ceiling"),
            "data_gap_건수": len(rp.get("data_gap") or []),
            "data_gap_종류별": gaps,
        }

    gates = {}
    for gid in ("audit", "weight"):
        p = os.path.join(run_dir, "hitl", f"{gid}_answer.json")
        gates[gid] = _load_json(p)
    out["게이트_답변"] = gates
    return out


def domain_inputs(domain: str) -> dict:
    """`datasets/<도메인>/` 의 **현재** 크기. run 시점의 값이 아니다 — 그래서 따로 적는다."""
    base = os.path.join(str(DATA_ROOT), domain)
    res: dict = {"경로": base, "존재": os.path.isdir(base),
                 "주의": "run 시점이 아니라 **지금** 디스크 상태다 (업로드로 바뀔 수 있다)"}
    for sub in ("data", "law"):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            res[sub] = {"값": None, "사유": f"{d} 없음"}
            continue
        files = []
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                files.append({"파일": name, "bytes": os.path.getsize(p)})
        res[sub] = {"파일_수": len(files), "총_bytes": sum(f["bytes"] for f in files),
                    "파일": files}
    return res


# ─────────────────────────────────────────────────────────────
# run 하나를 요약한다
# ─────────────────────────────────────────────────────────────
def _wall_seconds(status: dict) -> tuple[float | None, str | None]:
    a, b = status.get("started_at"), status.get("finished_at")
    if not a or not b:
        return None, "started_at 또는 finished_at 이 없다"
    try:
        d = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except Exception as e:
        return None, f"시각 파싱 실패: {e}"
    return d, None


def summarize_run(run_dir: str) -> dict:
    run_id = os.path.basename(run_dir)
    status = _load_json(os.path.join(run_dir, "status.json"))
    if status is None:
        raise ParseError(f"{run_id}: status.json 이 없다")

    mode = status.get("mode")
    steps = status.get("steps") or []

    unmanned, loading, missing = 0.0, 0.0, []
    for s in steps:
        sec = s.get("sec")
        if sec is None:
            missing.append(s.get("id"))
            continue
        if s.get("id") in LOAD_STEP_IDS:
            loading += sec
        else:
            unmanned += sec

    wall, wall_reason = _wall_seconds(status)
    log = parse_run_log(os.path.join(run_dir, "run.log"),
                        require_runpipe=(mode == "full"))

    # ── 감리 판정 비중 — `status.json` 으로는 못 구한다 (단계 "0" 이 STEP 0.5 를 흡수한다)
    audit_share: dict = {"값": None, "사유": "mode 가 full 이 아니라 STEP0~1 이 없다"}
    rp_sections = [s for s in log["sections"]
                   if s["script"] == "gam2_run_pipeline.py" and s["타이밍"]]
    if rp_sections:
        rows = rp_sections[0]["타이밍"]["rows"]
        total = rp_sections[0]["타이밍"]["합계"]
        step1 = next((v["sec"] for k, v in rows.items() if k.startswith("STEP 1")), None)
        audit_share = {
            "세부": {k: v["sec"] for k, v in rows.items()},
            "합계_sec": total,
            "STEP1_감리판정_sec": step1,
            "비율": round(step1 / total, 4) if step1 is not None and total else None,
            "주의": "이 표는 run.log 에만 있다 — status.json 의 단계 '0' 은 STEP 0.5 를 흡수한다",
        }

    # ── 제안 패스: `step_ids` 가 비어 있어 어느 단계에도 안 잡힌다
    propose = [s for s in log["sections"] if "--propose-only" in s["argv"]]
    propose_sec = None
    if propose and propose[0]["타이밍"]:
        propose_sec = propose[0]["타이밍"].get("체감") or propose[0]["타이밍"].get("합계")

    total_steps = unmanned + loading
    diff = round(wall - total_steps, 2) if wall is not None else None

    return {
        "run_id": run_id,
        "domain": status.get("domain"),
        "mode": mode,
        "status": status.get("status"),
        "user_id": status.get("user_id"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
        "steps": [{"id": s.get("id"), "label": s.get("label"),
                   "status": s.get("status"), "sec": s.get("sec")} for s in steps],
        "헤드라인": {
            "무인_구간_합계_sec": round(unmanned, 2),
            "적재_sec": round(loading, 2),
            "적재_내역": {s.get("id"): s.get("sec") for s in steps
                          if s.get("id") in LOAD_STEP_IDS},
            "steps_전체_합계_sec": round(total_steps, 2),
            "게이트_포함_벽시계_sec": wall,
            "벽시계_사유": wall_reason,
            "차이_sec": diff,
            "차이_구성": "게이트 대기 + 프로세스 기동·전환 + --propose-only 제안 패스",
            "제안패스_sec": propose_sec,
            "제안패스_주의": "step_ids 가 비어 있어 steps 에 안 들어간다",
            "sec_없는_단계": missing or None,
            "정밀도": "steps[].sec 는 10ms · 벽시계는 1초(±1s)",
        },
        "감리_비중": audit_share,
        "run_log_세부": {
            s["script"]: {"argv": s["argv"], "타이밍": s["타이밍"]}
            for s in log["sections"] if s["타이밍"]
        },
        "run_log_자식_순서": [s["script"] for s in log["sections"]],
        "지오코딩": log["extras"]["지오코딩"],
        "캐시_문장": log["extras"]["캐시_문장"],
        "정제_데이터셋": log["extras"]["정제_데이터셋"],
        "적재_기록": {"loaded": log["extras"]["loaded"],
                      "cascaded": log["extras"]["cascaded"],
                      "status_loaded": status.get("loaded")},
        "산출물": collect_artifacts(run_dir, status.get("domain") or ""),
        "측정_불가": {
            "지오코딩_소요시간": "구간 계측이 없다 (건수만)",
            "LLM_호출_횟수": "공통 카운터가 없다",
            "게이트_대기시간": "답변 시각은 있으나 질문 제시 시각이 없다",
            "사람_검토시간": "측정 대상이 아니다 (범위 밖)",
        },
    }


def iter_runs(domain: str) -> list[str]:
    if not os.path.isdir(RUNS_ROOT):
        raise SystemExit(f"[중단] runs/ 가 없다: {RUNS_ROOT}")
    out = []
    for name in sorted(os.listdir(RUNS_ROOT)):
        if not name.startswith("r_"):
            continue
        d = os.path.join(RUNS_ROOT, name)
        sp = os.path.join(d, "status.json")
        if not os.path.isfile(sp):
            continue
        try:
            st = json.load(open(sp, encoding="utf-8"))
        except Exception:
            continue
        if st.get("domain") == domain:
            out.append(d)
    return out


# ─────────────────────────────────────────────────────────────
# 콘솔 출력
# ─────────────────────────────────────────────────────────────
def _fmt(v, w=9, nd=2):
    return ("-" if v is None else f"{v:.{nd}f}").rjust(w)


def print_run(r: dict) -> None:
    h = r["헤드라인"]
    print("=" * 92)
    print(f"[{r['run_id']}]  {r['domain']} · mode={r['mode']} · {r['status']}"
          f"  ({r['started_at']} → {r['finished_at']})")
    print("-" * 92)
    for s in r["steps"]:
        mark = "적재" if s["id"] in LOAD_STEP_IDS else "무인"
        print(f"  {mark}  {str(s['id']):<8} {_fmt(s['sec'])}s  {s['label']}")
    print("-" * 92)
    print(f"  ▶ 무인 구간 합계 (Σsec)   {_fmt(h['무인_구간_합계_sec'])}s   ← 발표에 쓰는 숫자")
    print(f"    적재(DB 인프라)          {_fmt(h['적재_sec'])}s   ← 판단 파이프라인 아님")
    print(f"    steps 전체 합계          {_fmt(h['steps_전체_합계_sec'])}s")
    print(f"  ▶ 게이트 포함 벽시계       {_fmt(h['게이트_포함_벽시계_sec'])}s   ← 사람 대기 포함, 별도 표기")
    print(f"    차이                    {_fmt(h['차이_sec'])}s   ({h['차이_구성']})")
    print(f"      · 제안 패스            {_fmt(h['제안패스_sec'])}s   ({h['제안패스_주의']})")
    if h["sec_없는_단계"]:
        print(f"    🔴 sec 이 없는 단계: {h['sec_없는_단계']}")

    a = r["감리_비중"]
    print("-" * 92)
    if a.get("값", "x") is None:
        print(f"  감리 판정 비중 : 측정 불가 — {a['사유']}")
    else:
        print("  [run.log] STEP 세부 (status.json 으로는 STEP 0.5 가 안 갈린다)")
        for k, v in a["세부"].items():
            print(f"      {k:<26} {_fmt(v)}s")
        print(f"      {'합계':<26} {_fmt(a['합계_sec'])}s")
        if a["비율"] is not None:
            print(f"  ▶ 감리 판정이 STEP0~2 에서 차지하는 비중  {a['비율']*100:.1f}%")

    g = r["지오코딩"]
    print("-" * 92)
    if g:
        print(f"  지오코딩  {g['행']}행 · 고유 {g['고유']}건 · 캐시 적중 {g['캐시_적중']}건 "
              f"· 신규 호출 {g['신규_호출']}건   (소요 시간: 측정 불가)")
    else:
        print("  지오코딩  로그에 줄이 없다 — 측정 불가")
    for line in r["캐시_문장"]:
        print(f"  캐시      {line}")

    art = r["산출물"]
    sel = art.get("선정") or {}
    aud = art.get("감리") or {}
    cln = art.get("정제") or {}
    print("-" * 92)
    if sel.get("값", "x") is None:
        print(f"  선정 결과 : 없음 — {sel.get('사유')}")
    else:
        print(f"  후보 필지 {sel['후보_필지']:,} · 후보점 {sel['후보점']:,} · 생존 {sel['생존']:,}"
              f" (배제 {sel['배제된_후보점']:,})")
        print(f"  배제 union {sel['배제_union_km2']} km² · 최종 선정 {sel['최종_선정_지점']}지점"
              f" · 수요점 {sel['수요점']:,} · 커버 쌍 {sel['커버_쌍']:,}")
        print(f"  커버리지 reach {sel['커버리지_reach']} · knee {sel['커버리지_knee']}"
              f" · ceiling {sel['커버리지_ceiling']}")
        print(f"  data_gap {sel['data_gap_건수']}건 {sel['data_gap_종류별']}")
    if cln.get("값", "x") is not None:
        print(f"  정제 데이터셋 {cln['데이터셋_수']}개 · {cln['rows_before_합']:,}"
              f"→{cln['rows_after_합']:,}행 · flag {cln['n_flags_합']}개")
    if aud.get("값", "x") is not None:
        print(f"  감리 role {aud['role별']} · hitl_flags {aud['hitl_flags_총건수']}건 "
              f"{aud['hitl_flags_유형별']}")


# ─────────────────────────────────────────────────────────────
# --cold : LLM 캐시 비우기 (백업 → 삭제 → 실행 → 복원)
# ─────────────────────────────────────────────────────────────
def _llm_cache_files() -> list[str]:
    return [p for p in (str(JIMOK_CACHE_PATH), str(FACILITY_PARAM_CACHE_PATH))
            if os.path.isfile(p)]


def _geocode_cache_files() -> list[str]:
    d = str(GEOCODE_CACHE_DIR)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if os.path.isfile(os.path.join(d, n))]


def cold_clear(confirm: bool) -> str | None:
    """LLM 캐시만 백업 후 삭제한다. 백업 폴더 경로를 돌려준다."""
    llm = _llm_cache_files()
    geo = _geocode_cache_files()

    print("=" * 92)
    print("[--cold] 지울 파일 — **LLM 캐시만** 지운다")
    print("=" * 92)
    if not llm:
        print("  (지울 LLM 캐시가 없다 — 이미 cold 다)")
    for p in llm:
        print(f"  삭제  {p}  ({os.path.getsize(p):,} bytes)")
    print("-" * 92)
    print("  🔴 아래는 **안 지운다** — LLM 이 아니라 VWorld 주소 검색 캐시다.")
    for p in geo:
        print(f"  유지  {p}  ({os.path.getsize(p):,} bytes)")
    print("-" * 92)
    print("  ⚠ `jimok_role_cache.json` 을 지우면 지목 역할을 **LLM 이 다시 판정**한다.")
    print("    판정이 달라지면 배제 면적·후보 필지 수가 바뀔 수 있다(회귀가 아니라 재판정이다).")
    print("    → 실행 뒤 **원본을 되돌린다**. 되돌리기 전에 죽으면 백업 경로를 출력한다.")
    if not llm:
        return None
    if not confirm and input("  진행할까? [y/N] ").strip().lower() != "y":
        raise SystemExit("[중단] 사람이 취소했다")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(RUNS_ROOT, f"_cache_backup_{ts}")
    os.makedirs(backup, exist_ok=True)
    for p in llm:
        shutil.copy2(p, os.path.join(backup, os.path.basename(p)))
        os.remove(p)
    print(f"  [백업] {backup}")
    return backup


def cold_restore(backup: str) -> None:
    for name in sorted(os.listdir(backup)):
        src = os.path.join(backup, name)
        dst = (str(JIMOK_CACHE_PATH) if name == os.path.basename(str(JIMOK_CACHE_PATH))
               else str(FACILITY_PARAM_CACHE_PATH))
        shutil.copy2(src, dst)
        print(f"  [복원] {dst}")


# ─────────────────────────────────────────────────────────────
# --run : 살아 있는 서버로 새 실행
# ─────────────────────────────────────────────────────────────
def _http(url: str, payload=None, method="GET", timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


def _find_answers(domain: str, explicit: str | None) -> tuple[str, dict]:
    """게이트 답변을 이전 run 에서 가져온다 — 지어내지 않는다."""
    cands = []
    for d in iter_runs(domain):
        a = os.path.join(d, "hitl", "audit_answer.json")
        w = os.path.join(d, "hitl", "weight_answer.json")
        if os.path.isfile(a) and os.path.isfile(w):
            cands.append(d)
    if explicit:
        d = os.path.join(RUNS_ROOT, explicit)
        if d not in cands:
            raise SystemExit(f"[중단] {explicit} 에 두 게이트 답변이 다 있지 않다")
        pick = d
    else:
        if not cands:
            raise SystemExit(
                f"[중단] '{domain}' 도메인에 게이트 답변이 남은 run 이 없다. "
                "--answers <run_id> 로 지목하거나, 게이트 답변을 손으로 넣어야 한다")
        pick = cands[-1]
    ans = {g: json.load(open(os.path.join(pick, "hitl", f"{g}_answer.json"),
                             encoding="utf-8"))["answer"]
           for g in ("audit", "weight")}
    return os.path.basename(pick), ans


def do_run(domain: str, user_input: str, topn: int, api: str,
           answers_from: str | None, confirm: bool, timeout: int) -> str:
    src_run, answers = _find_answers(domain, answers_from)

    print("=" * 92)
    print(f"[--run] '{domain}' 을 mode=full 로 **새로 실행**한다 (살아 있는 서버: {api})")
    print("=" * 92)
    print("  바뀌는 것")
    print(f"   · runs/r_<날짜>_NNN/ 이 새로 생긴다 — 번호는 원장에서 발급되고 **안 되돌아간다**")
    print(f"   · DB `audit_rules`·`booth_candidates` 에 새 run_id 로 행이 생긴다")
    print(f"     → `/candidates?domain={domain}` (run_id 없이) 의 기본 목록이 **이 run 으로 이동**한다")
    print(f"   · datasets/{domain}/fixture/profiles.json 을 덮어쓴다 (full 은 항상 --reprofile)")
    print(f"   · datasets/search_cache/* 가 갱신될 수 있다 (지오코딩 신규 주소)")
    print(f"   · datasets/{domain}/law/*.pdf.txt 가 생길 수 있다")
    print("   · LLM 유료 호출 — STEP1 감리 판정 (gpt-4o)")
    print("  안 바뀌는 것 (근거: pipeline_runner._child_env 가 STEP1~4 출력 경로를 run 폴더로 돌린다)")
    print("   · 정본 datasets/step{1,2,3,4}_output/")
    print(f"   · 기준선 datasets/{domain}_FIX/")
    print("-" * 92)
    print(f"  게이트 답변은 **{src_run}** 의 것을 그대로 재사용한다 (지어내지 않는다):")
    for g in ("audit", "weight"):
        print(f"   · {g}: {json.dumps(answers[g], ensure_ascii=False)}")
    print(f"  user_input={user_input!r} · topn={topn}")
    print("-" * 92)
    if not confirm and input("  진행할까? [y/N] ").strip().lower() != "y":
        raise SystemExit("[중단] 사람이 취소했다")

    code, body = _http(f"{api}/api/v1/pipeline/runs", method="POST",
                       payload={"domain": domain, "mode": "full",
                                "user_input": user_input, "topn": topn})
    if code != 202:
        raise SystemExit(f"[중단] 실행 시작 실패 ({code}): {body}")
    run_id = body["run_id"]
    print(f"  [시작] {run_id}")

    t0 = time.time()
    seen: set = set()
    while True:
        if time.time() - t0 > timeout:
            raise SystemExit(f"[중단] {timeout}초 안에 안 끝났다 — run_id={run_id} "
                             "(타임아웃은 '여기까진 안 끝났다'만 증명한다)")
        code, st = _http(f"{api}/api/v1/pipeline/runs/{run_id}")
        if code != 200:
            raise SystemExit(f"[중단] status 조회 실패 ({code}): {st}")
        for s in st.get("steps") or []:
            key = (s.get("id"), s.get("status"))
            if key not in seen and s.get("status") in ("running", "done", "failed"):
                seen.add(key)
                sec = s.get("sec")
                shown = "        -" if sec is None else f"{sec:8.2f}s"
                print(f"    {s.get('id'):<10} {s.get('status'):<8} {shown}"
                      f"  {s.get('label')}")
        stt = st.get("status")
        if stt == "awaiting_hitl":
            gid = (st.get("gate") or {}).get("id")
            if gid not in answers:
                raise SystemExit(f"[중단] 모르는 게이트: {gid}")
            print(f"    [게이트 {gid}] 저장해둔 답변을 보낸다")
            c2, b2 = _http(f"{api}/api/v1/pipeline/runs/{run_id}/hitl/{gid}",
                           method="POST", payload=answers[gid])
            if c2 != 200:
                print(f"    🔴 게이트 답변 거절 ({c2}): {b2}")
                print(f"    현재 질문 목록: "
                      f"{json.dumps((st.get('gate') or {}).get('questions'), ensure_ascii=False)[:1500]}")
                raise SystemExit("[중단] 이전 run 의 답이 이번 run 의 질문과 안 맞는다")
            continue
        if stt in ("succeeded", "failed"):
            print(f"  [종료] {stt}  {st.get('error') or ''}")
            return run_id
        time.sleep(2)


# ─────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="파이프라인 실행시간 실측")
    ap.add_argument("--domain", required=True, help="도메인 폴더명 (하드코딩 없음)")
    ap.add_argument("--run", action="store_true", help="새 실행 (승인 필요)")
    ap.add_argument("--cold", action="store_true", help="LLM 캐시를 비우고 실행 (승인 필요)")
    ap.add_argument("--out", default=None, help="결과 JSON 경로")
    ap.add_argument("--api", default="http://127.0.0.1:8000",
                    help="살아 있는 서버 (localhost 아님 — IPv6 폴백 130초 함정)")
    ap.add_argument("--user-input", default=None, help="--run 일 때 STEP0.5 가 읽는 사용자 의도")
    ap.add_argument("--topn", type=int, default=20)
    ap.add_argument("--answers", default=None, help="게이트 답변을 가져올 run_id")
    ap.add_argument("--yes", action="store_true", help="y/n 확인 생략")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()

    if a.cold and not a.run:
        raise SystemExit("[중단] --cold 는 --run 과 같이 쓴다 (캐시를 비우고 **실행**해야 의미가 있다)")

    backup = None
    new_run_id = None
    try:
        if a.run:
            if not a.user_input:
                raise SystemExit("[중단] --run 에는 --user-input 이 필요하다 "
                                 "(STEP0.5 가 시설·지역을 여기서 확정한다. 추측하지 않는다)")
            if a.cold:
                backup = cold_clear(a.yes)
            new_run_id = do_run(a.domain, a.user_input, a.topn, a.api,
                                a.answers, a.yes, a.timeout)
    finally:
        if backup:
            print("-" * 92)
            cold_restore(backup)

    runs = iter_runs(a.domain)
    if not runs:
        raise SystemExit(f"[중단] '{a.domain}' 도메인의 run 폴더가 없다: {RUNS_ROOT}")

    results, skipped = [], []
    for d in runs:
        try:
            results.append(summarize_run(d))
        except ParseError as e:
            skipped.append({"run_id": os.path.basename(d), "사유": str(e)})

    for r in results:
        print_run(r)

    if skipped:
        print("=" * 92)
        print("🔴 파싱 실패로 집계에서 빠진 run (조용히 넘기지 않는다)")
        for s in skipped:
            print(f"  {s['run_id']}  — {s['사유']}")

    doc = {
        "생성시각": datetime.now().isoformat(timespec="seconds"),
        "도메인": a.domain,
        "저장소_루트": _ROOT,
        "모드": "신규실행" if a.run else "집계",
        "cold": bool(a.cold),
        "이번_실행_run_id": new_run_id,
        "runs": results,
        "파싱실패": skipped,
        "도메인_입력데이터": domain_inputs(a.domain),
        "계측_한계": {
            "계측층": {
                "status.json": "러너의 perf_counter · 10ms · 단계 경계는 자식 stdout 마커",
                "run.log": "자식이 스스로 찍은 세부 표 · 텍스트 파싱",
            },
            "단계0_흡수": "_RUNPIPE_MARKERS 에 STEP 0.5 마커가 없어 단계 '0' 이 STEP 0.5 를 흡수한다",
            "벽시계_정밀도": "started_at·finished_at 은 timespec='seconds' (±1s)",
            "Σsec≠벽시계": "게이트 대기 + 프로세스 기동·전환 + --propose-only 제안 패스",
        },
    }

    out = a.out or os.path.join(
        RUNS_ROOT, "_metrics",
        f"runtime_metrics_{a.domain}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("=" * 92)
    print(f"[저장] {out}")
    print(f"  run {len(results)}건 집계 · 파싱실패 {len(skipped)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
