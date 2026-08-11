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
    chk(
        "없는 run 404",
        c.get(BASE, params={"run_id": "r_없음_999"}).status_code == 404,
    )
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
