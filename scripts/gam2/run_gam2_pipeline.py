# -*- coding: utf-8 -*-
"""
OmniSite 감리 파이프라인 러너 (STEP 0 ~ 2)
==========================================
profile → real(감리 판정) → search(배제반경 상위법 검색) 을 한 번에 실행하고
각 단계 소요 시간을 측정한다.

  python run_pipeline.py <도메인폴더> "<사용자 입력>"
  예) python run_pipeline.py 흡연 "용산구 흡연부스 부지 선정"

옵션
  --skip-search   real 까지만 (검색 생략)
  --mock          LLM 호출 없이 MockLLM 으로 (키 불필요, 형식·시간 확인용)

※ HITL(hitl 모드)은 대화형이라 파이프라인에 포함하지 않는다. 이후 따로 실행:
     python audit_judgment_test.py hitl <도메인폴더>

기존 파일(audit_judgment_test.py)은 수정하지 않고 함수만 가져다 쓴다.
"""

from __future__ import annotations

import sys
import time
import traceback

import scripts.gam2.run_audit_judgment_test as A

# tqdm 은 선택 의존 — 없으면 간단한 텍스트 진행 표시로 폴백(파이프라인은 그대로 동작).
try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:  # pip install tqdm
    HAS_TQDM = False


class _Bar:
    """tqdm 있으면 진행바, 없으면 '(3/11) 07' 텍스트 폴백. with 문으로 사용."""

    def __init__(self, total: int, desc: str):
        self.total, self.desc, self.n = total, desc, 0
        self.bar = (
            tqdm(
                total=total,
                desc=desc,
                unit="개",
                bar_format="  {desc} |{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}",
            )
            if HAS_TQDM
            else None
        )

    def update(self, label: str = "") -> None:
        self.n += 1
        if self.bar:
            self.bar.set_postfix_str(label, refresh=False)
            self.bar.update(1)
        else:
            print(f"  ({self.n}/{self.total}) {label}", flush=True)

    def __enter__(self):
        if not self.bar:
            print(f"  {self.desc} — {self.total}건 처리 중...")
        return self

    def __exit__(self, *exc):
        if self.bar:
            self.bar.close()


# ══════════════════════════════════════════════════════════════════
# 시간 측정 유틸
# ══════════════════════════════════════════════════════════════════


class Timer:
    """단계별 소요 시간 기록 → 마지막에 표로 출력."""

    def __init__(self):
        self.laps: list[tuple[str, float, str]] = []  # (단계명, 초, 상태)
        self.t0 = time.perf_counter()

    def lap(self, name: str, seconds: float, status: str = "완료") -> None:
        self.laps.append((name, seconds, status))

    @property
    def total(self) -> float:
        return time.perf_counter() - self.t0

    def report(self) -> None:
        total = self.total
        print("\n" + "=" * 60)
        print("[소요 시간]")
        print("-" * 60)
        for name, sec, status in self.laps:
            pct = (sec / total * 100) if total else 0
            bar = "█" * max(1, int(pct / 4)) if status == "완료" else ""
            print(
                f"  {name:22} {sec:7.2f}s  {pct:5.1f}%  {bar} {'' if status == '완료' else '(' + status + ')'}"
            )
        print("-" * 60)
        print(f"  {'합계':22} {total:7.2f}s")
        print("=" * 60)


def _step(timer: Timer, name: str, fn, *args, **kwargs):
    """단계 실행 + 시간 측정. 예외는 잡아서 기록하고 다시 올린다."""
    print(f"\n{'━' * 60}\n▶ {name}\n{'━' * 60}")
    t = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception:
        timer.lap(name, time.perf_counter() - t, status="실패")
        raise
    timer.lap(name, time.perf_counter() - t)
    return result


# ══════════════════════════════════════════════════════════════════
# 파이프라인
# ══════════════════════════════════════════════════════════════════


