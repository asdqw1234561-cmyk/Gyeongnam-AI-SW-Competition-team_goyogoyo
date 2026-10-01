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


# ---------------------------------------------------------------------------
# 자연어 가중치 피드백 해석 ("의료 80%, 교통 20%로 바꿔줘" 등)
# ---------------------------------------------------------------------------
# 이 함수가 하는 일은 "자연어 -> 구조화된 가중치 제안"뿐이다. 점수 계산·순위
# 결정은 절대 하지 않으며, 반환값은 사용자가 승인한 뒤 app.py가
# analysis.scoring.compute_region_scores_from_weights()에 그대로 넘긴다.
WEIGHT_FEEDBACK_SUPPORTED_INDICATORS = {
    "bus_stop_count": "교통(버스정류장 수)",
    "hospital_count": "의료(병원 수)",
    "convenience_store_count": "생활편의(편의점 수)",
}

WEIGHT_FEEDBACK_SYSTEM_PROMPT = """당신은 창원시 생활권 비교 앱에서 사용자의 가중치 조정
요청을 해석하는 도우미입니다. 점수 계산이나 지역 추천은 당신의 역할이 아닙니다 -
오직 사용자의 문장을 아래 3개 지표에 대한 가중치 요청으로 분류하는 것만 하세요.

[다룰 수 있는 지표 - 이 3개뿐]
- bus_stop_count: 교통(버스정류장 수)
- hospital_count: 의료(병원 수)
- convenience_store_count: 생활편의(편의점 수)

[판단 기준 - 아래 3가지 type 중 정확히 하나를 고르세요]
1. "set_weights": 위 3개 지표 중 하나 이상에 구체적인 숫자(비율·퍼센트)가 명시된 경우.
   weights에 사용자가 말한 숫자를 그대로 넣으세요(%). 합계가 100이 아니어도 그대로
   두세요(나중에 자동으로 정규화됩니다). 언급되지 않은 지표는 weights에 넣지 마세요.
2. "ask_clarification": "의료가 더 중요해", "교통 위주로 봐줘"처럼 방향성만 있고
   구체적인 숫자가 없는 경우. 절대 숫자를 임의로 만들어내지 말고, message에 몇
   %로 할지 되묻는 한국어 질문을 작성하세요.
3. "unsupported": 사용자가 요청한 것이 위 3개 지표에 전혀 해당하지 않는 경우
   (예: 주거비/월세/전세, 통근시간/대중교통 소요시간, 교육, 안전, 자연환경,
   문화시설, 대형마트, 응급실 등). message에 "현재 해당 데이터를 확보하지
   못했다"는 한국어 안내를 작성하세요. 시설 수·이동시간·주거비·추천 점수 등을
   절대로 지어내지 마세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.

{"type": "set_weights", "weights": {"hospital_count": 80, "bus_stop_count": 20}, "message": null}
또는
{"type": "ask_clarification", "weights": null, "message": "의료를 몇 %로 둘까요? 예: 의료 70%, 교통 30%"}
또는
{"type": "unsupported", "weights": null, "message": "주거비(월세·전세) 데이터는 아직 확보되지 않아 가중치에 반영할 수 없습니다."}
"""


def _parse_weight_feedback(raw_text: str) -> dict:
    """
    Ollama 원문 응답을 파싱하고 검증한다. 허용되지 않은 지표 코드나 숫자가 아닌
    값은 조용히 버리고(해당 항목만 거부), 유효한 항목이 하나도 안 남으면 전체를
    invalid_response로 처리한다. JSON 자체가 깨졌거나 type이 알 수 없는 값이면
    즉시 invalid_response.
    """
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI 응답에서 JSON을 찾지 못했습니다.",
        }

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI 응답이 올바른 JSON 형식이 아닙니다.",
        }

    if not isinstance(data, dict):
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI 응답 형식이 올바르지 않습니다.",
        }

    resp_type = data.get("type")
    if resp_type not in ("set_weights", "ask_clarification", "unsupported"):
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI 응답의 type 값이 올바르지 않습니다.",
        }

    raw_message = data.get("message")
    message = str(raw_message).strip() if raw_message else None

    if resp_type != "set_weights":
        return {"status": "ok", "type": resp_type, "weights": None, "message": message}

    raw_weights = data.get("weights")
    if not isinstance(raw_weights, dict) or not raw_weights:
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI가 구체적인 가중치를 지정하지 않았습니다.",
        }

    validated_weights: dict[str, float] = {}
    for code, value in raw_weights.items():
        if code not in WEIGHT_FEEDBACK_SUPPORTED_INDICATORS:
            continue  # 허용되지 않은 지표 코드는 그 항목만 조용히 거부
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue  # 숫자가 아닌 값도 그 항목만 거부
        if numeric < 0:
            continue
        validated_weights[code] = numeric

    if not validated_weights:
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI가 제시한 가중치에 유효한 지표·숫자가 없습니다.",
        }

    return {"status": "ok", "type": "set_weights", "weights": validated_weights, "message": message}


def interpret_weight_feedback(user_text: str) -> dict:
    """
    사용자의 자연어 피드백(예: "의료 80%, 교통 20%로 비교해줘")을 로컬
    Ollama(qwen3.5:4b)로 해석해, 가중치 조정 UI에서 쓸 구조화된 결과로 돌려준다.

    이 함수는 조건 해석만 하고 점수 계산은 절대 하지 않는다 - 반환되는 weights는
    사용자가 승인한 뒤 analysis.scoring.compute_region_scores_from_weights()에
    그대로 전달될 뿐이다.

    Returns:
        {
            "status": "ok" | "ollama_error" | "invalid_response",
            "type": "set_weights" | "ask_clarification" | "unsupported" | None,
            "weights": {indicator_code: 0 이상 숫자, ...} | None,  # type=="set_weights"일 때만
            "message": str | None,
        }
        status != "ok"면 type/weights는 항상 None이고, message에 사용자에게 보여줄
        문구(Ollama 연결 실패, JSON 파싱 실패, 허용되지 않은 지표/값 등)가 들어있다.
    """
    text = (user_text or "").strip()
    if not text:
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "입력한 내용이 없습니다.",
        }

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": WEIGHT_FEEDBACK_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            options={"temperature": 0.0},
        )
    except Exception as exc:  # Ollama 서버 미실행 등 - 수동 슬라이더는 계속 쓸 수 있어야 하므로 예외를 던지지 않는다
        return {
            "status": "ollama_error",
            "type": None,
            "weights": None,
            "message": f"Ollama 호출에 실패했습니다: {exc}",
        }

    raw_text = response["message"]["content"]
    return _parse_weight_feedback(raw_text)
