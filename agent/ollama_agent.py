# Ollama(Qwen3.5) 연동 - 입력정보 기반 추가질문 생성
import math
import re

import ollama

from agent import llm  # LLM_BACKEND(.env)에 따라 Ollama 또는 Claude Code CLI 호출
from agent.llm_json import extract_json_object
from analysis.scoring import CONDITION_TO_INDICATOR_CODE

OLLAMA_MODEL = "qwen3.5:4b"

SYSTEM_PROMPT = """당신은 경남 이주자의 생활권 탐색을 돕는 AI 상담사입니다.
사용자가 입력한 조건을 보고, 적절한 거주지역을 추천하기 위해 "꼭 필요한데 아직 모르는 정보"가 있는지 판단하세요.

규칙:
- 이미 사용자가 입력한 항목은 절대 다시 묻지 마세요.
- 가족 구성, 자녀 유무, 반려동물 유무, 최대 통근시간, 구체적인 주거비 금액처럼 현재
  추천 점수 계산에 쓰이는 지표(교통/의료/생활편의 시설 수)와 전혀 연결되지 않는
  질문은 만들지 마세요 - 이런 질문은 저장만 되고 실제 계산에 반영되지 않습니다.
- 중요하게 생각하는 생활조건들 사이의 우선순위(비율/가중치)를 묻는 질문은 별도
  로직에서 결정적으로 처리하므로, 당신은 그런 질문을 만들 필요가 없습니다.
- 질문은 최대 2개까지만 생성하세요. 부족한 정보가 없다면 질문을 0개 생성해도 됩니다.
- 질문은 한국어로, 간결하고 구체적으로 작성하세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.

{"questions": ["질문1", "질문2"]}
"""

# AI가 프롬프트를 무시하고 만들어낼 수 있는, 현재 계산에 쓸 수 없는 질문을 걸러내는
# 키워드 목록. 하나라도 포함되면 그 질문은 화면에 보여주지 않는다("실제 계산 가능한
# 질문으로 대체하거나 생략" 중 "생략" 방식 - 억지로 다른 문장으로 바꿔치기하지 않는다).
_GROUNDLESS_QUESTION_KEYWORDS = [
    "가족", "자녀", "자녀분", "아이", "반려동물", "애완", "통근 시간", "통근시간",
    "출퇴근 시간", "출퇴근시간", "최대 통근", "주거비", "월세", "전세", "보증금",
    "예산",
]


def _is_groundless_question(question: str) -> bool:
    return any(keyword in question for keyword in _GROUNDLESS_QUESTION_KEYWORDS)


# app.py의 "중요 생활조건" 선택지를 가중치 질문 문장에 쓸 짧은 표현으로 바꾼다.
# analysis.scoring.CONDITION_TO_INDICATOR_CODE와 같은 키 집합을 쓰되, 질문
# 문장에는 지표 코드가 아니라 사람이 읽을 짧은 조건 이름만 필요하다.
_CONDITION_DISPLAY_LABEL = {
    "교통": "교통",
    "의료": "의료",
    "생활편의(마트/편의점)": "생활편의",
}

_EXPLICIT_PERCENT_PATTERN = re.compile(r"\d{1,3}\s*%")


def _looks_like_explicit_weight_request(text: str) -> bool:
    """추가 요청사항 등에 비율처럼 보이는 표현(숫자% 2개 이상)이 있는지 가볍게
    확인한다. 이 결과만으로 "정상적인 비율"이라고 확정하지 않는다 - 퍼센트
    기호가 있다는 사실은 "interpret_weight_feedback()으로 실제 검증을 시도할
    가치가 있는가"를 판단하는 사전 필터일 뿐이다. 최종 판단(지원 지표인지,
    숫자가 유효한지, 합계가 100%인지)은 항상 interpret_weight_feedback()이
    내린다(plan_followup_questions() 참고)."""
    if not text:
        return False
    return len(_EXPLICIT_PERCENT_PATTERN.findall(text)) >= 2


