# -*- coding: utf-8 -*-
"""`/audit/*` 대조 (in-process TestClient).

    python app\\tools\\check_audit.py

🔴 **LLM 을 한 번도 안 부른다.** 분류기는 LLM 이 아니라 단어빈도 코사인이다.
🔴 `/save` 는 **실제로 `verified_precedents` 에 넣는다.** 넣고 **지운다**(끝에 확인).
   지우기 전에 죽으면 행이 남으므로 id 를 화면에 찍는다.
"""
import asyncio
import sys
from pathlib import Path

# `app/tools/` 기준 **두 단계 위**가 저장소 루트다(저장소 관례).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import fitz  # noqa: E402  PyMuPDF — `parser.py` 가 쓰는 것과 같은 것
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.core.audit_ai.classifier import audit_classifier  # noqa: E402
from app.core.audit_ai.parser import pdf_parser  # noqa: E402
from app.core.sim_ai.scenario import scenario_code, scenario_compare_text  # noqa: E402
from app.db.models.precedent import VerifiedPrecedent  # noqa: E402
from app.db.models.simulation import HearingResultA  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

ok = fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label} {extra}")
    else:
        fail += 1
        print(f"  [!!] {label} {extra}")


print("--- 1) scenario_code — 어휘 두 벌 + 'Scenario' 오판 (DB·LLM 안 씀)")
chk("A 엔진 키(scenario)", scenario_code({"scenario": "A"}) == "A")
chk("B 엔진 키(scenario_type)", scenario_code({"scenario_type": "Scenario A"}) == "A",
    str(scenario_code({"scenario_type": "Scenario A"})))
chk("🔴 'Scenario A' 가 C 로 안 읽힌다", scenario_code({"scenario": "Scenario A"}) == "A",
    str(scenario_code({"scenario": "Scenario A"})))
chk("'시나리오 C' 는 C", scenario_code({"scenario": "시나리오 C"}) == "C")
chk("템플릿 원문도 A", scenario_code({"scenario": "A (또는 B, C)"}) == "A")
chk("못 뽑으면 None (추측 안 함)", scenario_code({"scenario": "미정"}) is None)
chk("키 자체가 없어도 None", scenario_code({"summary": "x"}) is None)

print("--- 2) scenario_compare_text — summary 만 보지 않는다")
chk("summary 있으면 포함", "타결" in scenario_compare_text({"summary": "조건부 타결"}))
chk("summary 없어도 설명·사유로 만든다",
    scenario_compare_text({"scenario_description": "조건부 합의", "reason": "주민 우려"})
    == "조건부 합의\n주민 우려")
chk("텍스트가 아예 없으면 빈 문자열", scenario_compare_text({"scenario": "A"}) == "")

print("--- 3) classifier — 🔴 예전엔 matched_scenario 가 항상 'A' 였다")
A_SHAPE = [  # `app/templates/default/reporter.txt` 가 내는 실제 모양
    {"scenario": "C", "scenario_description": "협상 결렬",
     "summary": "주민 반대가 지속되어 입지 재검토가 필요하다", "reason": "합의 실패"},
]
r = audit_classifier.classify_actual_scenario("주민 반대가 지속되어 입지 재검토", A_SHAPE)
chk("A 엔진 모양에서 실제 코드 C 를 뽑는다", r["matched_scenario"] == "C",
    str(r["matched_scenario"]))
chk("유사도 > 0", r["similarity_score"] > 0, str(r["similarity_score"]))

r = audit_classifier.classify_actual_scenario(
    "주민 반대가 지속되어 입지 재검토", [{"scenario_type": "Scenario B", "summary": "입지 재검토"}])
chk("B 엔진 모양도 읽는다", r["matched_scenario"] == "B", str(r["matched_scenario"]))

r = audit_classifier.classify_actual_scenario("완전히 무관한 낱말", A_SHAPE)
chk("안 겹치면 UNCLASSIFIED", r["classification_status"] == "UNCLASSIFIED"
    and r["matched_scenario"] is None, r["classification_status"])

r = audit_classifier.classify_actual_scenario("아무 말", [{"scenario": "A"}])
chk("🔴 대조할 텍스트가 없으면 NO_PREDICTION (0.0 과 구분한다)",
    r["classification_status"] == "NO_PREDICTION", r["classification_status"])
r = audit_classifier.classify_actual_scenario("아무 말", [])
chk("시나리오 배열이 비어도 NO_PREDICTION", r["classification_status"] == "NO_PREDICTION")

