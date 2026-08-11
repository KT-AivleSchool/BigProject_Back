# -*- coding: utf-8 -*-
"""화면5 B 엔진 **정상 경로** 관통 (in-process ASGI · 🔴 LLM 다회 · 유료).

    python app\\tools\\check_stakeholders_e2e.py [--parcel 2] [--personas 3] [--cleanup]

`check_stakeholders.py` 는 **거절 경로만** 본다(400·404·409·502). 정상 경로는
다인 LLM 토론이라 안 쳤고, 그래서 2026-08-11 까지 `/stakeholders/*` 는
「구현됐지만 한 번도 안 돌려본」 자리였다. 이 대조기가 실제로 돌린다:

    /generate → 페르소나 고르기 → /dynamic/discuss/stream(SSE)
    → hearing_results_b 저장 → /simulations/hearings/b/{id} → ?engine=B 목록

🔴 **돈이 든다.** 페르소나 수만큼 발화가 늘어난다 — 기본 3명으로 줄여 돌린다.
🔴 **lifespan 을 안 띄운다**(`httpx.ASGITransport` 기본). 띄우면 `reap_orphans` 가
   남의 run 을 닫고 부팅 정리가 `runs/` 산출물을 지운다.
🔴 **쓰는 대조기다** — `hearing_results_b` 에 한 행이 남는다. 그게 정상이다
   (프런트가 `?engine=B` 를 확인할 실물이 된다). 지우려면 `--cleanup`.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402

API = "http://test/api/v1"
ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


async def main(parcel_id: int, n_personas: int, cleanup: bool):
    transport = httpx.ASGITransport(app=app)
    hearing_id = None

    async with httpx.AsyncClient(
        transport=transport, base_url=API, timeout=900.0
    ) as c:
        print(f"--- 1) /stakeholders/generate  parcel_id={parcel_id}")
        t0 = time.time()
        g = await c.post("/stakeholders/generate", json={"parcel_id": parcel_id})
        chk("200", g.status_code == 200, f"-> {g.status_code} {g.text[:120]}")
        if g.status_code != 200:
            return 1
        cands = g.json()
        print(f"      {len(cands)}명 · {time.time() - t0:.1f}s")
        chk("후보 ≥1", len(cands) >= 1, f"-> {len(cands)}명")
        # 🔴 여기서 확인하는 건 개수가 아니라 **어휘**다. 옛 별칭(name·role)이
        #    되살아나면 `/discuss` 가 400 이 된다(2026-08-11 별칭 제거).
        keys = ("display_name", "stakeholder_type", "relationship_to_topic")
        chk(
            "정본 어휘 3키 전건",
            all(all(k in p and p[k] for k in keys) for p in cands),
            f"-> 첫 항목 키 {sorted(cands[0].keys())}",
        )
        for p in cands[:n_personas]:
            print(
                f"      · {p['display_name']} / {p['stakeholder_type']} "
                f"/ 등급 {p.get('importance_grade')}"
            )

        personas = cands[:n_personas]
        print(f"--- 2) /stakeholders/dynamic/discuss/stream  ({len(personas)}명)")
        events, done = [], False
        t0 = time.time()
        async with c.stream(
            "POST",
            "/stakeholders/dynamic/discuss/stream",
            json={"personas": personas, "parcel_id": parcel_id},
        ) as resp:
            chk("200", resp.status_code == 200, f"-> {resp.status_code}")
            chk(
                "content-type 이 SSE",
                "text/event-stream" in resp.headers.get("content-type", ""),
                f"-> {resp.headers.get('content-type')}",
            )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    done = True
                    continue
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    chk("이벤트가 JSON", False, f"-> {payload[:60]}")
                    continue
                events.append(ev)
        dur = time.time() - t0
        by = {}
        for ev in events:
            by[ev.get("type")] = by.get(ev.get("type"), 0) + 1
        print(f"      {len(events)} events · {dur:.1f}s · {by}")

        chk("끝이 [DONE]", done)
        chk("이벤트 ≥1", len(events) >= 1, f"-> {len(events)}")
        chk("seq 는 0부터 연속", [e.get("seq") for e in events] == list(range(len(events))))
        allowed = {"message", "evaluation", "report", "raw", "error", "saved", "save_failed"}
        bad = sorted({e.get("type") for e in events} - allowed)
        chk("알려진 type 만", not bad, f"-> 낯선 type {bad}")
        chk("error 이벤트 없음", by.get("error", 0) == 0, f"-> {by.get('error', 0)}건")

        msgs = [e for e in events if e.get("type") == "message"]
        chk("발화 ≥1", len(msgs) >= 1, f"-> {len(msgs)}")
        chk(
            "발화마다 speaker 3키",
            all(
                isinstance(m.get("speaker"), dict)
                and {"id", "name", "kind"} <= set(m["speaker"])
                for m in msgs
            ),
        )
        # 🔴 접두 형식이 어긋나면 `_split_speaker` 가 못 자르고 전부 unknown 이 된다 —
        #    예외가 안 나고 이름만 사라진다. 그래서 **persona 로 잘린 발화가 있는지**를 본다.
        kinds = {m["speaker"]["kind"] for m in msgs if isinstance(m.get("speaker"), dict)}
        n_persona_said = sum(
            1 for m in msgs if (m.get("speaker") or {}).get("kind") == "persona"
        )
        chk("페르소나 발화가 잘렸다", n_persona_said >= 1, f"-> {n_persona_said}건 · kinds={sorted(kinds)}")
        said = {(m["speaker"] or {}).get("name") for m in msgs}
        want = {p["display_name"] for p in personas}
        chk(
            "고른 페르소나가 실제로 말했다",
            want & said == want,
            f"-> 말한 사람 {sorted(x for x in said if x)}",
        )
        chk("evaluation ≥1", by.get("evaluation", 0) >= 1, f"-> {by.get('evaluation', 0)}")
        chk("report ≥1", by.get("report", 0) >= 1, f"-> {by.get('report', 0)}")
        rep = [e for e in events if e.get("type") == "report"]
        if rep:
            chk("최종 report 가 끝났다고 말한다", bool(rep[-1].get("is_finished")))
            chk("시나리오 비어 있지 않다", bool(rep[-1].get("final_scenarios")))

        saved = [e for e in events if e.get("type") == "saved"]
        chk("saved 이벤트 1건", len(saved) == 1, f"-> {len(saved)} (save_failed {by.get('save_failed', 0)})")
        if not saved:
            return 1
        hearing_id = saved[0]["hearing_id"]
        chk("engine=B", saved[0].get("engine") == "B")
        chk(
            "result_url 이 규약대로",
            saved[0].get("result_url") == f"/api/v1/simulations/hearings/b/{hearing_id}",
            f"-> {saved[0].get('result_url')}",
        )

        print(f"--- 3) 결과 조회  hearing_id={hearing_id}")
        rb = await c.get(f"/simulations/hearings/b/{hearing_id}")
        chk("200", rb.status_code == 200, f"-> {rb.status_code}")
        if rb.status_code == 200:
            d = rb.json()
            rj = d.get("result_json") or {}
            chk("engine B", rj.get("engine") == "B", f"-> {rj.get('engine')}")
            chk(
                "SSE 발화 수 == 저장된 발화 수",
                len(rj.get("messages") or []) == len(msgs),
                f"-> {len(rj.get('messages') or [])} vs {len(msgs)}",
            )
            chk(
                "event_count == SSE 이벤트 수",
                # `saved` 는 저장 뒤에 나가므로 그것만 빠진다.
                rj.get("event_count") == len(events) - 1,
                f"-> {rj.get('event_count')} vs {len(events) - 1}",
            )
            # 🔴 근거 스냅샷 — 재적재로 `audit_rules` 가 갈아끼워져도 남아야 한다.
            b = rj.get("basis") or {}
            chk("basis 존재", bool(b))
            for k in ("captured_at", "domain", "run_id", "facility_type",
                      "audit_context", "poi_context"):
                chk(f"basis.{k}", k in b, f"-> {str(b.get(k))[:40]}")
            chk("basis.audit_context 비어있지 않다", bool(b.get("audit_context")))

        print("--- 4) 목록에 뜨는가 (engine=B)")
        run_id = (rj.get("basis") or {}).get("run_id") if rb.status_code == 200 else None
        if run_id:
            hl = await c.get(
                "/simulations/hearings", params={"run_id": run_id, "engine": "B"}
            )
            chk("200", hl.status_code == 200, f"-> {hl.status_code}")
            if hl.status_code == 200:
                ids = [h["hearing_id"] for h in hl.json()["hearings"]]
                chk("방금 것이 목록에 있다", hearing_id in ids, f"-> {ids}")

    print("--- 5) 정리")
    async with AsyncSessionLocal() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM hearing_results_b WHERE id = :i"),
                {"i": hearing_id},
            )
        ).scalar_one()
        chk("DB 에 실제 행", n == 1, f"-> {n}행")
        if cleanup:
            await s.execute(
                text("DELETE FROM hearing_results_b WHERE id = :i"), {"i": hearing_id}
            )
            await s.commit()
            chk(
                "삭제됨",
                (
                    await s.execute(
                        text("SELECT count(*) FROM hearing_results_b WHERE id = :i"),
                        {"i": hearing_id},
                    )
                ).scalar_one()
                == 0,
            )
        else:
            print(f"  [--] hearing_results_b id={hearing_id} 는 **남긴다** (--cleanup 으로 삭제)")

    print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
    return 1 if fail else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--parcel", type=int, default=2)
    ap.add_argument("--personas", type=int, default=3)
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.parcel, a.personas, a.cleanup)))
