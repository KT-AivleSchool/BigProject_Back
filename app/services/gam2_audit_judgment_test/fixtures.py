# -*- coding: utf-8 -*-
"""§4 조례 로드 · 프로파일(픽스처) 생성 · 지역 요구.

🔴 `fixture/profiles.json` 은 `data/` 의 **사본**이지 독립 입력이 아니다.
   「없으면 만든다」가 곧 「있으면 안 본다」라서, 원본이 바뀔 수 있는 경로(`full`)에서는
   `force_profile=True` 로 **무조건 다시 만든다** — 안 그러면 감리 AI 가 새로 올린
   데이터셋을 못 본다(2026-08-12 재활용 실측).
"""
from __future__ import annotations

import json
import os

from .state import _DOMAIN

# ══════════════════════════════════════════════════════════════════
# 4. 데이터셋 프로파일 — profile.py 로 폴더 파일을 읽어 생성
# ══════════════════════════════════════════════════════════════════
# build_fixtures(폴더) = profile_folder() 출력 + 조례(a안: 전 데이터셋 주입).
# null_coords/has_addr_col/sample_rows/조례가 감리 판정의 근거.


# 조례 로드 — 소스 추상화. 지금은 업로드된 파일이지만, 나중에 DB/프론트 전달값으로
# 바꿔도 이 함수 내부만 교체하면 됨(호출부 불변).
def load_ordinance(source: str | None = None) -> str:
    """조례 텍스트를 로드. source 우선순위:
      1) source 가 조례 텍스트 자체(개행 포함 긴 문자열)면 그대로 사용 (프론트/DB 직접 전달)
      2) source 가 폴더 경로면 그 폴더의 모든 텍스트 파일을 읽어 합침
      3) None 이면 config 의 ORDINANCE_DIR(기본 ./law) 폴더 전체
    ── 추후 DB/프론트 전환 시 이 함수만 교체(예: return db.fetch_ordinances(region, facility)).
    법령 폴더에 조례+시행규칙 등 여러 파일을 넣으면 모두 합쳐 ordinance_rag 로 쓴다.
    """
    import os
    import glob
    from app.config import ORDINANCE_DIR

    # 1) 텍스트 직접 전달(프론트/DB)
    if source and ("\n" in source) and not os.path.exists(source):
        return source
    # 2/3) 폴더에서 텍스트 파일 수집 (기본: 현재 도메인의 law/, 없으면 config 기본)
    folder = source or _DOMAIN["law"] or ORDINANCE_DIR
    if not os.path.isdir(folder):
        return ""
    # PDF·DOCX·HWPX 만 있어도 읽히도록 먼저 텍스트로 변환(옆에 .txt 캐시).
    #   추출은 부가 기능이라 의존 패키지가 없으면 건너뛰고 진행한다.
    try:
        from app.services.gam2_doc_extract import ensure_text_files

        ensure_text_files(folder)
    except Exception as e:
        print(f"  ⚠ 문서 텍스트 추출 생략({e})")
    parts = []
    for path in sorted(
        glob.glob(os.path.join(folder, "*.txt"))
        + glob.glob(os.path.join(folder, "*.md"))
    ):
        try:
            with open(path, encoding="utf-8") as f:
                parts.append(f"[{os.path.basename(path)}]\n" + f.read())
        except OSError:
            continue
    return "\n\n".join(parts)


def build_fixtures(profiles_path: str | None = None,
                   force_profile: bool = False) -> dict:
    """fixture/profiles.json 로드 → 조례 (a)안 전 데이터셋 주입.
    profiles.json 이 없으면 profile.py 로 자동 생성한다(data/ 프로파일링).

    🔴 `force_profile=True` 면 **있어도 다시 만든다.** 이 파일은 `data/` 의 사본이지
       독립된 입력이 아닌데, 없을 때만 만들면 원본이 바뀌어도 낡은 사본이 계속
       이긴다 — 파일이 늘거나 줄어도 감리 AI 는 옛 목록을 본다(예외 없이 값만 틀린다).
       실제로 재활용 도메인에서 지운 데이터셋 2개가 프로파일에 남아 있었다
       (2026-08-12). 호출자는 `full` 모드뿐이다 — 거기서만 원본이 바뀔 수 있다.
    """
    path = profiles_path or _DOMAIN["profiles"]
    if not path:
        raise RuntimeError("도메인 미설정 — set_domain(<도메인폴더>) 먼저 호출 필요")

    if force_profile or not os.path.isfile(path):
        # fixture 없음(또는 강제 재생성) → data/ 를 프로파일링 (무슨 상황인지 출력)
        from app.services.gam2_profile import profile_folder, save_profiles

        data_dir = _DOMAIN["data"]
        print(f"[fixture {'재생성' if os.path.isfile(path) else '없음'}] {path}")
        if not os.path.isdir(data_dir):
            raise FileNotFoundError(
                f"데이터 폴더도 없음: {data_dir}\n"
                f"  → <도메인>/data/ 에 원본(csv·xlsx·shp·json)을 넣으세요."
            )
        print(f"[자동 프로파일링] {data_dir} 를 읽어 fixture 를 생성합니다...")
        profiles = profile_folder(data_dir)
        if not profiles:
            raise RuntimeError(
                f"프로파일 0건 — {data_dir} 에 읽을 수 있는 데이터 파일이 없습니다."
            )
        save_profiles(profiles, path)
        print(f"[자동 프로파일링 완료] {len(profiles)}개 데이터셋 → {path}\n")

    with open(path, encoding="utf-8") as f:
        profiles = json.load(f)
    ordinance = load_ordinance()  # <도메인>/law/ 조례
    if not ordinance:
        print(
            f"[조례 없음] {_DOMAIN['law']} 에 조례(txt/md) 없음 "
            f"— 모든 배제가 미확정(HITL)으로 처리됩니다."
        )
    for p in profiles.values():
        p["ordinance"] = ordinance  # (a) 전 데이터셋 주입
    return profiles


def require_region(fac: dict) -> str:
    """시설 확정 결과(`resolve_facility*`)에서 대상 지역을 꺼낸다. 없으면 멈춘다.

    🔴 예전엔 `DOMAIN = {"facility": "흡연부스", "region": "용산구"}` 를 두고
       `fac.get("region") or DOMAIN["region"]` 로 채웠다(2026-08-10 제거, 사람 승인).
       주석엔 "테스트용 폴백"이라 적혀 있었지만 실제로는 **실행 경로 3곳**에서 쓰였다
       (`gam2_run_pipeline.py:191`·이 파일 real/mock 진입점 2곳).
       MVP 도메인 값이라 성동구 입력에서 지역이 안 잡히면 조용히 **용산구** 조례·상위법을
       검색한다 — 안 터지고 근거만 틀린다. "엔진은 그대로, 데이터만 바꾼다"에도 어긋난다.
    """
    region = str(fac.get("region") or "").strip()
    if not region:
        raise SystemExit(
            "🔴 대상 지역을 확정하지 못했다. 지역을 추측하지 않는다. "
            "입력에 '<시군구>' 를 포함할 것 (예: \"용산구 흡연부스 부지 선정\"). "
            f"입력: {fac.get('source_input', '')!r}"
        )
    return region