r = audit_classifier.classify_actual_scenario(
    "입지 재검토", [{"scenario": "미정", "summary": "입지 재검토"}])
chk("코드를 못 뽑으면 None 이지 'A' 가 아니다", r["matched_scenario"] is None
    and r["similarity_score"] > 0, str(r["matched_scenario"]))

print("--- 3-2) parse_document_metadata — 🔴 한글 공문 관례를 못 읽었다")
_md = pdf_parser.parse_document_metadata
chk("라벨 붙은 문서번호", _md("문서번호: 용산-2026-1234")["document_no"] == "용산-2026-1234",
    str(_md("문서번호: 용산-2026-1234")["document_no"]))
chk("🔴 처리과명-일련번호 (행정업무규정 기본형)",
    _md("용산구청 도시계획과-12345 (2026. 8. 11.)")["document_no"] == "도시계획과-12345",
    str(_md("용산구청 도시계획과-12345")["document_no"]))
chk("제2026-1234호 (고시·공고)", _md("제2026-1234호")["document_no"] == "제2026-1234호")
chk("기존 세 토막도 그대로", _md("용산구-행정-12345호")["document_no"] == "용산구-행정-12345호")
chk("🔴 지번이 문서번호로 새지 않는다",
    _md("설치 위치는 용산구 이태원동 123-45 이다.")["document_no"] is None,
    str(_md("설치 위치는 용산구 이태원동 123-45 이다.")["document_no"]))
chk("번호가 없으면 None (지어내지 않는다)", _md("본 건은 종결 처리한다.")["document_no"] is None)
chk("🔴 시 생략 지번 (= booth_candidates.jibun 형식)",
    _md("용산구 이태원동 123-45")["parsed_jibun"] == "용산구 이태원동 123-45",
    str(_md("용산구 이태원동 123-45")["parsed_jibun"]))
chk("전체주소는 시까지 포함해 잡는다",
    _md("서울특별시 용산구 이태원동 123-45")["parsed_jibun"] == "서울특별시 용산구 이태원동 123-45")
chk("🔴 뒤 문장까지 삼키지 않는다",
    _md("용산구 이태원동 123-45 일대의 흡연부스 설치 건은 종결한다.")["parsed_jibun"]
    == "용산구 이태원동 123-45",
    str(_md("용산구 이태원동 123-45 일대의 설치 건은 종결한다.")["parsed_jibun"]))
chk("날짜 한글 표기", _md("2026년 8월 11일 준공")["parsed_date"] == "2026년8월11일")
chk("날짜 점 표기(공문 관례)", _md("2026. 8. 11.")["parsed_date"] == "2026-8-11")
# 🔴 시설 어휘는 주입식이다. 사전을 안 주면 **추측하지 않고 None**.
chk("어휘 미주입이면 facility_type 은 None (사전 하드코딩 제거)",
    _md("흡연부스 설치 건")["facility_type"] is None,
    str(_md("흡연부스 설치 건")["facility_type"]))
chk("주입한 어휘로만 판정한다",
    pdf_parser.parse_document_metadata(
        "재활용정거장 설치 건", {"재활용정거장": ["재활용정거장"]}
    )["facility_type"] == "재활용정거장")
chk("어휘에 없으면 None (없는 시설을 지어내지 않는다)",
    pdf_parser.parse_document_metadata(
        "흡연부스 설치 건", {"재활용정거장": ["재활용정거장"]}
    )["facility_type"] is None)

print("--- 4) 실 DB — 대조에 쓸 시뮬레이션 하나 찾기")


async def _pick():
    """🔴 `asyncio.run` 이 루프를 닫는데 커넥션 풀은 모듈 전역이라, 안 버리면
       **다음 TestClient 루프**에서 `Event loop is closed` 로 터진다."""
    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(HearingResultA).order_by(HearingResultA.id.desc())
                )
            ).scalars().first()
            return (
                (row.id, row.result_json, row.facility_type)
                if row
                else (None, None, None)
            )
    finally:
        await engine.dispose()


sim_id, result_json, sim_facility = asyncio.run(_pick())
chk("hearing_result_a 에 행이 있다", sim_id is not None, f"id={sim_id}")
if sim_id is None:
    print("\n대조할 시뮬레이션이 없다 — 화면5 를 한 번 돌린 뒤 다시 실행할 것.")
    sys.exit(1)

