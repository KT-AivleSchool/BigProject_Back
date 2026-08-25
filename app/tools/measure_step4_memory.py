"""STEP4 입지선정(MCLP) 의 피크 RSS 를 **자식 프로세스 밖에서** 잰다.

왜 필요했나
----------
2026-08-24 EC2 에서 `EV 충전소_2` STEP4 가 커널 OOM 으로 죽었다
(`global_oom` · anon-RSS **1,692MB** / VSZ 5,282MB · 호스트 총 1,910MB).
죽은 자리는 `gam4_site_select.select_mclp:537` 의 `S.neighbors_within(...)` 안이다
— 그 직후에 찍혀야 할 `후보 … × 수요점 … 커버 쌍 …` 이 로그에 없다.

그런데 **1.7GB 중 쌍 배열이 몇 MB인지는 안 쟀다.** 「쌍 배열만 줄이면 된다」는
지금 추측이고(원칙 5), 이 도구가 그 추측을 숫자로 바꾼다.

무엇을 재나 — 그리고 무엇을 **못** 재나
--------------------------------------
잰다:
  · 자식 프로세스의 **피크 RSS** 와 그 시각
  · stdout 마커 시점의 RSS (= `neighbors_within` **직전 / 직후**)
  · 쌍 배열의 **결정론적 크기**(`커버 쌍 N` × dtype × 배열수)

못 잰다 (그래서 단정하지 않는다):
  · 피크의 **내역**을 항목별로 쪼개는 것. RSS 는 총량이다 — 여기서 나오는 것은
    「구간 사이의 증가분」이지 「어느 객체가 몇 MB」가 아니다.
  · 리눅스 anon-RSS 와 윈도우 RSS 는 **같은 값이 아니다**. 윈도우는 working set 이라
    페이지 반환 정책이 다르다 → 절대값을 EC2 의 1,692MB 와 **직접 대면 안 된다.**
    이 도구가 답하는 것은 **비율**이다(쌍 배열이 피크의 몇 %인가).

왜 in-process 가 아니라 자식인가
--------------------------------
실제 파이프라인이 자식 프로세스로 돌린다(`pipeline_runner._proc_of` stage "4").
in-process 로 재면 우리 하네스의 import 가 섞이고, 무엇보다 **측정 대상이
실제로 배포되는 실행 형태가 아니게 된다**(「in-process 대조기」 함정과 같은 계열).

사용
----
    python app\\tools\\measure_step4_memory.py 흡연

⚠ 이 스크립트는 **STEP4 를 실제로 돌린다** — `datasets/step4_output/<도메인>_*` 를
   덮어쓴다. 기존 산출물은 자동으로 백업했다가 되돌린다(`--no-restore` 로 끔).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

STEP4_OUT = ROOT / "datasets" / "step4_output"

# `neighbors_within` 을 사이에 두고 찍히는 두 줄. 이 사이의 RSS 증가분이 쌍 배열이다.
#   앞: gam4_site_select.py  build_demand_grid 끝     "[수요격자] 6,797점"
#   뒤: select_mclp:549                                 "후보 … 커버 쌍 … (R_cover=…)"
MARK_BEFORE = "[수요격자]"
MARK_AFTER = "커버 쌍"
MARK_SELECT = "[H] 선정"


def _fmt(nbytes: float) -> str:
    return f"{nbytes / 1024 / 1024:,.1f} MB"


class Poller(threading.Thread):
    """자식(및 그 손자)의 RSS 를 주기적으로 샘플링한다."""

    def __init__(self, pid: int, interval: float = 0.025):
        super().__init__(daemon=True)
        self.proc = psutil.Process(pid)
        self.interval = interval
        self.samples: list[tuple[float, int]] = []  # (경과초, rss)
        self.stop_flag = threading.Event()
        self.t0 = time.monotonic()

    def _rss(self) -> int:
        total = 0
        try:
            total += self.proc.memory_info().rss
            for ch in self.proc.children(recursive=True):
                try:
                    total += ch.memory_info().rss
                except psutil.Error:
                    pass
        except psutil.Error:
            return -1
        return total

    def run(self):
        while not self.stop_flag.is_set():
            rss = self._rss()
            if rss < 0:
                break
            self.samples.append((time.monotonic() - self.t0, rss))
            time.sleep(self.interval)

    def rss_at(self, t: float) -> int | None:
        """시각 t 이하의 마지막 샘플."""
        best = None
        for ts, rss in self.samples:
            if ts <= t:
                best = rss
            else:
                break
        return best

    def peak(self) -> tuple[float, int]:
        if not self.samples:
            return (0.0, 0)
        t, rss = max(self.samples, key=lambda s: s[1])
        return (t, rss)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("--spacing", default="20", help="러너 기본값과 같게 (fixture 조건)")
    ap.add_argument("--topn", default="20")
    ap.add_argument("--interval", type=float, default=0.025, help="샘플링 주기(초)")
    ap.add_argument("--no-restore", action="store_true",
                    help="기존 step4 산출물을 되돌리지 않는다")
    args = ap.parse_args()

    # ── 기존 산출물 백업 (이 도구는 진단인데 산출물을 갈아치우면 진단이 아니다)
    backup: dict[Path, bytes] = {}
    if STEP4_OUT.is_dir():
        for p in STEP4_OUT.glob(f"{args.domain}_*"):
            if p.is_file():
                backup[p] = p.read_bytes()
    if backup:
        print(f"[백업] 기존 step4 산출물 {len(backup)}개를 메모리에 담았다 "
              f"({_fmt(sum(len(v) for v in backup.values()))})")

    py = sys.executable
    argv = [py, str(ROOT / "app" / "services" / "gam4_site_select.py"),
            args.domain, "--spacing", args.spacing, "--topn", args.topn]
    print(f"[실행] {' '.join(argv[1:])}\n")

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        argv, cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    poller = Poller(proc.pid, args.interval)
    poller.start()

    marks: list[tuple[float, str]] = []   # (경과초, 줄)
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        t = time.monotonic() - poller.t0
        line = line.rstrip()
        lines.append(line)
        if MARK_BEFORE in line or MARK_AFTER in line or MARK_SELECT in line:
            marks.append((t, line))
        cur = poller.samples[-1][1] if poller.samples else 0
        print(f"  {t:7.2f}s {cur / 1024 / 1024:7.0f}MB | {line}")

    rc = proc.wait()
    poller.stop_flag.set()
    poller.join(timeout=2)

    # ── 산출물 되돌리기
    if backup and not args.no_restore:
        for p in STEP4_OUT.glob(f"{args.domain}_*"):
            if p.is_file() and p not in backup:
                p.unlink()
        for p, data in backup.items():
            p.write_bytes(data)
        print(f"\n[복원] step4 산출물 {len(backup)}개를 되돌렸다")

    # ── 보고
    print("\n" + "=" * 72)
    print(f"종료 코드 {rc} · 샘플 {len(poller.samples):,}개 "
          f"(주기 {args.interval * 1000:.0f}ms)")
    if not poller.samples:
        print("🔴 샘플이 0개다 — 자식이 즉시 끝났거나 RSS 를 못 읽었다. 판정 불가")
        return 1

    pt, prss = poller.peak()
    print(f"\n피크 RSS  {_fmt(prss)}   (t={pt:.2f}s)")

    print("\n마커별 RSS")
    for t, line in marks:
        rss = poller.rss_at(t)
        head = line if len(line) <= 58 else line[:55] + "..."
        print(f"  {t:7.2f}s  {_fmt(rss or 0):>12}   {head}")

    # ── neighbors_within 구간 증가분
    t_before = next((t for t, ln in marks if MARK_BEFORE in ln), None)
    t_after = next((t for t, ln in marks if MARK_AFTER in ln), None)
    if t_before is not None and t_after is not None:
        r0, r1 = poller.rss_at(t_before), poller.rss_at(t_after)
        seg = [s for s in poller.samples if t_before <= s[0] <= t_after]
        seg_peak = max((r for _, r in seg), default=0)
        print(f"\nneighbors_within 구간  ({t_before:.2f}s → {t_after:.2f}s, "
              f"{t_after - t_before:.2f}초)")
        print(f"  직전   {_fmt(r0 or 0)}")
        print(f"  직후   {_fmt(r1 or 0)}")
        print(f"  구간피크 {_fmt(seg_peak)}   ← concatenate 순간 2배가 여기 보인다")
        if r0:
            print(f"  증가분 {_fmt((r1 or 0) - r0)}  "
                  f"/ 구간피크 기준 {_fmt(seg_peak - r0)}")
    else:
        print("\n⚠ 마커를 못 찾아 구간을 못 갈랐다 "
              f"(before={t_before}, after={t_after}). 문구가 바뀌었는지 볼 것")

    # ── 쌍 배열의 결정론적 크기
    pairs = None
    for ln in lines:
        if MARK_AFTER in ln:
            for tok in ln.replace(",", "").split():
                if tok.isdigit() and int(tok) > 100_000:
                    pairs = int(tok)
            break
    if pairs:
        print(f"\n쌍 배열 결정론 계산  (커버 쌈 {pairs:,})".replace("쌈", "쌍"))
        cur = pairs * 8 * 3
        print(f"  지금   int64 ci + int64 tj + float64 D = {_fmt(cur)}")
        print(f"         + np.concatenate 순간 사본        = {_fmt(cur * 2)} (피크)")
        print(f"  demw = dem[tj] (float64)                = {_fmt(pairs * 8)}")
        nxt = pairs * 4 * 2
        print(f"  줄이면 int32 ci + int32 tj, D 미생성      = {_fmt(nxt)}")
        print(f"         + 미리 할당(사본 없음)             = {_fmt(nxt)} (피크)")
        print(f"  → 피크 기준 절감 {_fmt(cur * 2 + pairs * 8 - nxt)} "
              f"(총 피크의 {(cur * 2 + pairs * 8 - nxt) / prss * 100:.1f}%)")

    print("\n⚠ 윈도우 RSS(working set) 는 리눅스 anon-RSS 와 같은 값이 아니다.")
    print("   EC2 의 1,692MB 와 절대값을 직접 대지 말 것 — 여기서 읽을 것은 **비율**이다.")
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    raise SystemExit(main())