def run(
    domain_dir: str, user_input: str, skip_search: bool = False, mock: bool = False
) -> dict:
    """profile(자동) → real → search. 반환: {산출물 경로들}"""
    timer = Timer()
    paths: dict[str, str] = {}

    # ── STEP 0. 도메인 설정 (경로·프리픽스 확정)
    A.set_domain(domain_dir)
    print(f"[도메인] {domain_dir}  (프리픽스: {A._DOMAIN['prefix']})")
    print(
        f'[입력]   "{user_input}"' + ("   ※ MOCK 모드(LLM 호출 없음)" if mock else "")
    )

    # ── STEP 0-1. fixture 확보 (없으면 build_fixtures 가 자동 프로파일링)
    fixtures = _step(timer, "STEP 0  프로파일/조례", A.build_fixtures)
    print(f"[fixture] {len(fixtures)}개 데이터셋")

    # ── STEP 0-2. 시설·지역 확정 (mini)
    def _facility():
        fn = A.resolve_facility_mock if mock else A.resolve_facility
        return fn(user_input, fixtures)

    fac = _step(timer, "STEP 0.5 시설·지역 확정", _facility)
    print(f"[시설 확정] '{fac['facility']}' / 지역 '{fac.get('region', '')}'")
    if fac.get("근거"):
        print(f"  근거: {fac['근거']}")
    if fac.get("mismatch"):
        print(f"  ⚠ 입력↔데이터 불일치: {fac.get('mismatch_reason', '')}")
    print("  ※ 확정 아님 — HITL에서 확인/수정")

    domain = {
        "facility": fac["facility"],
        "region": fac.get("region") or A.DOMAIN["region"],
    }

    # ── STEP 1. 감리 판정 (gpt-4o)
    from app.config import AUDIT_LLM_MODEL

    model = "mock" if mock else AUDIT_LLM_MODEL
    llm = A.MockLLM() if mock else A.RealLLM()

    def _audit():
        with _Bar(len(fixtures), "감리 판정") as bar:
            return A.run_harness(
                llm, fixtures, domain, progress=lambda did: bar.update(f"#{did}")
            )

    judgments, raw_preds = _step(timer, f"STEP 1  감리 판정 ({model})", _audit)
    A.report(judgments, raw_preds)
    paths["audit"] = A.save_results(judgments, raw_preds, model, facility_info=fac)
    print(f"\n[저장] {paths['audit']}")

    # ── STEP 2. 배제반경 상위법 검색 (mini)
    n_missing = sum(
        1
        for p in raw_preds.values()
        for f in p.get("hitl_flags", [])
        if f.get("type") == "exclusion_radius_missing"
    )

    if skip_search:
        timer.lap("STEP 2  배제반경 검색", 0.0, status="생략")
    elif mock:
        print("\n[STEP 2 생략] mock 모드 — 실제 검색은 real 에서만 수행")
        timer.lap("STEP 2  배제반경 검색", 0.0, status="mock 생략")
    elif n_missing == 0:
        print("\n[STEP 2 생략] 미확정 배제반경 0건 — 검색할 대상 없음")
        timer.lap("STEP 2  배제반경 검색", 0.0, status="대상 없음")
    else:
        print(f"\n(미확정 배제반경 {n_missing}건 → 상위법 검색)")

        def _search():
            return A.enrich_with_search(region=domain["region"])

        paths["enriched"] = _step(timer, "STEP 2  배제반경 검색", _search)

    # ── 결과 요약
    timer.report()

    print("\n[산출물]")
    for k, v in paths.items():
        print(f"  {k:9} {v}")
    if not skip_search and not mock:
        print(f"\n다음 단계(대화형): python audit_judgment_test.py hitl {domain_dir}")
    return paths


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

USAGE = """사용법:
  python run_pipeline.py <도메인폴더> "<사용자 입력>" [--skip-search] [--mock]

예)
  python run_pipeline.py 흡연 "용산구 흡연부스 부지 선정"
  python run_pipeline.py EV_데이터셋 "강남구 EV 충전소 선정" --skip-search
  python run_pipeline.py 흡연 "용산구 흡연부스 선정" --mock      (키 불필요)

※ HITL 은 대화형이라 별도 실행: python audit_judgment_test.py hitl <도메인폴더>"""

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if len(args) < 2:
        print(USAGE)
        sys.exit(1)

    try:
        run(
            args[0],
            args[1],
            skip_search="--skip-search" in flags,
            mock="--mock" in flags,
        )
    except FileNotFoundError as e:
        print(f"\n[중단] {e}")
        sys.exit(1)
    except Exception:
        print("\n[중단] 파이프라인 오류:")
        traceback.print_exc()
        sys.exit(1)
