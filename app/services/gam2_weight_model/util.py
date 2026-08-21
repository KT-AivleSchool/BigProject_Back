# -*- coding: utf-8 -*-
"""의존성 없는 계산 유틸 — 순위·타이머·표기 정규화·수치컬럼 탐지"""
from __future__ import annotations

import re
import time as _time
import numpy as np
import pandas as pd


def rankdata(a: np.ndarray) -> np.ndarray:
    """scipy.stats.rankdata 대체(평균 순위, 동점 처리). 의존성 최소화용.
    a: 1차원 배열 → 1부터 시작하는 순위, 동점은 평균 순위."""
    a = np.asarray(a, dtype=float)
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    # 동점 평균 처리
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, ranks)
    return (sums / cnt)[inv]


class Timer:
    """단계별 소요 시간 기록 → 마지막에 표로 출력.
    (gam2_run_pipeline.Timer 와 같은 형식. STEP3·STEP4 가 공유한다)"""

    def __init__(self):
        self.laps: list[tuple[str, float]] = []
        self.t0 = _time.perf_counter()
        self._mark = self.t0

    def lap(self, name: str) -> float:
        now = _time.perf_counter()
        el = now - self._mark
        self._mark = now
        self.laps.append((name, el))
        return el

    @property
    def total(self) -> float:
        return _time.perf_counter() - self.t0

    def report(
        self, title: str = "소요 시간", import_sec: float = 0.0, start: float = 0.0
    ) -> None:
        """import_sec: 라이브러리 임포트 시간(Timer 생성 전이라 랩에 안 잡힌다).
        start: 프로세스 기동 시각 — 주면 '체감 시간'을 함께 보여준다."""
        total = self.total
        rows = list(self.laps)
        if import_sec > 0:
            rows.insert(0, ("(임포트)", import_sec))
        denom = total + (import_sec if import_sec > 0 else 0)
        print("\n" + "=" * 60)
        print(f"[{title}]")
        print("-" * 60)
        for name, sec in rows:
            pct = (sec / denom * 100) if denom else 0
            bar = "█" * max(1, int(pct / 4))
            print(f"  {name:24} {sec:7.2f}s  {pct:5.1f}%  {bar}")
        print("-" * 60)
        print(f"  {'처리 합계':24} {total:7.2f}s")
        if start:
            print(f"  {'체감(기동~종료)':24} {_time.perf_counter() - start:7.2f}s")
        print("=" * 60)


# =========================================================
# 유틸: 표기 정규화 (gis_load 와 동일 규칙 — 조인 키 맞춤)
# =========================================================
def _norm_station(v) -> str:
    return re.sub(r"\(.*?\)", "", str(v)).strip()


def _norm_none(v) -> str:
    return str(v).strip()


_NORMALIZERS = {"none": _norm_none, "strip_paren": _norm_station}

_VALUE_HINT = ("승객", "승차", "하차", "인구", "수", "량", "건수")


def _pick_value_cols(df: pd.DataFrame) -> list:
    """통계표에서 합산할 수치 컬럼 자동탐지(식별자·좌표·코드 제외)."""
    skip = (
        "id",
        "코드",
        "번호",
        "일자",
        "노선",
        "역명",
        "좌표",
        "위도",
        "경도",
        "ID",
        "CD",
        "NM",
        "geometry",
    )
    out = []
    for c in df.columns:
        if any(s in str(c) for s in skip):
            continue
        if pd.api.types.is_numeric_dtype(df[c]) and any(
            h in str(c) for h in _VALUE_HINT
        ):
            out.append(c)
    return out


def _safe_corr(Xr: np.ndarray) -> np.ndarray:
    """0-분산 컬럼이 있어도 NaN 없이 상관행렬. 분산 0인 열의 상관은 0으로."""
    m = Xr.shape[1]
    std = Xr.std(axis=0)
    corr = np.eye(m)
    for a in range(m):
        for b in range(a + 1, m):
            if std[a] < 1e-12 or std[b] < 1e-12:
                r = 0.0
            else:
                r = np.corrcoef(Xr[:, a], Xr[:, b])[0, 1]
                r = 0.0 if np.isnan(r) else r
            corr[a, b] = corr[b, a] = r
    return corr
