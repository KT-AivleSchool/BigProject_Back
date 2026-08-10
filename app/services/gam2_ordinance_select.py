# -*- coding: utf-8 -*-
"""
OmniSite 조례 조문 분할·선별
=============================
조례 전문을 조(條) 단위로 쪼개고, **용도에 맞는 조문만** 골라 쓴다.

왜 필요한가
  기존에는 조례 전문을 데이터셋 프로파일 전부에 주입했다(`p["ordinance"] = ordinance`).
  데이터셋이 9개면 같은 조례가 9번 프롬프트에 실린다.
  성동구 폐기물 조례(전문)로 바꾸자 감리가 66초 → 4분 37초(4.2배)가 됐다.

  게다가 대부분은 쓸모가 없다 — '생활인구 통계'를 판정하는 데
  과태료 조항·금연지도원 조항이 필요하지 않다.

무엇을 남기는가
  ① 규제성 조문   설치·금지·제한·이격·구역 지정 등 (배제 판정의 근거)
  ② 관련 조문     그 데이터셋의 키워드가 등장하는 조문
  ③ 참조된 조문   ①②가 「제5조제1항제2호」처럼 가리키는 조문 (단서 해석에 필수)

  ③이 핵심이다. 제8조 단서가 "제5조제1항제2호부터 제4호까지" 라고만 쓰므로
  제5조를 같이 넣지 않으면 LLM 이 무엇이 금지되는지 알 수 없다.

⚠️ 배제반경·설치가부 추출(STEP2)에는 **전문을 쓴다.**
   거기서는 조문 하나가 빠지면 근거가 사라지고, 호출 횟수도 배제 건수뿐이라 싸다.
   선별은 **감리 프롬프트(데이터셋 수만큼 반복)** 에만 적용한다.
"""
from __future__ import annotations

import re

# 조문 헤더: 제5조(금연구역의 지정) / 제9조의2(금연지도원 제도 운영)
_ART = re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?)\s*\(([^)]{0,60})\)")

# 조문 간 참조: 제5조제1항제2호 / 제5조 제1항 / 제8조의2
_REF = re.compile(r"제\s*(\d+)\s*조(?:의\s*(\d+))?")

# 규제성 조문 판정 — **구조 신호**로 본다. 도메인 단어를 넣으면 엔진이 도메인에 묶인다.
#
#   키워드만으로는 안 걸러진다(실측): '설치'·'지정'·'하여야 한다' 는
#   표지판 설치·교육 홍보·과태료 조문에도 다 나와 전문의 86% 가 남았다.
#   조문을 규제로 만드는 것은 단어가 아니라 **거리·금지·열거** 세 구조다.
DIST = ("미터", "ｍ", "m 이내", "이격", "반경", "직선거리")
PROHIBIT = ("할 수 없다", "아니 된다", "금지한다", "설치할 수 없다",
            "설치·운영할 수 없다", "제한한다", "배제")
_ENUM = re.compile(r"^\s*\d+\.\s", re.M)      # 각 호 열거 (1. 2. 3. …)
ENUM_MIN = 3                                    # 3개 이상이면 대상 목록으로 본다


def split_articles(text: str) -> list[dict]:
    """조례 전문 -> [{"no": "제5조", "num": 5, "sub": 0, "title": "...", "text": "..."}]

    헤더 앞의 서두(제명·시행일)는 no=None 으로 맨 앞에 둔다.
    """
    if not text:
        return []
    out, marks = [], list(_ART.finditer(text))
    if not marks:
        return [{"no": None, "num": 0, "sub": 0, "title": "", "text": text.strip()}]

    head = text[: marks[0].start()].strip()
    if head:
        out.append({"no": None, "num": 0, "sub": 0, "title": "(서두)", "text": head})

    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        no = re.sub(r"\s+", "", m.group(1))
        n = int(re.search(r"\d+", no).group())
        sub = re.search(r"의(\d+)", no)
        body = text[m.start():end].strip()
        # 같은 조문이 두 번 나오면(PDF 머리말 반복 등) 긴 쪽을 남긴다
        prev = next((o for o in out if o["no"] == no), None)
        if prev:
            if len(body) > len(prev["text"]):
                prev["text"] = body
            continue
        out.append({"no": no, "num": n, "sub": int(sub.group(1)) if sub else 0,
                    "title": m.group(2).strip(), "text": body})
    return out


def _refs_in(body: str) -> set[tuple]:
    """본문이 가리키는 다른 조문 번호 집합. (조번호, 의번호)"""
    return {(int(a), int(b) if b else 0) for a, b in _REF.findall(body)}


def is_regulatory(art: dict) -> tuple:
    """규제성 조문인가. 반환 (bool, 사유).

    셋 중 하나면 규제 조문으로 본다.
      · 거리 규정   '30미터 이내'
      · 금지 규정   '설치·운영할 수 없다'
      · 대상 열거   각 호 3개 이상 (금지구역·대상시설 목록)
    """
    body = art.get("text", "")
    if any(k in body for k in DIST):
        return True, "거리"
    if any(k in body for k in PROHIBIT):
        return True, "금지"
    n = len(_ENUM.findall(body))
    if n >= ENUM_MIN:
        return True, f"열거{n}"
    return False, ""


