# -*- coding: utf-8 -*-
r"""CLI — `python -m app.services.gam2_audit_judgment_test <모드> <도메인폴더>`

🔴 예전엔 `python app\services\gam2_audit_judgment_test.py` 였다. 패키지가 되면서
   그 형태는 못 쓴다(패키지는 스크립트로 못 돈다). 러너는 이 모듈을 subprocess 로
   부르지 않으므로(`pipeline_runner/commands.py` 참조) 바뀌는 것은 **사람 경로뿐**이다.
"""
from __future__ import annotations

from .exclusions import enrich_with_search
from .fixtures import build_fixtures, require_region
from .harness import run_harness
from .hitl import review_hitl
from .llm import MockLLM, RealLLM
from .outputs import report, save_results
from .prompts import resolve_facility, resolve_facility_mock
from .state import _DOMAIN, set_domain

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]

    _M = "python -m app.services.gam2_audit_judgment_test"
    USAGE = (
        "사용법:\n"
        f'  {_M} real "<입력>" <도메인폴더>\n'
        f'  {_M} "<입력>" <도메인폴더>          (mock)\n'
        f"  {_M} search <도메인폴더>\n"
        f"  {_M} hitl <도메인폴더>\n"
        "  ※ 먼저 python app\\services\\gam2_profile.py <도메인폴더> 로 fixture 생성 필요."
    )
    if not args:
        print(USAGE)
        sys.exit(1)

    # 도메인 폴더는 항상 마지막 위치 인자. set_domain 으로 경로·프리픽스 확정.
    mode = args[0] if args[0] in ("real", "search", "hitl") else "mock"
    domain_dir = args[-1]
    if (mode == "mock" and len(args) < 2) or (mode != "mock" and len(args) < 2):
        print(USAGE)
        sys.exit(1)
    set_domain(domain_dir)
    print(f"[도메인] {domain_dir}  (프리픽스: {_DOMAIN['prefix']})")

    if mode == "hitl":
        review_hitl()
    elif mode == "search":
        print("[배제반경 서핑] 조례에 반경 없는 배제 대상만 web_search 로 후보 제시")
        print("※ 제안값은 확정 아님 — 반드시 사람이 출처 확인 후 확정하세요.\n")
        enrich_with_search()
    elif mode == "real":
        # python -m app.services.gam2_audit_judgment_test real "강남구 EV 충전소 선정" EV_데이터셋
        from app.config import AUDIT_LLM_MODEL, FACILITY_LLM_MODEL

        user_input = args[1] if len(args) > 2 else ""
        fixtures = build_fixtures()  # fixture/profiles.json 로드(+조례 주입)
        print(f"[fixture] {_DOMAIN['profiles']} → {len(fixtures)}개 프로파일\n")
        fac = resolve_facility(user_input, fixtures)
        print(
            f"[시설 확정] '{fac['facility']}' / 지역 '{fac.get('region', '')}' (모델: {FACILITY_LLM_MODEL})"
        )
        print(f"  근거: {fac['근거']}")
        if fac.get("mismatch"):
            print(f"  ⚠ 입력↔데이터 불일치: {fac['mismatch_reason']}")
        print("  ※ 확정 아님 — HITL에서 확인/수정 필요\n")
        domain = {
            "facility": fac["facility"],
            "region": require_region(fac),
        }
        print(f"[감리 AI 검수 리포트] 모델: {AUDIT_LLM_MODEL}")
        print("※ 배제반경 미확정·애매 데이터는 아래 HITL 대기로 넘어갑니다.\n")
        judgments, raw_preds = run_harness(RealLLM(), fixtures, domain)
        report(judgments, raw_preds)
        path = save_results(judgments, raw_preds, AUDIT_LLM_MODEL, facility_info=fac)
        print(f"\n[저장] {path}")
    else:
        # mock: python -m app.services.gam2_audit_judgment_test "강남구 EV 충전소 선정" EV_데이터셋
        user_input = args[0] if len(args) > 1 else "부지 선정해줘"
        fixtures = build_fixtures()
        print(f"[fixture] {_DOMAIN['profiles']} → {len(fixtures)}개 프로파일")
        fac = resolve_facility_mock(user_input, fixtures)
        print(f"[시설 확정(mock)] '{fac['facility']}'  (입력: {user_input})\n")
        domain = {
            "facility": fac["facility"],
            "region": require_region(fac),
        }
        print("[MockLLM 검수 리포트] — 하네스 출력 형식 확인용\n")
        judgments, raw_preds = run_harness(MockLLM(), fixtures, domain)
        report(judgments, raw_preds)
        path = save_results(judgments, raw_preds, "mock", facility_info=fac)
        print(f"\n[저장] {path}")
