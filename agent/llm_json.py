# LLM 응답 JSON 추출·정리 공용 헬퍼 (planner / location_agent / ollama_agent 공통)
"""
LLM(Ollama, claude CLI)이 돌려준 텍스트에서 JSON 객체를 꺼내고, 화면 표시용 필드를
길이·개수 제한으로 정리하는 함수들. 각 Agent 모듈에 같은 코드가 반복돼 있던 것을
한곳으로 모았다 - 동작(제한 길이 포함)은 기존과 같고, JSON 추출만 더 안전해졌다.

[JSON 추출 방식]
    예전에는 `\\{.*\\}` 탐욕적 정규식으로 "첫 '{'부터 마지막 '}'까지"를 통째로 json.loads
    했다. 그래서 응답에 JSON이 두 개 있거나 설명 문장 안에 중괄호가 섞이면 파싱에
    실패해 곧바로 기본 절차로 폴백했다. 지금은 json.JSONDecoder.raw_decode로 각 '{'
    위치에서 "완전한 JSON 객체 하나"를 읽어 보고, 처음으로 성공한 dict를 쓴다.
"""

from __future__ import annotations

import json

_DECODER = json.JSONDecoder()


def extract_json_object(raw_text: str | None) -> dict | None:
    """텍스트 안의 첫 번째 완전한 JSON 객체(dict)를 반환한다. 없으면 None."""
    text = raw_text or ""
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = _DECODER.raw_decode(text, idx)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    return None


def sanitize_goals(raw: object) -> list[str]:
    """AI가 제안한 분석 목표 목록 - 최대 10개, 각 100자."""
    if not isinstance(raw, list):
        return []
    return [str(g).strip()[:100] for g in raw[:10] if str(g).strip()]


def sanitize_unsupported_requests(raw: object, request_max_len: int = 100) -> list[dict]:
    """AI가 분류한 '지원하지 않는 요청' 목록 - 최대 10개, request는 request_max_len자,
    reason은 200자. 구조만 검증하며 내용은 표시용으로만 쓴다."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        request = str(item.get("request") or "").strip()[:request_max_len]
        reason = str(item.get("reason") or "").strip()[:200]
        if request:
            out.append({"request": request, "reason": reason})
    return out