def has_siting_provision(text: str) -> tuple[bool, list]:
    """조례 전문에 **입지 규정**(이격거리·설치금지)이 있는가. 반환 (있음, 매칭신호).

    LLM 0회 · 정규식조차 아닌 부분문자열 검사다. 비용 0.

    왜 필요한가
      기존 게이트는 *"조례가 있는가"*(`extract_cited_laws` 가 비었는가)를 물었다.
      성동구 폐기물 조례는 **있고 상위법을 11개나 인용**하는데 이격 규정만 없다.
      게이트가 안 걸려 11개 × 배제 3건 검색으로 들어갔고 크레딧이 소진됐다.

      물어야 할 것은 *"조례에 이격 규정이 있는가"* 다.

    실측 (2026-08-03)
      흡연    4,221자   DIST['미터'] · PROHIBIT 3종  → True
      재활용 17,264자   신호 0건                     → False
      재활용이 4배 길다. 전문 길이·조문 수와 무관하게 갈린다.

    열거(각 호)는 신호로 쓰지 않는다 — 재활용은 열거로 16개 조문이 뽑혔지만
    거리 규정은 0건이었다. 이격 근거로는 약하다.

    ⚠️ False 는 **"조례에 없다"** 까지만 뜻한다. 상위법에는 있을 수 있다
       (흡연의 학교 200m 는 조례가 아니라 교육환경법 계열이다).
       호출부가 이걸 근거로 상위법 검색을 생략한다면 **생략했다는 사실을
       산출물에 남겨야 한다** — 안 그러면 "규정 없음"을 확정했다고 거짓말하게 된다.
    """
    if not text:
        return False, []
    hits = [k for k in DIST if k in text] + [k for k in PROHIBIT if k in text]
    return bool(hits), hits


def select_articles(text: str, keywords=(), max_chars: int = 6000,
                    verbose: bool = False) -> str:
    """감리 프롬프트용 조례 발췌.

    keywords : 이 데이터셋을 나타내는 말(파일명·컬럼명에서 추출). 매칭 조문을 포함한다.
    max_chars: 상한. 넘으면 조문 번호 순으로 자르고 잘렸음을 표시한다.

    선별 결과가 없으면 **전문을 그대로 반환한다** — 조례가 짧거나 형식이
    예상과 다를 때 빈 문자열을 주면 감리가 근거 없이 판정하게 된다.
    """
    arts = split_articles(text)
    if len(arts) <= 1:
        return text

    kws = [str(k).strip() for k in keywords if str(k).strip()]
    keep: set[str] = set()
    for a in arts:
        if a["no"] is None:                       # 서두(제명·시행일)는 항상
            keep.add("__head__")
            continue
        if is_regulatory(a)[0]:
            keep.add(a["no"])
        elif kws and any(k in a["text"] for k in kws):
            keep.add(a["no"])

    # 참조 확장 — 선택된 조문이 가리키는 조문을 끌어온다(단서 해석용)
    by_key = {(a["num"], a["sub"]): a for a in arts if a["no"]}
    for _ in range(2):                            # 2단계까지(참조의 참조)
        add = set()
        for a in arts:
            if a["no"] not in keep:
                continue
            for r in _refs_in(a["text"]):
                t = by_key.get(r)
                if t and t["no"] not in keep:
                    add.add(t["no"])
        if not add:
            break
        keep |= add

    sel = [a for a in arts
           if (a["no"] is None and "__head__" in keep) or a["no"] in keep]
    if not sel:
        return text

    parts, total, cut = [], 0, False
    for a in sel:
        if total + len(a["text"]) > max_chars:
            cut = True
            break
        parts.append(a["text"])
        total += len(a["text"])
    if cut:
        parts.append(f"…(이하 생략 — 전문 {len(text):,}자 중 {total:,}자 발췌)")

    out = "\n\n".join(parts)
    if verbose:
        print(f"  ⓘ 조례 발췌: 조문 {len(arts)}개 중 {len(sel)}개 · "
              f"{len(text):,}자 → {len(out):,}자 ({len(out)/max(len(text),1):.0%})")
    return out


def keywords_of(profile: dict) -> list[str]:
    """데이터셋 프로파일에서 조문 매칭용 키워드 추출.

    파일명·컬럼명의 한글 명사 조각을 쓴다. 도메인 사전을 두지 않는다 —
    '어린이집 현황.csv' 면 '어린이집' 이 그대로 조문 매칭 키가 된다.
    """
    src = [str(profile.get("filename", ""))]
    cols = profile.get("columns") or []
    src += [str(c.get("name", c) if isinstance(c, dict) else c) for c in cols]
    out, seen = [], set()
    for s in src:
        for tok in re.findall(r"[가-힣]{2,}", s):
            if tok in seen or len(tok) < 2:
                continue
            seen.add(tok)
            out.append(tok)
    return out[:40]