def plan_weight_question(user_input: dict) -> dict | None:
    """
    사용자가 선택한 "중요 생활조건" 중 실제 계산 가능한 지표(교통/의료/생활편의)가
    2개 이상이면, "어느 쪽을 더 중요하게 생각하는지" 묻는 질문을 결정적으로(고정된
    워딩으로) 만든다. Ollama를 호출하지 않는다 - 이 질문은 나중에 사용자가 답하면
    interpret_weight_feedback()으로 정확히 파싱할 수 있어야 하므로, 표현을 AI에게
    맡기지 않는다.

    "추가 요청사항에 이미 비율이 있는지"는 이 함수가 아니라 plan_followup_questions()
    가 interpret_weight_feedback()으로 실제 검증한 뒤 판단한다(이 함수를 단독으로
    호출할 때는 계산 가능한 조건 개수만 본다) - 계산 가능한 조건이 1개 이하면
    가중치를 나눌 필요가 없으므로 None(질문 불필요).

    Returns: {"text": str, "conditions": [...], "indicator_codes": [...]} | None
    """
    conditions = user_input.get("중요 생활조건") or []
    computable_conditions = [c for c in conditions if c in CONDITION_TO_INDICATOR_CODE]

    if len(computable_conditions) < 2:
        return None

    indicator_codes = [CONDITION_TO_INDICATOR_CODE[c] for c in computable_conditions]
    labels = [_CONDITION_DISPLAY_LABEL.get(c, c) for c in computable_conditions]

    if len(labels) == 2:
        example = f"{labels[0]} 70%, {labels[1]} 30%"
    else:
        share = round(100 / len(labels))
        example = ", ".join(f"{label} {share}%" for label in labels[:-1])
        example += f", {labels[-1]} {100 - share * (len(labels) - 1)}%"

    question_text = (
        f"{', '.join(labels)} 중 어느 것을 더 중요하게 생각하시나요? 합계 100%로 "
        f"입력해 주세요. 예: {example}"
    )
    return {"text": question_text, "conditions": computable_conditions, "indicator_codes": indicator_codes}


def plan_followup_questions(user_input: dict) -> dict:
    """
    최초 입력 제출 시점에 한 번만 호출된다. generate_followup_questions()(Ollama
    호출), plan_weight_question()(결정적, Ollama 미호출), interpret_weight_feedback()
    (Ollama 호출, 기존 자연어 가중치 해석 로직 재사용)을 합쳐서 app.py가 쓸 최종
    계획을 만든다. 이 함수 자체는 Ollama 호출이 실패해도 예외를 던지지 않는다
    (요구사항: AI 추가질문 생성 실패 시에도 동일 가중치 추천으로 안전하게 진행
    가능해야 함).

    [최초 입력 "추가 요청사항"과 AI 추가질문의 가중치 확인 질문 - 우선순위]
    계산 가능한 조건(교통/의료/생활편의)이 2개 이상이고, 추가 요청사항에 비율처럼
    보이는 표현(숫자% 2개 이상)이 있으면, 그 자리에서 interpret_weight_feedback()
    으로 실제 검증한다(퍼센트 기호 개수만으로 정상 비율이라고 간주하지 않는다 -
    지원 지표인지/숫자가 유효한지/합계가 100%인지까지 전부 확인). 결과가 유효하든
    (합계 100%의 구체적 비율) 무효하든(지원하지 않는 지표 혼합, 합계 불일치 등)
    사용자가 이미 비율을 밝히려 시도한 것이므로, 같은 내용을 다시 묻는 가중치
    확인 질문(weight_question)은 만들지 않는다 - 구조적으로 initial_weight_interpretation
    과 weight_question이 동시에 존재할 수 없으므로 두 출처의 가중치가 충돌할
    여지가 없다. 이 경우를 포함해 모든 검증 결과는 app.py의 'weight_confirm'
    단계에서 미리보기 + 승인/재확인/동일 가중치 진행 선택지로 이어진다(여기서는
    해석만 하고 적용하지 않는다).

    그 외의 경우(조건이 1개 이하이거나, 비율처럼 보이는 표현이 없음)에는 기존처럼
    plan_weight_question()으로 가중치 확인 질문을 만들지 결정한다.

    [AI 추가질문(가중치 외) 생성 실패 시 안전한 대체]
    generate_followup_questions()가 Ollama 연결 실패 등으로 RuntimeError를
    던지면 여기서 잡아서 questions를 빈 목록으로 두고 ai_questions_error에 사유를
    담아 반환한다 - weight_question이나 initial_weight_interpretation은 이미
    결정돼 있으므로(Ollama 추가질문 생성과 무관하게) 그대로 유지되고, 사용자는
    이어서 가중치 확인(또는 동일 가중치)으로 최초 추천을 계속 진행할 수 있다.

    Returns:
        {
            "questions": [str, ...],                       # 화면에 보여줄 질문(가중치 질문 제외 가능, 최대 2개)
            "weight_question": {"text","conditions","indicator_codes"} | None,
            "initial_weight_interpretation": interpret_weight_feedback() 반환값 | None,
            "ai_questions_error": str | None,                # AI 추가질문 생성 실패 메시지(있으면)
        }
    """
    computable_conditions = [
        c for c in (user_input.get("중요 생활조건") or []) if c in CONDITION_TO_INDICATOR_CODE
    ]
    extra_request = user_input.get("추가 요청사항") or ""

    weight_question: dict | None = None
    initial_weight_interpretation: dict | None = None
    if len(computable_conditions) >= 2 and _looks_like_explicit_weight_request(extra_request):
        initial_weight_interpretation = interpret_weight_feedback(extra_request)
    else:
        weight_question = plan_weight_question(user_input)

    remaining_slots = 2 - (1 if weight_question else 0)
    ai_questions: list[str] = []
    ai_questions_error: str | None = None
    if remaining_slots > 0:
        try:
            candidates = generate_followup_questions(user_input)
        except RuntimeError as exc:
            ai_questions_error = str(exc)
        else:
            ai_questions = [q for q in candidates if not _is_groundless_question(q)][:remaining_slots]

    questions = ([weight_question["text"]] if weight_question else []) + ai_questions
    return {
        "questions": questions,
        "weight_question": weight_question,
        "initial_weight_interpretation": initial_weight_interpretation,
        "ai_questions_error": ai_questions_error,
    }


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
    data = extract_json_object(raw_text)
    if data is None:
        return []

    questions = data.get("questions", [])
    if not isinstance(questions, list):
        return []

    return [str(q).strip() for q in questions if str(q).strip()][:2]


