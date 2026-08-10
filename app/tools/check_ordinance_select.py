# -*- coding: utf-8 -*-
"""
조례 조문 선별 결과 확인 (일회성 진단)
======================================
  python app/tools/check_ordinance_select.py 재활용
  python app/tools/check_ordinance_select.py 흡연 --show 5     (선별된 조문 본문 5개 미리보기)

감리 프롬프트에 실제로 들어가는 조례 발췌가 **맞게 골라졌는지** 눈으로 본다.

🔴 보는 법
  · 배제·이격·금지 조문에 O      → 정상
  · 수수료·과태료·위원회에 O     → 과대 선별 (신호 조정 검토)
  · **배제 관련 조문이 '-'**     → 누락. 배제 판정 근거가 사라진다
"""
from __future__ import annotations

import argparse
import os
import sys

# 이 스크립트는 `app/tools/` 안에 있다 — 저장소 루트는 두 단계 위다.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.services.gam2_ordinance_select import (
    split_articles, is_regulatory, select_articles, keywords_of)
from app.services.gam2_audit_judgment_test import load_ordinance, set_domain


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--show", type=int, default=0, help="선별 조문 본문 미리보기 개수")
    args = ap.parse_args()

    set_domain(args.domain)
    text = load_ordinance()
    if not text:
        print(f"[중단] 조례 없음 — data_임시/{args.domain}/law/ 확인")
        return

    arts = split_articles(text)
    picked = select_articles(text, [], verbose=False)

    print("=" * 84)
    print(f"[조례 조문 선별] {args.domain}  —  전문 {len(text):,}자 · 조문 {len(arts)}개")
    print("=" * 84)
    print(f"{'조문':<11}{'제목':<30}{'길이':>7}  판정")
    print("-" * 84)

    n_keep = 0
    for a in arts:
        if a["no"] is None:
            print(f"{'(서두)':<11}{a['title'][:28]:<30}{len(a['text']):>6}자  O 항상")
            n_keep += 1
            continue
        ok, why = is_regulatory(a)
        # 참조로 끌려온 조문도 발췌본에 들어간다 — 실제 포함 여부로 표시
        inc = a["text"][:40] in picked
        mark = f"O {why}" if ok else ("O 참조" if inc else "-")
        n_keep += bool(inc)
        print(f"{a['no']:<11}{a['title'][:28]:<30}{len(a['text']):>6}자  {mark}")

    print("-" * 84)
    print(f"  선별 {n_keep}/{len(arts)}개 · {len(text):,}자 → {len(picked):,}자 "
          f"({len(picked)/max(len(text),1):.0%})")

    # 누락 위험 진단 — 배제 관련 단어가 있는데 안 뽑힌 조문
    RISK = ("이격", "거리", "미터", "설치", "금지", "배제", "제한", "구역")
    missed = [a for a in arts
              if a["no"] and a["text"][:40] not in picked
              and any(k in a["text"] for k in RISK)]
    if missed:
        print(f"\n  ⚠ 규제 관련 단어가 있으나 제외된 조문 {len(missed)}개 — 확인 필요")
        for a in missed:
            hit = [k for k in RISK if k in a["text"]]
            print(f"     {a['no']} {a['title'][:24]:<26} 포함어: {hit}")
    else:
        print("\n  ✅ 규제 관련 단어가 있는 조문은 모두 포함됐습니다.")

    if args.show:
        print("\n" + "=" * 84)
        print(f"[선별 조문 본문 미리보기 — 최대 {args.show}개]")
        print("=" * 84)
        shown = 0
        for a in arts:
            if a["no"] is None or a["text"][:40] not in picked:
                continue
            print(f"\n── {a['no']} ({a['title']}) ──")
            print(a["text"][:700] + ("…" if len(a["text"]) > 700 else ""))
            shown += 1
            if shown >= args.show:
                break

    print("\n" + "=" * 84)


if __name__ == "__main__":
    main()
