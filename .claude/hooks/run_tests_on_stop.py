"""Claude Code Stop hook - 작업을 끝내기 전에 단위 테스트를 자동 실행한다.

- 작업 트리에 바뀐 .py 파일(수정·추가·삭제, 미추적 포함)이 없으면 아무것도 하지 않는다.
- 있으면 `python -m unittest discover -s tests`를 실행한다.
  - 통과: 결과를 한 줄로 알려주고 그대로 종료를 허용한다.
  - 실패: decision="block"으로 종료를 막고 실패 내용을 Claude에게 돌려줘 스스로 고치게 한다.
    단, 이미 이 hook 때문에 한 번 이어서 작업한 상태(stop_hook_active)라면 무한 반복을
    막기 위해 다시 막지 않고 경고만 남긴다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
TAIL_LINES = 40


def _changed_py_files() -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=PROJECT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    files = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:  # rename
            path = path.split(" -> ", 1)[1]
        if path.endswith(".py") and not path.startswith(".claude/"):
            files.append(path)
    return files


def _emit(payload: dict) -> None:
    # ensure_ascii=True: Windows 콘솔 인코딩과 무관하게 한글이 깨지지 않도록 이스케이프해서 보낸다.
    print(json.dumps(payload, ensure_ascii=True))


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    changed = _changed_py_files()
    if not changed:
        return 0

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=PROJECT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=600,
    )
    summary = next(
        (l for l in reversed(proc.stderr.splitlines()) if l.startswith(("OK", "FAILED"))), "결과 확인 불가"
    )
    ran = next((l for l in reversed(proc.stderr.splitlines()) if l.startswith("Ran ")), "")

    if proc.returncode == 0:
        _emit({"systemMessage": f"[자동 테스트] 변경된 .py {len(changed)}개 감지 - {ran} {summary}".strip()})
        return 0

    tail = "\n".join(proc.stderr.splitlines()[-TAIL_LINES:])
    if hook_input.get("stop_hook_active"):
        _emit({"systemMessage": f"[자동 테스트] 여전히 실패 중입니다 ({summary}). 사용자 확인이 필요합니다."})
        return 0

    _emit({
        "decision": "block",
        "reason": (
            f"[자동 테스트] 단위 테스트가 실패했습니다 ({ran} {summary}). "
            "CLAUDE.md 원칙을 지키면서 원인을 고치고 다시 테스트하세요. "
            "테스트 기대값을 실패에 맞춰 바꾸는 것은 기능 의미가 바뀐 경우에만 하고, 그 이유를 보고하세요.\n\n"
            f"{tail}"
        ),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