def generate_followup_questions(user_input: dict) -> list[str]:
    """사용자 입력 정보를 바탕으로 Qwen3.5에게 추가 질문(최대 2개)을 생성받는다."""
    try:
        response = llm.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(user_input)},
            ],
            options={"temperature": 0.3},
            # qwen3.5의 생각 과정(thinking)이 응답 토큰을 다 써서 최종 JSON(content)이
            # 비어 오는 경우가 있어 끈다(agent/location_agent.py와 동일한 이유).
            think=False,
        )
    except Exception as exc:  # Ollama 서버 미실행 등
        raise RuntimeError(f"{llm.backend_label()} 호출에 실패했습니다: {exc}") from exc

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
    "hospital_count": "의료(의료기관 수)",
    "convenience_store_count": "생활편의(편의점 수)",
}

WEIGHT_FEEDBACK_SYSTEM_PROMPT = """당신은 창원시 생활권 비교 앱에서 사용자의 가중치 조정
요청을 해석하는 도우미입니다. 점수 계산이나 지역 추천은 당신의 역할이 아닙니다 -
오직 사용자의 문장을 아래 3개 지표에 대한 가중치 요청으로 분류하는 것만 하세요.

[다룰 수 있는 지표 - 이 3개뿐]
- bus_stop_count: 교통(버스정류장 수)
- hospital_count: 의료(의료기관 수)
- convenience_store_count: 생활편의(편의점 수)

[판단 기준 - 아래 3가지 type 중 정확히 하나를 고르세요]
1. "set_weights": 위 3개 지표 중 하나 이상에 구체적인 숫자(비율·퍼센트)가 명시된 경우.
   weights에 사용자가 말한 숫자를 그대로 넣으세요(%) - 더하거나 빼거나 비율을
   바꾸지 마세요. 합계가 100이 아니어도 계산하지 말고 사용자가 말한 숫자만 그대로
   전달하세요(합계 확인과 재질문은 이후 단계에서 처리합니다). 언급되지 않은 지표는
   weights에 넣지 마세요.
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


# 비율 합계가 100%와 이 정도 차이 안이면 "사실상 100%"로 본다(LLM의 사소한 반올림만
# 허용 - 80+20처럼 사용자가 직접 말한 숫자는 보통 정확히 더해진다).
_WEIGHT_SUM_TOLERANCE = 0.5


def _validate_weight_value(value) -> float | None:
    """
    가중치 값 하나가 "0~100 사이의 유한한 숫자"인지 검사해서 float을 돌려주고,
    아니면 None을 돌려준다. bool은 int의 서브클래스라 float(True)==1.0으로
    조용히 통과해버리므로 명시적으로 막는다. NaN/Infinity/-Infinity, 음수,
    100 초과는 전부 거부한다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
    else:
        return None

    if not math.isfinite(numeric):  # NaN, inf, -inf 전부 차단 ("Infinity"/"NaN" 문자열도 float()은 통과시키므로 필수)
        return None
    if numeric < 0 or numeric > 100:
        return None
    return numeric


