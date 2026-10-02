# LLM 백엔드 선택 (Ollama / Claude Code CLI)
"""
.env 의 LLM_BACKEND 값으로 Ollama 와 Claude Code CLI 중 하나를 고른다.

    LLM_BACKEND=ollama       (기본값) 기존처럼 로컬 Ollama 사용
    LLM_BACKEND=claude_cli   PC에 로그인된 Claude Code CLI(claude -p) 사용

기존 agent 코드에서는 `from agent import llm` 한 줄을 추가하고 `ollama.chat(...)` 호출을
`llm.chat(...)` 으로 바꾸면 된다. `import ollama` 는 지우지 않는다(기존 테스트가
mock.patch("agent.xxx.ollama.chat") 으로 모킹하는데, Ollama 백엔드일 때 llm.chat 이
같은 ollama.chat 을 부르므로 그 모킹이 그대로 적용된다).
인자와 반환 모양(response["message"]["content"])이 같으므로 나머지 코드는 그대로 둔다.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

SUPPORTED_BACKENDS = ("ollama", "claude_cli")


def get_backend() -> str:
    value = (os.environ.get("LLM_BACKEND") or "ollama").strip().lower()
    return value if value in SUPPORTED_BACKENDS else "ollama"


def chat(**kwargs):
    """ollama.chat(**kwargs) 와 같은 방식으로 호출한다."""
    if get_backend() == "claude_cli":
        from agent import claude_cli
        return claude_cli.chat(**kwargs)

    import ollama
    return ollama.chat(**kwargs)