scen = (result_json or {}).get("scenarios") or []
chk("result_json 에 scenarios 가 있다", bool(scen), f"{len(scen)}건")
if scen:
    codes = [scenario_code(s) for s in scen]
    chk("저장된 시나리오에서 코드가 뽑힌다", all(c in ("A", "B", "C") for c in codes),
        str(codes))


def _one_page_pdf(lines, fontname="helv") -> bytes:
    """텍스트 레이어가 있는 1쪽 PDF. 스캔본이 아니라 실제 글자다.

    🔴 한글은 `helv` 로 넣으면 글자가 **빠진다**(예외 없이). PyMuPDF 내장 CJK
       폰트 `korea` 를 써야 추출했을 때 원문이 그대로 돌아온다.
    """
    if isinstance(lines, str):
        lines = [lines]
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + i * 18), line, fontname=fontname, fontsize=11)
    return doc.tobytes()


# 실제 행정 공문의 모양. 🔴 여기 값들이 `/verify` 의 `parsed_metadata` 로 되돌아
# 나와야 한다 — 하나라도 null 이면 그 칸은 `/save` 까지 빈 채로 간다.
KO_DOC = [
    "용산구청 도시계획과-12345",
    "수신: 관계 부서장",
    "제목: 흡연부스 설치 건 행정 종결 통보",
    "설치 예정지: 용산구 이태원동 123-45",
    "2026. 8. 11.",
    "버스정류소 반경 10m 이내 금연구역에 해당하여 설치 불가로 종결한다.",
    "주민 반대가 지속되어 입지 재검토가 필요하다.",
]


