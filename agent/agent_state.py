# Agent 상태 관리 - 위치 분석 대화 기억
"""
위치 분석 Agent가 "그럼 버스는?", "1km로 넓히면?" 같은 후속 질문을 이해할 수 있도록
이전 질문·답변·사용한 도구를 기억한다.

    memory = ConversationMemory(st.session_state)          # Streamlit 세션에 저장
    history = memory.recent(search_center=(lat, lon))       # 같은 위치의 최근 대화만
    result = run_location_agent(..., history=history)
    memory.add_from_result(result)

[원칙]
    - 기억은 "질문의 맥락을 이해하는 데"만 쓴다. 이전 답변의 숫자는 다시 쓰지 않고, 필요하면
      다시 조회한다(답변 검증도 이번 조회 결과의 숫자만 허용한다).
    - 검색 위치가 바뀌면 이전 위치의 대화는 맥락에 넣지 않는다(다른 장소의 결과가 섞이지 않게).
    - 저장 위치는 Streamlit 세션(브라우저 접속 단위)이다. 서버나 디스크에 남기지 않는다.
    - 프롬프트가 길어지지 않도록 최근 MAX_TURNS 개만, 질문·답변은 일정 길이로 자른다.
"""

from __future__ import annotations

from typing import MutableMapping, Optional

MAX_TURNS = 5            # 저장하는 최대 대화 수
CONTEXT_TURNS = 3        # AI에게 보여주는 최근 대화 수
MAX_TEXT_CHARS = 300     # 질문·답변 1개당 저장 길이


def _same_center(a, b, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a[0]) - float(b[0])) <= tol and abs(float(a[1]) - float(b[1])) <= tol
    except (TypeError, ValueError, IndexError):
        return False


class ConversationMemory:
    def __init__(self, store: Optional[MutableMapping] = None, key: str = "location_agent_memory",
                 max_turns: int = MAX_TURNS):
        self._store = store if store is not None else {}
        self._key = key
        self._max_turns = max_turns
        if not isinstance(self._store.get(self._key), list):
            self._store[self._key] = []

    @property
    def turns(self) -> list[dict]:
        return list(self._store[self._key])

    def add_turn(self, *, user_text: str, search_center, radius_m, answer: str, tools: list[str]) -> None:
        turns = self._store[self._key]
        turns.append({
            "user_text": str(user_text or "")[:MAX_TEXT_CHARS],
            "search_center": tuple(search_center) if search_center else None,
            "radius_m": radius_m,
            "answer": str(answer or "")[:MAX_TEXT_CHARS],
            "tools": list(tools or []),
        })
        del turns[:-self._max_turns]

    def add_from_result(self, result: dict) -> None:
        """run_location_agent() 반환값에서 기억할 내용만 뽑아 저장한다."""
        if not result or result.get("status") != "ok":
            return
        final = result.get("final_answer") or {}
        answer = final.get("text") or ""
        if not answer and result.get("unsupported_requests"):
            answer = "지원하지 않는 요청: " + ", ".join(u["request"] for u in result["unsupported_requests"])
        self.add_turn(
            user_text=result.get("user_text"),
            search_center=result.get("search_center"),
            radius_m=result.get("resolved_radius_m"),
            answer=answer,
            tools=[e["tool"] for e in result.get("executed_tool_calls") or [] if e.get("executed")],
        )

    def recent(self, n: int = CONTEXT_TURNS, search_center=None) -> list[dict]:
        turns = self.turns
        if search_center is not None:
            turns = [t for t in turns if _same_center(t["search_center"], search_center)]
        return turns[-n:] if n > 0 else []

    def clear(self) -> None:
        self._store[self._key] = []


def history_to_prompt(history: Optional[list[dict]]) -> str:
    """AI 프롬프트에 넣을 이전 대화 블록. 없으면 빈 문자열."""
    if not history:
        return ""
    lines = ["[같은 검색 위치에서의 이전 대화 - 생략된 질문의 맥락 파악용]"]
    for i, turn in enumerate(history, start=1):
        tools = ", ".join(turn.get("tools") or []) or "없음"
        lines.append(f"{i}. 사용자: {turn['user_text']}")
        lines.append(f"   답변 요약: {turn['answer']}")
        lines.append(f"   (반경 {turn.get('radius_m')}m, 사용 도구: {tools})")
    lines.append("주의: 이전 대화는 '그럼 버스는?'처럼 생략된 질문이 무엇을 가리키는지 이해하는 데만 쓰세요. "
                 "이전 답변의 숫자를 다시 쓰지 말고, 필요한 정보는 이번에 조회한 결과로만 답하세요.")
    return "\n".join(lines)
