# -*- coding: utf-8 -*-
"""`GET /simulations/hearings` 대조 (in-process TestClient · LLM 0회).

    python app\\tools\\check_hearings.py

🔴 uvicorn 을 재시작하지 않는다. 포트도 프로세스도 안 쓴다.
🔴 실 DB 를 읽지만 **행 수를 기준으로 삼지 않는다** — 언제든 늘어난다.
   보는 건 관계다: 필지당 latest 정확히 1건 · 비최신은 `result_url` null ·
   최신의 `result_url` 은 실제로 200.
"""
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

BASE = "/api/v1/simulations/hearings"
ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


with TestClient(app) as c:
    print("--- 1) 정본 run 조회")
    r = c.get(BASE, params={"run_id": "정본"})
    chk("200", r.status_code == 200, f"-> {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        print(f"      count={d['count']}")
        # 🔴 `engine` 미지정이면 A·B 가 **섞여 온다**(2026-08-11). 두 엔진은 산출물
        #    지표가 겹치지 않아 키 집합도 다르다 — A 전용 검사에 B 행을 넣으면 KeyError 다.
        A = [h for h in d["hearings"] if h["engine"] == "A"]
        B = [h for h in d["hearings"] if h["engine"] == "B"]
        for h in A:
            print(
                f"      A sim={h['simulation_id']} parcel={h['parcel_id']} rank={h['rank']} "
                f"css={h['css_score']} sc={h['scenario_code']} logs={h['debate_log_count']} "
                f"latest={h['is_latest_for_parcel']} url={h['result_url']}"
            )
        for h in B:
            print(
                f"      B hearing={h['hearing_id']} parcel={h['parcel_id']} rank={h['rank']} "
                f"personas={h['persona_count']} msgs={h['message_count']} url={h['result_url']}"
            )
        chk("run_id 반영", d["run_id"] == "정본")
        chk("engine 은 A 아니면 B", len(A) + len(B) == len(d["hearings"]))
        chk("count == len", d["count"] == len(d["hearings"]))
        # 같은 필지에 여러 건이면 latest 는 정확히 1건 (A 만 해당)
        from collections import Counter

        per = Counter(h["parcel_id"] for h in A)
        lat = Counter(h["parcel_id"] for h in A if h["is_latest_for_parcel"])
        chk("필지당 latest 1건", all(lat[p] == 1 for p in per), f"필지별건수={dict(per)}")
        chk(
            "비최신은 URL null",
            all((h["result_url"] is None) != h["is_latest_for_parcel"] for h in A),
        )
        chk(
            "최신 result_url 이 200",
            all(
                c.get(h["result_url"]).status_code == 200
                for h in A
                if h["is_latest_for_parcel"]
            ),
        )
        # B 는 조회 키가 `hearing_results_b.id` 단건이라 **옛 건도 자기 URL** 을 갖는다.
        chk("B 는 result_url 전건 채워짐", all(h.get("result_url") for h in B), f"n={len(B)}")
        chk(
            "B result_url 이 200",
            all(c.get(h["result_url"]).status_code == 200 for h in B),
        )

    print("--- 2) domain 필터")
    r2 = c.get(BASE, params={"run_id": "정본", "domain": "흡연"})
    chk("흡연 200", r2.status_code == 200, f"-> {r2.status_code}")
    r3 = c.get(BASE, params={"run_id": "정본", "domain": "없는도메인"})
    chk("없는 도메인 404", r3.status_code == 404, f"-> {r3.status_code}")

    print("--- 3) 없는 run / 잘못된 인자")
    r4 = c.get(BASE, params={"run_id": "r_없음_999"})
    chk("없는 run 404", r4.status_code == 404, f"-> {r4.status_code}")

    # ── 404 의 `detail` 은 **객체**다 (2026-08-11, 프런트 합의) ──────────────────
    # 🔴 확인하는 건 "404 가 났다"가 아니라 **왜 없는지를 갈라서 말하는가**다.
    #    같은 404 라도 「run 이 없다」·「적재한 적 없다」·「적재했는데 지금 없다」·
    #    「domain 이 틀렸다」·「물어볼 수가 없다」는 서로 다른 사실이고, 접으면
    #    화면이 없는 말을 한다(원칙 4).
    #    `message` 는 **전 갈래에서 비면 안 된다** — 프런트는 모르는 `code` 를 만나면
    #    분기하지 않고 `message` 를 그대로 띄운다. 비면 화면에 코드값만 뜬다.
    print("--- 3-1) 404 detail 의 code 갈래")

    def detail_of(resp):
        try:
            d = resp.json().get("detail")
        except Exception:
            return None
        return d if isinstance(d, dict) else None

    def chk_detail(label, resp, code):
        d = detail_of(resp)
        chk(f"{label} — detail 이 객체", d is not None, f"-> {type(d).__name__}")
        if d is None:
            return
        chk(f"{label} — code={code}", d.get("code") == code, f"-> {d.get('code')}")
        chk(
            f"{label} — message 채워짐",
            isinstance(d.get("message"), str) and len(d["message"]) > 10,
            f"-> {str(d.get('message'))[:40]}…",
        )
        for k in ("run_id", "domain", "loaded", "current"):
            chk(f"{label} — {k} 키 존재", k in d)

    chk_detail("없는 run", r4, "UNKNOWN_RUN")
    # run 은 있는데 domain 만 안 맞는 자리. 다른 갈래로 답하면 "행이 없다"고
    # 말하는데 사실은 **있다** — 그래서 code 가 따로 있다.
    chk_detail("domain 불일치", r3, "DOMAIN_MISMATCH")
    d3 = detail_of(r3)
    if d3:
        chk(
            "domain 불일치 — 다른 domain 행 수를 같이 준다",
            (d3.get("current") or {}).get("booth_candidates_any_domain", 0) > 0,
            f"-> {(d3.get('current') or {}).get('booth_candidates_any_domain')}",
        )

    # 🔴 아래 둘은 **디스크의 `runs/` 상태에 의존한다.** 폴더가 정리되면 갈래가
    #    바뀌므로 없으면 건너뛴다(대조기가 남의 정리 결과로 실패하면 안 된다).
    import json as _json
    from app.services import pipeline_runner as _runner  # noqa: E402

    _seen: set[str] = set()
    for _rid in sorted(p.name for p in _runner.RUNS_ROOT.glob("r_*") if p.is_dir()):
        try:
            _doc = _json.loads((_runner.run_dir(_rid) / "status.json").read_text("utf-8"))
        except Exception:
            continue
        _ld = _doc.get("loaded")
        _want = (
            "LOADED_BUT_MISSING"
            if isinstance(_ld, dict) and (_ld.get("booth_candidates") or 0) > 0
            else "NEVER_LOADED"
        )
        if _want in _seen:
            continue
        _r = c.get(BASE, params={"run_id": _rid})
        if _r.status_code != 404:
            continue  # 아직 DB 에 후보점이 남아 있는 run — 갈래 대상이 아니다
        _seen.add(_want)
        chk_detail(f"{_rid}({_want})", _r, _want)
    for _want in ("LOADED_BUT_MISSING", "NEVER_LOADED"):
        if _want not in _seen:
            print(f"  [--] {_want} — 해당하는 run 이 runs/ 에 없어 건너뜀")

    # 폴더는 있는데 `status.json` 을 못 읽는 자리. 「없다」가 아니라 **「물을 수 없다」**다.
    # 프런트가 명시로 물어온 갈래라 실제로 만들어서 확인한다(만들고 반드시 지운다).
    _bad = _runner.RUNS_ROOT / "r_대조_unreadable"
    try:
        _bad.mkdir(parents=True, exist_ok=True)
        (_bad / "status.json").write_text("{깨진 JSON", encoding="utf-8")
        rU = c.get(BASE, params={"run_id": _bad.name})
        chk("status.json 깨짐 → 404", rU.status_code == 404, f"-> {rU.status_code}")
        chk_detail("status 못 읽음", rU, "STATUS_UNREADABLE")
        dU = detail_of(rU)
        if dU:
            chk(
                "status 못 읽음 — 사유가 message 에 들어간다",
                "JSONDecodeError" in dU.get("message", "")
                or "status.json" in dU.get("message", ""),
                f"-> {dU.get('message', '')[:50]}…",
            )
    finally:
        for _f in _bad.glob("*"):
            _f.unlink()
        _bad.rmdir()
    chk("대조용 폴더 정리됨", not _bad.exists())

    chk("run_id 누락 422", c.get(BASE).status_code == 422)
    chk(
        "engine=C 400",
        c.get(BASE, params={"run_id": "정본", "engine": "C"}).status_code == 400,
    )
    # 🔴 2026-08-11 이전엔 **501**("저장 경로가 없다")이었다. 이제 `hearing_results_b`
    #    가 있으므로 200 이고, B 토론이 없으면 **빈 배열**이 참인 진술이다.
    rB = c.get(BASE, params={"run_id": "정본", "engine": "B"})
    chk("engine=B 200 (501 아님)", rB.status_code == 200, f"-> {rB.status_code}")
    rA = c.get(BASE, params={"run_id": "정본", "engine": "A"})
    chk("engine=A 200", rA.status_code == 200)
    if rA.status_code == rB.status_code == 200 and r.status_code == 200:
        chk("engine=B 는 B 만", all(h["engine"] == "B" for h in rB.json()["hearings"]))
        chk("engine=A 는 A 만", all(h["engine"] == "A" for h in rA.json()["hearings"]))
        chk(
            "미지정 = A + B",
            r.json()["count"] == rA.json()["count"] + rB.json()["count"],
            f"{r.json()['count']} == {rA.json()['count']}+{rB.json()['count']}",
        )
    chk(
        "없는 B 결과 404",
        c.get("/api/v1/simulations/hearings/b/999999").status_code == 404,
    )

    print("--- 4) 무회귀 (기존 경로)")
    chk(
        "/candidates 200",
        c.get("/api/v1/simulations/candidates", params={"domain": "흡연"}).status_code
        == 200,
    )

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