saved_id = None
with TestClient(app) as c:
    print("--- 5) /audit/verify")
    r = c.post(
        "/api/v1/audit/verify",
        files={"file": ("x.txt", b"hello", "text/plain")},
        data={"simulation_id": sim_id},
    )
    chk("PDF 아니면 400", r.status_code == 400, f"-> {r.status_code}")

    pdf = _one_page_pdf("Notice of completion. Document No. 2026-1234.")
    r = c.post(
        "/api/v1/audit/verify",
        files={"file": ("done.pdf", pdf, "application/pdf")},
        data={"simulation_id": 999999},
    )
    # 🔴 예전엔 여기가 **200 + UNCLASSIFIED** 였다 — 대조를 한 것처럼 보인다.
    chk("없는 simulation_id 404", r.status_code == 404, f"-> {r.status_code}")

    r = c.post(
        "/api/v1/audit/verify",
        files={"file": ("done.pdf", pdf, "application/pdf")},
        data={"simulation_id": sim_id},
    )
    chk("정상 200", r.status_code == 200, f"-> {r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        body = r.json()
        chk("OCR 성공", body["ocr_success"] is True)
        chk("본문 조각이 실제 글자", "completion" in body["extracted_text_snippet"],
            body["extracted_text_snippet"][:50])
        chk("matched_scenario 가 A 로 굳지 않는다",
            body["matched_scenario"] in (None, "A", "B", "C"),
            f'{body["matched_scenario"]} / {body["classification_status"]}')

    print("--- 5-2) /verify — 🔴 한글 공문 실물 (여기가 진짜 쓰임새다)")
    r = c.post(
        "/api/v1/audit/verify",
        files={"file": ("종결.pdf", _one_page_pdf(KO_DOC, "korea"), "application/pdf")},
        data={"simulation_id": sim_id},
    )
    chk("한글 PDF 200", r.status_code == 200, f"-> {r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        body = r.json()
        md = body["parsed_metadata"]
        chk("한글 텍스트 레이어가 살아 있다", "용산구청" in body["extracted_text_snippet"],
            body["extracted_text_snippet"][:40])
        chk("🔴 document_no 가 null 이 아니다", md["document_no"] == "도시계획과-12345",
            str(md["document_no"]))
        chk("🔴 parsed_jibun 이 null 이 아니다", md["parsed_jibun"] == "용산구 이태원동 123-45",
            str(md["parsed_jibun"]))
        chk("parsed_date", md["parsed_date"] == "2026-8-11", str(md["parsed_date"]))
        # 🔴 기대값을 글자로 박지 않는다 — 이 시뮬레이션의 시설이 어휘의 출처다.
        #    공문 본문에 그 시설명이 있으면 그 값, 없으면 None 이 정답이다.
        _want = sim_facility if (sim_facility and sim_facility in "\n".join(KO_DOC)) else None
        chk(f"facility_type 은 시뮬레이션 시설에서 온다 (기대: {_want})",
            md["facility_type"] == _want, str(md["facility_type"]))
        # 저장된 시나리오 문구와 실제로 겹쳐야 대조를 한 것이다.
        chk("판정이 났다 (미분류가 아니다)",
            body["classification_status"] in ("COMPLIANT", "WARNING", "DEVIATED")
            and body["matched_scenario"] in ("A", "B", "C"),
            f'{body["matched_scenario"]} / {body["classification_status"]} '
            f'/ {body["similarity_score"]}')

    print("--- 6) /audit/save — 미분류는 안 넣는다")
    # `/verify` 가 미분류면 `matched_scenario: null` 을 준다 → 그대로 전달하면
    # FastAPI 가 **핸들러 앞에서** 필수 누락으로 막는다(빈 문자열도 같다).
    r = c.post("/api/v1/audit/save", data={
        "simulation_id": sim_id, "matched_scenario": "", "similarity_score": 0.0,
        "classification_status": "UNCLASSIFIED", "extracted_text": "x"})
    chk("빈 matched_scenario 는 422 (필수 필드)", r.status_code == 422, f"-> {r.status_code}")
    # 🔴 여기가 우리 방어선이다 — 코드는 실려 있는데 판정이 미분류인 경우.
    r = c.post("/api/v1/audit/save", data={
        "simulation_id": sim_id, "matched_scenario": "A", "similarity_score": 0.0,
        "classification_status": "UNCLASSIFIED", "extracted_text": "x"})
    chk("UNCLASSIFIED 400", r.status_code == 400, f"-> {r.status_code}")
    chk("400 이 이유를 말한다", "실증 사례" in r.text, r.text[:80] if r.status_code == 400 else "")
    r = c.post("/api/v1/audit/save", data={
        "simulation_id": sim_id, "matched_scenario": "A", "similarity_score": 0.0,
        "classification_status": "NO_PREDICTION", "extracted_text": "x"})
    chk("NO_PREDICTION 400", r.status_code == 400, f"-> {r.status_code}")
    r = c.post("/api/v1/audit/save", data={
        "simulation_id": sim_id, "matched_scenario": "   ", "similarity_score": 0.0,
        "classification_status": "DEVIATED", "extracted_text": "x"})
    chk("공백만 있는 코드도 400 (422 를 통과한다)", r.status_code == 400, f"-> {r.status_code}")

    print("--- 7) /audit/save — 🔴 실제로 넣는다 (2026-08-09 컬럼 정정 이후 첫 실행)")
    r = c.post("/api/v1/audit/save", data={
        "simulation_id": sim_id, "matched_scenario": "C", "similarity_score": 0.421,
        "classification_status": "DEVIATED", "extracted_text": "대조기 임시행",
        "document_no": "CHK-AUDIT-TMP"})
    chk("저장 200", r.status_code == 200, f"-> {r.status_code} {r.text[:150]}")
    if r.status_code == 200:
        saved_id = r.json()["audit_id"]
        chk("격리 적재 표시", r.json()["is_feedback_loop_isolated"] is True)
        chk("saved_at 이 있다", bool(r.json().get("saved_at")), str(r.json().get("saved_at")))
        print(f"      넣은 행 id={saved_id} (아래에서 지운다)")

print("--- 8) 임시행 정리")


async def _cleanup(pk):
    try:
        async with AsyncSessionLocal() as db:
            got = (
                await db.execute(select(VerifiedPrecedent).where(VerifiedPrecedent.id == pk))
            ).scalars().first()
            found = got is not None and got.actual_scenario == "C"
            await db.execute(delete(VerifiedPrecedent).where(VerifiedPrecedent.id == pk))
            await db.commit()
            left = (
                await db.execute(select(VerifiedPrecedent).where(VerifiedPrecedent.id == pk))
            ).scalars().first()
            return found, left is None
    finally:
        await engine.dispose()


if saved_id is not None:
    found, gone = asyncio.run(_cleanup(saved_id))
    chk("넣은 값이 실제로 읽힌다", found, f"id={saved_id}")
    chk("임시행 지워짐", gone, f"id={saved_id}")
else:
    chk("임시행 정리", False, "저장이 실패해서 지울 것도 없다")

print(f"\n{ok + fail}항목 중 {ok} 통과 · {fail} 실패")
sys.exit(1 if fail else 0)
