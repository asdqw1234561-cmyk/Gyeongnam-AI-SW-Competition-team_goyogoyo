# Ollama(Qwen3.5) 연동 - 입력정보 기반 추가질문 생성
import json
import re

import ollama

OLLAMA_MODEL = "qwen3.5:4b"

SYSTEM_PROMPT = """당신은 경남 이주자의 생활권 탐색을 돕는 AI 상담사입니다.
사용자가 입력한 조건을 보고, 적절한 거주지역을 추천하기 위해 "꼭 필요한데 아직 모르는 정보"가 있는지 판단하세요.

규칙:
- 이미 사용자가 입력한 항목은 절대 다시 묻지 마세요.
- 추천에 실질적으로 영향을 주는 핵심 정보만 질문하세요(예: 가족 구성, 통근 수단 선호, 반려동물 유무, 자녀 교육 환경 등).
- 질문은 최대 2개까지만 생성하세요. 부족한 정보가 없다면 질문을 0개 생성해도 됩니다.
- 질문은 한국어로, 간결하고 구체적으로 작성하세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.

{"questions": ["질문1", "질문2"]}
"""


def _build_user_prompt(user_input: dict) -> str:
    lines = ["[사용자 입력 정보]"]
    for key, value in user_input.items():
        if value in (None, "", []):
            continue
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("위 정보를 바탕으로 추가 질문이 필요한지 판단하고 JSON으로 응답하세요.")
    return "\n".join(lines)


def _parse_questions(raw_text: str) -> list[str]:
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    questions = data.get("questions", [])
    if not isinstance(questions, list):
        return []

    return [str(q).strip() for q in questions if str(q).strip()][:2]


def generate_followup_questions(user_input: dict) -> list[str]:
    """사용자 입력 정보를 바탕으로 Qwen3.5에게 추가 질문(최대 2개)을 생성받는다."""
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(user_input)},
            ],
            options={"temperature": 0.3},
        )
    except Exception as exc:  # Ollama 서버 미실행 등
        raise RuntimeError(f"Ollama 모델 호출에 실패했습니다: {exc}") from exc

    raw_text = response["message"]["content"]
    return _parse_questions(raw_text)