def _parse_weight_feedback(raw_text: str) -> dict:
    """
    Ollama 원문 응답을 파싱하고 검증한다. 사용자 의도를 임의로 바꾸지 않는 것을
    최우선으로 한다 - 지원하지 않는 지표가 섞여 있거나, 값이 유효하지 않거나,
    비율 합계가 100%가 아니면 "일부만" 조용히 적용하지 않고 전체를 되묻거나
    거부한다.

    1) 지원하지 않는 지표(bus_stop_count/hospital_count/convenience_store_count
       외)가 weights에 하나라도 있으면 -> 전체를 "unsupported"로 돌려 재입력을
       유도한다(예: "의료 60%, 주거비 40%"가 의료 60%만으로 조용히 바뀌는 것을
       막기 위함).
    2) 값이 하나라도 유효하지 않으면(NaN/Infinity/음수/100 초과/bool/숫자 아님)
       -> 전체를 invalid_response로 거부한다.
    3) 유효한 값이 전부 0이면 -> invalid_response.
    4) 유효한 값의 합계가 100%(±0.5%p)에서 벗어나면 -> 임의로 정규화하지 않고
       "ask_clarification"으로 돌려 사용자에게 재확인을 요청한다(모자라면 "나머지를
       어디에 둘지", 넘치면 "합계가 X%로 100%를 넘는다"는 안내).
    """
    if "{" not in (raw_text or ""):
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI 응답에서 JSON을 찾지 못했습니다.",
        }

    data = extract_json_object(raw_text)
    if data is None:
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "AI 응답이 올바른 JSON 형식이 아닙니다.",
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

    # 1) 지원하지 않는 지표가 섞여 있으면 일부만 적용하지 않고 전체를 되묻는다.
    unsupported_codes = [c for c in raw_weights if c not in WEIGHT_FEEDBACK_SUPPORTED_INDICATORS]
    if unsupported_codes:
        return {
            "status": "ok",
            "type": "unsupported",
            "weights": None,
            "message": (
                f"요청하신 조건 중 '{', '.join(unsupported_codes)}'에 해당하는 데이터는 "
                "아직 확보되지 않아 요청하신 비율 구성을 그대로 적용할 수 없습니다. "
                "교통·의료·생활편의 중에서만 다시 비율을 지정해 주세요."
            ),
        }

    # 2) 값 하나라도 유효하지 않으면 전체를 거부한다(일부만 조용히 쓰지 않는다).
    validated_weights: dict[str, float] = {}
    for code, value in raw_weights.items():
        numeric = _validate_weight_value(value)
        if numeric is None:
            return {
                "status": "invalid_response",
                "type": None,
                "weights": None,
                "message": f"'{WEIGHT_FEEDBACK_SUPPORTED_INDICATORS[code]}'에 지정된 값이 올바른 0~100 사이 숫자가 아닙니다.",
            }
        validated_weights[code] = numeric

    # 3) 전부 0이면 거부한다.
    if all(v == 0 for v in validated_weights.values()):
        return {
            "status": "invalid_response",
            "type": None,
            "weights": None,
            "message": "요청하신 가중치가 전부 0입니다. 하나 이상에 0보다 큰 비율을 지정해 주세요.",
        }

    # 4) 합계가 100%에서 벗어나면 임의로 정규화하지 않고 되묻는다.
    weight_sum = sum(validated_weights.values())
    breakdown = ", ".join(
        f"{WEIGHT_FEEDBACK_SUPPORTED_INDICATORS[c]} {v:g}%" for c, v in validated_weights.items()
    )
    if weight_sum > 100 + _WEIGHT_SUM_TOLERANCE:
        return {
            "status": "ok",
            "type": "ask_clarification",
            "weights": None,
            "message": (
                f"입력하신 비율({breakdown})의 합이 {weight_sum:g}%로 100%를 넘습니다. "
                "비율을 다시 확인해서 합이 100%가 되도록 말씀해 주세요."
            ),
        }
    if weight_sum < 100 - _WEIGHT_SUM_TOLERANCE:
        remaining = 100 - weight_sum
        return {
            "status": "ok",
            "type": "ask_clarification",
            "weights": None,
            "message": (
                f"입력하신 비율({breakdown})의 합이 {weight_sum:g}%로 100%가 되지 않습니다. "
                f"나머지 {remaining:g}%를 어디에 둘지 알려주세요(그대로 0%로 두시려면 "
                "'나머지는 0%'처럼 말씀해 주세요)."
            ),
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
        response = llm.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": WEIGHT_FEEDBACK_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            options={"temperature": 0.0},
            think=False,  # generate_followup_questions()와 동일한 이유
        )
    except Exception as exc:  # Ollama 서버 미실행 등 - 수동 슬라이더는 계속 쓸 수 있어야 하므로 예외를 던지지 않는다
        return {
            "status": "ollama_error",
            "type": None,
            "weights": None,
            "message": f"{llm.backend_label()} 호출에 실패했습니다: {exc}",
        }

    raw_text = response["message"]["content"]
    return _parse_weight_feedback(raw_text)
