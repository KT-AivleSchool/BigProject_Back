# -*- coding: utf-8 -*-
r"""분할 전후 **본문이 같은가** — AST 대조.

    python app\tools\check_split_ast.py <옛리비전>:<옛경로> <새패키지폴더>
    python app\tools\check_split_ast.py ecfa27c~1:app/services/gam2_audit_judgment_test.py ^
                                        app/services/gam2_audit_judgment_test

🔴 **이것이 재는 것**
   한 파일을 여러 서브모듈로 가를 때 진짜 위험은 「import 가 깨진다」가 아니다 —
   그건 터지니까 걸린다. 위험한 건 **옮기다 한 줄이 바뀌는 것**이다. 조건 하나,
   기본값 하나가 달라져도 예외는 안 나고 **값만 조용히 틀린다**.

   그리고 이 저장소에서 그걸 잡아 줄 유일한 자(`check_fixture.py`)는 **아무 데서나
   못 돈다** — `datasets/region_data/`(지적도·경계)가 `.gitignore` 라 clone 에
   안 들어오고, 산출물을 새로 뽑으려면 유료 실행 + 공유 정본 덮어쓰기다.
   즉 「분할했는데 값이 그대로인가」를 **숫자로는 증명할 수 없는 자리**가 있다.

   여기서 묻는 것은 그래서 숫자가 아니라 **본문**이다. 최상위 정의(함수·클래스·
   상수)의 AST 가 옛 파일과 **같은 객체 모양**이면, 값이 달라질 수 있는 경로가
   구조적으로 없다. 줄 수·포맷·주석·import 위치는 안 본다(그건 갈라도 되는 것들이다).

🔴 **초록불이 목표가 아니다.** 분할이 **일부러** 바꾸는 것이 있다 —
   깊어진 폴더만큼 맞춘 `_ROOT`(`".."` 2→3) 같은 것. 그런 건 여기서 **다르다고
   나오는 게 맞다**. 예외 목록을 이 도구에 심지 않는다: 예외 목록은 언젠가 상하고,
   상한 예외 목록은 「안 봤다」를 「봤는데 괜찮다」로 바꾼다(원칙 4).
   다른 것이 나오면 **그 자리를 눈으로 보고 커밋 메시지에 적는다.**

🔴 **같은 이름이 두 서브모듈에 있으면 그것도 알린다.** 프록시가 `raise` 하는
   바로 그 모호함이고(원칙 1), 옮기다 사본을 만든 흔적이기도 하다
   (「모듈 사본」 함정 — import 는 멀쩡히 되고 값만 다르게 나온다).

DB·LLM·네트워크 안 쓴다. `git show` 로 옛 파일을 꺼내 파싱만 한다.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 최상위에서 「이름을 가진 것」으로 셀 노드
_NAMED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _names_of(node: ast.stmt) -> list[str]:
    """최상위 문장 하나가 정의하는 이름들."""
    if isinstance(node, _NAMED):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _collect(src: str, where: str) -> tuple[dict[str, tuple[str, ast.stmt]], list[str]]:
    """최상위 정의를 이름 → (어느 파일, 노드) 로. 같은 파일 안 재정의도 알린다."""
    tree = ast.parse(src)
    out: dict[str, tuple[str, ast.stmt]] = {}
    dups: list[str] = []
    for node in tree.body:
        for name in _names_of(node):
            if name in out:
                dups.append(f"{name} ({where} 안에서 두 번)")
            out[name] = (where, node)
    return out, dups


def _dump(node: ast.stmt) -> str:
    """줄번호·컬럼은 빼고 모양만. 포맷·주석 차이는 여기서 사라진다."""
    return ast.dump(node, include_attributes=False, indent=2)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    old_ref, new_dir_arg = sys.argv[1], sys.argv[2]
    if ":" not in old_ref:
        print(f"🔴 첫 인자는 '<리비전>:<경로>' 형태여야 합니다 — 받은 것: {old_ref}")
        return 2

    try:
        old_src = subprocess.run(
            ["git", "show", old_ref],
            cwd=_ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
    except subprocess.CalledProcessError as e:
        print(f"🔴 옛 파일을 못 꺼냈습니다: {old_ref}\n{e.stderr.decode('utf-8', 'replace')}")
        return 2

    new_dir = (_ROOT / new_dir_arg).resolve()
    if not new_dir.is_dir():
        print(f"🔴 새 패키지 폴더가 없습니다: {new_dir}")
        return 2

    old_defs, old_dups = _collect(old_src, old_ref.split(":")[-1])

    new_defs: dict[str, tuple[str, ast.stmt]] = {}
    new_dups: list[str] = []
    for p in sorted(new_dir.glob("*.py")):
        d, dd = _collect(p.read_text(encoding="utf-8"), p.name)
        new_dups += dd
        for name, (where, node) in d.items():
            if name in new_defs:
                # 🔴 프록시가 raise 하는 그 모호함이자, 옮기다 사본을 만든 흔적이다.
                new_dups.append(f"{name} ({new_defs[name][0]} · {where})")
            new_defs[name] = (where, node)

    print(f"옛 파일 {old_ref}  최상위 정의 {len(old_defs)}개")
    print(f"새 패키지 {new_dir.name}/  ({len(list(new_dir.glob('*.py')))}파일) 최상위 정의 {len(new_defs)}개\n")

    same, diff, missing = [], [], []
    for name, (_, onode) in sorted(old_defs.items()):
        if name not in new_defs:
            missing.append(name)
            continue
        where, nnode = new_defs[name]
        (same if _dump(onode) == _dump(nnode) else diff).append((name, where))

    added = sorted(set(new_defs) - set(old_defs))

    print(f"  본문 동일          {len(same)}개")
    print(f"  🔴 본문 다름        {len(diff)}개")
    print(f"  🔴 새 패키지에 없음  {len(missing)}개")
    print(f"  ⚠ 분할하며 생긴 것  {len(added)}개")
    print(f"  🔴 이름 중복        {len(old_dups) + len(new_dups)}건\n")

    for name, where in diff:
        print(f"[다름] {name}   → {where}")
        o, n = _dump(old_defs[name][1]).splitlines(), _dump(new_defs[name][1]).splitlines()
        import difflib

        for line in list(difflib.unified_diff(o, n, "옛", "새", n=1, lineterm=""))[:40]:
            print(f"       {line}")
        print()

    for name in missing:
        print(f"[없음] {name}   — 옛 파일엔 있는데 새 패키지 어디에도 없다")
    for d in old_dups + new_dups:
        print(f"[중복] {d}")
    if added:
        print(f"[신규] {', '.join(f'{n}({new_defs[n][0]})' for n in added)}")

    bad = len(diff) + len(missing) + len(old_dups) + len(new_dups)
    print(f"\n{'✅ 본문 차이 0건 — 값이 달라질 경로가 없다.' if bad == 0 else f'🔴 확인할 것 {bad}건 — 눈으로 보고 커밋에 적을 것.'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
