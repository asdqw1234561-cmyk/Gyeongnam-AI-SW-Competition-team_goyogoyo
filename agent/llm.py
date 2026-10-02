# LLM 백엔드 선택 (Ollama / Claude Code CLI) + AI 호출 사용량 제한
"""
.env 의 LLM_BACKEND 값으로 Ollama 와 Claude Code CLI 중 하나를 고른다.

    LLM_BACKEND=ollama       (기본값) 기존처럼 로컬 Ollama 사용
    LLM_BACKEND=claude_cli   PC에 로그인된 Claude Code CLI(claude -p) 사용

기존 agent 코드에서는 `from agent import llm` 한 줄을 추가하고 `ollama.chat(...)` 호출을
`llm.chat(...)` 으로 바꾸면 된다. `import ollama` 는 지우지 않는다(기존 테스트가
mock.patch("agent.xxx.ollama.chat") 으로 모킹하는데, Ollama 백엔드일 때 llm.chat 이
같은 ollama.chat 을 부르므로 그 모킹이 그대로 적용된다).
인자와 반환 모양(response["message"]["content"])이 같으므로 나머지 코드는 그대로 둔다.

[남용 방지 - 모든 AI 호출이 이 chat() 한 곳을 지나가므로 여기서 한 번에 제한한다]
    LLM_MAX_CALLS_PER_SESSION   브라우저 접속(Streamlit 세션) 1개당 최대 호출 수 (기본 30)
    LLM_MAX_CALLS_PER_DAY       서버 프로세스 전체(모든 사용자 합산) 하루 최대 호출 수 (기본 500)
                                새로고침으로 세션 제한을 우회해도 이 상한은 넘을 수 없다.
    LLM_MAX_INPUT_CHARS         system 을 뺀 메시지 내용 합계 최대 글자 수 (기본 4000)
    값을 0 으로 두면 해당 제한을 끈다.

    호출 횟수 제한은 Streamlit 화면 안에서 호출될 때만 적용된다(단위 테스트·스크립트 제외).
    입력 길이 제한은 항상 적용된다.

    제한에 걸리면 LLMLimitError(RuntimeError 하위 클래스)를 던진다. agent 코드는 이미
    `except Exception` 으로 AI 실패를 받아 기본 절차(fallback)로 넘어가므로, 제한에 걸려도
    화면이 멈추지 않고 "AI 없이" 계속 동작한다.
"""

from __future__ import annotations

import os
import threading
from datetime import date

from dotenv import load_dotenv

load_dotenv()

SUPPORTED_BACKENDS = ("ollama", "claude_cli")
BACKEND_LABELS = {"ollama": "로컬 Ollama", "claude_cli": "Claude(로컬 CLI)"}

DEFAULT_MAX_CALLS_PER_SESSION = 30
DEFAULT_MAX_CALLS_PER_DAY = 500
DEFAULT_MAX_INPUT_CHARS = 4000

_SESSION_KEY = "_llm_call_count"
_daily_lock = threading.Lock()
_daily_counter = {"date": None, "count": 0}


class LLMLimitError(RuntimeError):
    """사용량·입력 길이 제한에 걸림. 메시지는 화면에 그대로 보여줘도 되는 한국어 문장."""


def get_backend() -> str:
    value = (os.environ.get("LLM_BACKEND") or "ollama").strip().lower()
    return value if value in SUPPORTED_BACKENDS else "ollama"


def backend_label() -> str:
    """화면 문구용 이름. 예: 'Claude(로컬 CLI)', '로컬 Ollama'."""
    return BACKEND_LABELS[get_backend()]


def _limit(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _session_state():
    """Streamlit 화면 안에서 호출될 때만 session_state 를 돌려준다(테스트·스크립트에선 None)."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except Exception:  # streamlit 미설치 등
        return None
    try:
        ctx = get_script_run_ctx(suppress_warning=True)
    except TypeError:  # 구버전 streamlit
        ctx = get_script_run_ctx()
    if ctx is None:
        return None
    import streamlit as st
    return st.session_state


def _input_chars(messages) -> int:
    total = 0
    for msg in messages or []:
        if isinstance(msg, dict) and msg.get("role") != "system":
            total += len(str(msg.get("content", "")))
    return total


def _check_and_count(messages) -> None:
    max_chars = _limit("LLM_MAX_INPUT_CHARS", DEFAULT_MAX_INPUT_CHARS)
    if max_chars and _input_chars(messages) > max_chars:
        raise LLMLimitError(
            f"입력이 너무 깁니다(최대 {max_chars}자). 요청을 짧게 줄여 다시 시도해 주세요."
        )

    state = _session_state()
    per_session = _limit("LLM_MAX_CALLS_PER_SESSION", DEFAULT_MAX_CALLS_PER_SESSION)
    if state is not None and per_session and state.get(_SESSION_KEY, 0) >= per_session:
        raise LLMLimitError(
            f"이번 접속에서 사용할 수 있는 AI 호출 횟수({per_session}회)를 모두 사용했습니다. "
            "AI 없이 기본 분석으로 계속 진행합니다."
        )

    if state is None:
        return  # 테스트·스크립트 실행(화면 밖)에서는 호출 횟수를 세지 않는다

    per_day = _limit("LLM_MAX_CALLS_PER_DAY", DEFAULT_MAX_CALLS_PER_DAY)
    with _daily_lock:
        today = date.today().isoformat()
        if _daily_counter["date"] != today:
            _daily_counter.update(date=today, count=0)
        if per_day and _daily_counter["count"] >= per_day:
            raise LLMLimitError(
                "오늘 서비스 전체의 AI 사용량이 모두 소진되었습니다. AI 없이 기본 분석으로 계속 진행합니다."
            )
        _daily_counter["count"] += 1
    state[_SESSION_KEY] = state.get(_SESSION_KEY, 0) + 1


def get_usage() -> dict:
    """현재 사용량(화면 표시·점검용). 한도 0 은 '제한 없음'."""
    state = _session_state()
    with _daily_lock:
        daily = _daily_counter["count"] if _daily_counter["date"] == date.today().isoformat() else 0
    return {
        "backend": get_backend(),
        "session_used": state.get(_SESSION_KEY, 0) if state is not None else None,
        "session_limit": _limit("LLM_MAX_CALLS_PER_SESSION", DEFAULT_MAX_CALLS_PER_SESSION),
        "daily_used": daily,
        "daily_limit": _limit("LLM_MAX_CALLS_PER_DAY", DEFAULT_MAX_CALLS_PER_DAY),
    }


def chat(**kwargs):
    """ollama.chat(**kwargs) 와 같은 방식으로 호출한다. 제한에 걸리면 LLMLimitError."""
    _check_and_count(kwargs.get("messages"))

    if get_backend() == "claude_cli":
        from agent import claude_cli
        return claude_cli.chat(**kwargs)

    import ollama
    return ollama.chat(**kwargs)
