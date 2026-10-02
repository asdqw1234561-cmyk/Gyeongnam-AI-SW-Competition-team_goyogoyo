"""Claude Code SessionStart hook - 세션 시작 시 개발 Agent가 알아야 할 변화를 한 번에 알려준다.

- 현재 브랜치(자율 개발 루프 /steward 는 STEWARD_BRANCH 에서만 코드 수정)
- 미커밋 변경 수
- references/ 의 새 자료·변경 자료·분석 대기 자료 (.claude/scripts/ref_scan.py 의 해시 비교)
- docs/agent/CURRENT_STATE.md 가 마지막으로 확인한 커밋 이후 쌓인 커밋 수 (.agent_state/state.json)
- 제출 마감까지 남은 일수

읽기만 하고 아무것도 수정하지 않는다. 오류가 나도 세션을 막지 않는다(항상 exit 0).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE_PATH = ROOT / ".agent_state" / "state.json"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def _reference_lines() -> list[str]:
    spec = importlib.util.spec_from_file_location("ref_scan", ROOT / ".claude" / "scripts" / "ref_scan.py")
    ref_scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref_scan)
    d = ref_scan.diff(ref_scan.load_index())
    labels = {"new": "새 자료", "changed": "변경된 자료", "removed": "삭제된 자료",
              "pending_extract": "추출 대기", "pending_analysis": "분석 대기"}
    lines = [f"- {label}: {', '.join(Path(p).name for p in d[key])}" for key, label in labels.items() if d[key]]
    if lines:
        lines.append("  → /steward 가 DISCOVER 단계에서 ref_scan.py extract 후 분석·반영한다.")
    return lines


def main() -> int:
    out: list[str] = ["[프로젝트 상태 점검 - SessionStart hook]"]
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}

    branch = _git("branch", "--show-current")
    steward_branch = state.get("steward_branch", "jhy-next")
    note = "" if branch == steward_branch else f" (⚠ /steward 자율 루프는 {steward_branch} 전용 - 여기서는 코드 수정 금지, 보고만)"
    out.append(f"- 브랜치: {branch}{note}")
    dirty = [l for l in _git("status", "--porcelain").splitlines() if l.strip()]
    if dirty:
        out.append(f"- 미커밋 변경: {len(dirty)}개")

    synced = state.get("last_synced_commit")
    if synced:
        behind = _git("rev-list", "--count", f"{synced}..HEAD")
        if behind and behind != "0":
            out.append(f"- docs/agent/CURRENT_STATE.md 이후 새 커밋 {behind}개 → 상태 문서 갱신 필요")
    else:
        out.append("- .agent_state/state.json 없음 또는 last_synced_commit 없음 → CURRENT_STATE 확인 필요")

    deadline = state.get("submission_deadline")
    if deadline:
        days = (dt.date.fromisoformat(deadline[:10]) - dt.date.today()).days
        out.append(f"- 제출 마감 {deadline} (D-{days})" if days >= 0 else f"- 제출 마감 {deadline} 지남 - 마감 후 핵심기능·소스 신규 추가 금지")

    try:
        out.extend(_reference_lines())
    except Exception as exc:  # 색인 손상 등: 알리기만 한다
        out.append(f"- references 점검 실패: {type(exc).__name__}: {exc}")

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": "\n".join(out)}}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
