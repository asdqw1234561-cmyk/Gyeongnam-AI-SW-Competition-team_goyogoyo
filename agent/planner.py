# Ollama(Qwen3.5) 연동 - 분석 계획 수립 및 허용된 도구 실행 (Agent Planner)
"""
사용자가 승인한 가중치를 바탕으로 "어떤 지표를 조회하고 어떻게 점수를 계산할지"를
로컬 Ollama(qwen3.5:4b)가 계획하고, Python이 그 계획을 검증한 뒤 허용된 도구만
실제로 실행하는 작은 Agent Workflow.

[설계 원칙]
- AI는 계획(어떤 도구를, 어떤 순서로, 어떤 인자로 호출할지)만 제안한다. 실제 데이터
  조회와 점수 계산은 전부 기존 함수(services.region_data.get_all_changwon_regions,
  analysis.scoring의 collect_confirmed_indicator/compute_region_scores_from_weights)
  를 그대로 재사용하는 Python 도구 함수가 수행한다 - AI가 시설 수·점수·통근시간·
  주거비 등의 숫자를 직접 만들어낼 방법이 구조적으로 없다.
- AI가 제안한 가중치는 절대 신뢰하지 않는다. calculate_region_scores 도구는 항상
  run_agent_plan()에 전달된 "승인된 가중치"(approved_weights)로 덮어써서 실행한다 -
  AI가 다른 숫자를 적더라도 그 숫자는 쓰이지 않는다(검증 단계에서 무시했다는 사실만
  기록에 남긴다).
- 조회(get_region_indicators)와 계산(calculate_region_scores)은 run_agent_plan()
  안에서 딱 한 번 조회한 같은 regions 스냅샷(get_all_changwon_regions())을 공유한다
  (analysis/simulation.py가 먼저 도입한 regions 선택적 주입 기능을 그대로 재사용) -
  "조회한 자료와 점수 계산에 쓴 자료가 다르다"는 불일치가 날 수 없다.
- Ollama 호출(call_planner)이 실패하거나 계획이 검증을 통과하지 못하면, 같은 도구들을
  고정된 순서(get_available_indicators -> get_region_indicators -> calculate_region_scores)
  로 Python이 직접 실행하는 "기본 절차"로 안전하게 전환한다 - 추천 기능 자체가
  멈추는 일은 없다.
"""

from __future__ import annotations

import ollama

from agent.llm_json import extract_json_object, sanitize_goals, sanitize_unsupported_requests
from agent import llm  # LLM_BACKEND(.env)에 따라 Ollama 또는 Claude Code CLI 호출
from agent import planner_loop  # 점수 계산 결과 관찰 -> 설명/추가 조회/가정 계산 판단 반복

from analysis.candidates import build_candidate_set
from analysis.scoring import (
    VALID_SCORABLE_INDICATOR_CODES,
    collect_confirmed_indicator,
    compute_region_scores,
    compute_region_scores_from_weights,
)
from services.region_data import get_all_changwon_regions

OLLAMA_MODEL = "qwen3.5:4b"

# ---------------------------------------------------------------------------
# 허용된 도구 목록 - AI는 이 이름만 고를 수 있고, 각 함수는 기존 조회/계산 함수를
# 그대로 감싸기만 한다(새 계산식·새 데이터 조회 경로를 추가하지 않는다).
# ---------------------------------------------------------------------------
ALLOWED_TOOLS: tuple[str, ...] = (
    "get_available_indicators",
    "get_region_indicators",
    "calculate_region_scores",
)

TOOL_LABELS: dict[str, str] = {
    "get_available_indicators": "지표 확보 여부 조회",
    "get_region_indicators": "지역별 지표 수치 조회",
    "calculate_region_scores": "가중치 기반 점수 계산",
}


def tool_get_available_indicators(regions: list[dict]) -> dict[str, bool]:
    """VALID_SCORABLE_INDICATOR_CODES(교통/의료/생활편의) 각각이 창원시 5개 구
    전부 data_status=='확보'인지. 새 판정 로직 없이 기존 collect_confirmed_indicator
    를 그대로 재사용한다."""
    return {
        code: collect_confirmed_indicator(regions, code) is not None
        for code in VALID_SCORABLE_INDICATOR_CODES
    }


def tool_get_region_indicators(regions: list[dict], indicator_codes: list[str]) -> dict[str, dict]:
    """확보된 지표만 {indicator_code: {"indicator_name", "values": {region_id: value}}}
    로 돌려준다. 허용되지 않거나 미확보인 코드는 조용히 제외한다(검증은 이미
    validate_and_normalize_plan에서 끝났어야 하지만, 도구 자체도 방어적으로 한 번 더
    걸러 미확보 지표가 조회 결과에 섞여 들어가지 않게 한다)."""
    result: dict[str, dict] = {}
    for code in indicator_codes:
        if code not in VALID_SCORABLE_INDICATOR_CODES:
            continue
        collected = collect_confirmed_indicator(regions, code)
        if collected is None:
            continue
        raw_values, indicator_name = collected
        result[code] = {"indicator_name": indicator_name, "values": raw_values}
    return result


def tool_calculate_region_scores(regions: list[dict], weights: dict[str, float], candidate_count: int) -> dict:
    """새 점수 계산식을 만들지 않고 기존 compute_region_scores_from_weights()를
    그대로 호출한다."""
    return compute_region_scores_from_weights(weights, candidate_count, regions=regions)


# ---------------------------------------------------------------------------
# 계획 수립 (Ollama 호출)
# ---------------------------------------------------------------------------
PLANNER_SYSTEM_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 분석 계획 수립 도우미입니다.
직접 데이터를 조회하거나 점수를 계산하지 않습니다 - 아래 3개의 Python 도구 중 어떤 것을,
어떤 순서로, 어떤 인자로 호출할지 "계획"만 세우세요. 실제 실행과 검증은 Python이 합니다.

[사용 가능한 도구 - 이 3개뿐, 그 외 도구는 존재하지 않습니다]
1. get_available_indicators: 창원시 5개 구 전체에서 확보된 지표를 확인합니다. 인자 없음.
2. get_region_indicators: 지정한 지표(indicator_codes)의 창원시 5개 구 실제 수치를 조회합니다.
   indicator_codes는 아래 "지원 지표" 목록의 코드만 쓸 수 있습니다.
3. calculate_region_scores: 승인된 가중치로 창원시 5개 구를 상대 비교합니다. weights 필드에
   아래 "승인된 가중치"를 그대로 옮겨 적으세요 - 실제 계산은 Python이 그 값으로만 수행하며,
   다른 숫자를 적어도 무시됩니다. 숫자를 새로 만들거나 바꾸지 마세요.

[지원 지표 - 이 3개뿐, 그 외 지표는 존재하지 않습니다]
- bus_stop_count: 교통(버스정류장 수)
- hospital_count: 의료(의료기관 수)
- convenience_store_count: 생활편의(편의점 수)

[규칙]
- 입력으로 주어지는 "승인된 가중치"는 이미 사용자가 확정한 값입니다. 당신은 이 값을 바꾸거나
  새로 만들 수 없습니다.
- 입력으로 주어지는 "확보 상태"에서 미확보로 표시된 지표는 조회·계산에 쓸 수 없습니다.
- 사용자가 중요하게 생각하는 조건 중 위 3개 지표에 대응하지 않는 것(교육/안전/자연환경/문화시설/
  주거비/통근시간 등)은 unsupported_requests에 그 이름과 짧은 이유만 적으세요 - 절대로 시설 수,
  추천 점수, 통근시간, 주거비 등 실제 수치를 만들어내지 마세요.
- 추론 과정을 출력하지 마세요. 각 도구 호출의 reason은 한 문장으로 간단히만 적으세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.

{"goals": ["교통 분석", "의료 분석"], "tool_calls": [{"tool": "get_available_indicators", "reason": "확보된 지표 확인"}, {"tool": "get_region_indicators", "indicator_codes": ["bus_stop_count", "hospital_count"], "reason": "교통·의료 실제 수치 조회"}, {"tool": "calculate_region_scores", "weights": {"bus_stop_count": 70, "hospital_count": 30}, "reason": "승인된 가중치로 5개 구 비교"}], "unsupported_requests": [{"request": "주거비", "reason": "지원 지표가 아님"}]}
"""


def _build_planner_user_prompt(
    selected_conditions: list[str],
    approved_weights: dict[str, float],
    desired_region: str,
    available_indicators: dict[str, bool],
) -> str:
    lines = [
        "[사용자 요청 정보]",
        f"- 희망지역: {desired_region or '(미지정)'}",
        f"- 선택한 중요 생활조건: {selected_conditions or '(없음)'}",
        f"- 승인된 가중치(그대로 옮겨 적을 것, 변경 금지): {approved_weights}",
        f"- 지표별 확보 상태: {available_indicators}",
        "",
        "위 정보를 바탕으로 분석 계획을 JSON으로 작성하세요.",
    ]
    return "\n".join(lines)


def call_planner(
    selected_conditions: list[str],
    approved_weights: dict[str, float],
    desired_region: str,
    available_indicators: dict[str, bool],
) -> dict:
    """로컬 Ollama(qwen3.5:4b)를 호출해 분석 계획(JSON)을 받는다.

    Ollama 연결 실패와 JSON 해석 실패를 모두 RuntimeError 하나로 통일한다 -
    run_agent_plan()은 이 예외 하나만 잡으면 "기본 절차" 폴백으로 안전하게
    전환할 수 있다(에러 종류별로 상태를 분기하지 않아 흐름이 단순해진다)."""
    try:
        response = llm.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_planner_user_prompt(
                        selected_conditions, approved_weights, desired_region, available_indicators
                    ),
                },
            ],
            options={"temperature": 0.0},
            # qwen3.5의 생각 과정(thinking)이 응답 토큰을 다 써서 최종 JSON(content)이
            # 비어 오는 경우가 있어 끈다(agent/location_agent.py와 동일한 이유).
            think=False,
        )
    except Exception as exc:  # Ollama 서버 미실행 등
        raise RuntimeError(f"{llm.backend_label()} 분석 계획 호출에 실패했습니다: {exc}") from exc

    raw_text = response["message"]["content"]
    plan = extract_json_object(raw_text)
    if plan is None:
        raise RuntimeError("AI가 반환한 분석 계획을 JSON으로 해석하지 못했습니다.")
    return plan


# ---------------------------------------------------------------------------
# 계획 검증 - LLM의 계획을 그대로 신뢰하지 않는다
# ---------------------------------------------------------------------------
def _weights_match(a: dict, b: dict, tol: float = 0.5) -> bool:
    try:
        if set(a) != set(b):
            return False
        return all(abs(float(a[k]) - float(b[k])) <= tol for k in a)
    except (TypeError, ValueError):
        return False


def validate_and_normalize_plan(
    plan: object,
    approved_weights: dict[str, float],
    available_indicator_codes: set[str],
) -> dict:
    """
    AI가 제안한 계획(plan)을 검증해 Python이 실제로 실행할 수 있는 형태로 정규화한다.

    확인하는 것:
    1. plan이 올바른 구조(dict + 비어있지 않은 tool_calls 리스트)인지
    2. 각 tool_call의 "tool"이 ALLOWED_TOOLS 안에 있는지(없으면 전체 계획 거부)
    3. get_region_indicators의 indicator_codes가 전부 확보된 지원 지표인지
       (허용되지 않거나 미확보인 코드가 하나라도 있으면 전체 계획 거부)
    4. calculate_region_scores의 weights는 항상 approved_weights로 강제 치환한다
       (AI가 다른 값을 제안했다면 무시했다는 사실만 notes에 남긴다 - "사용자 승인
       없이 가중치를 변경"하는 일은 구조적으로 불가능하다)
    5. calculate_region_scores는 최대 1번만 허용하고, 그 앞에 필요한 지표의
       get_region_indicators 조회가 빠져 있으면 Python이 자동으로 보완한다
       (필요한 조회 작업이 누락되지 않도록)
    6. approved_weights가 있는데 계획에 calculate_region_scores 자체가 없으면
       Python이 추가한다(계산 작업 누락 방지)

    Returns:
        {"status": "ok", "tool_calls": [...], "notes": [...]} | {"status": "rejected", "reason": str}
    """
    if not isinstance(plan, dict):
        return {"status": "rejected", "reason": "AI 응답이 JSON 객체가 아닙니다."}

    raw_calls = plan.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return {"status": "rejected", "reason": "AI가 실행할 도구를 하나도 제안하지 않았습니다."}
    if len(raw_calls) > 6:
        return {"status": "rejected", "reason": "제안된 도구 호출 수가 비정상적으로 많습니다."}

    notes: list[str] = []
    normalized: list[dict] = []

    for i, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            return {"status": "rejected", "reason": f"{i + 1}번째 도구 호출 형식이 올바르지 않습니다."}

        tool = call.get("tool")
        if tool not in ALLOWED_TOOLS:
            return {"status": "rejected", "reason": f"허용되지 않은 도구 '{tool}'입니다."}

        reason = str(call.get("reason") or "").strip()[:200]

        if tool == "get_available_indicators":
            normalized.append({"tool": tool, "reason": reason})

        elif tool == "get_region_indicators":
            codes = call.get("indicator_codes")
            if not isinstance(codes, list) or not codes:
                return {"status": "rejected", "reason": "get_region_indicators에 조회할 지표가 지정되지 않았습니다."}
            codes = list(dict.fromkeys(codes))  # 순서 보존 중복 제거
            invalid = [c for c in codes if c not in available_indicator_codes]
            if invalid:
                return {
                    "status": "rejected",
                    "reason": f"허용되지 않거나 미확보된 지표 {invalid}는 조회할 수 없습니다.",
                }
            normalized.append({"tool": tool, "indicator_codes": codes, "reason": reason})

        elif tool == "calculate_region_scores":
            proposed_weights = call.get("weights")
            if proposed_weights is not None and not _weights_match(proposed_weights, approved_weights):
                notes.append("AI가 제안한 가중치를 무시하고 사용자가 승인한 가중치를 그대로 사용했습니다.")
            # 항상 승인된 가중치로 강제 치환한다 - AI 제안값은 절대 쓰지 않는다.
            normalized.append({"tool": tool, "weights": dict(approved_weights), "reason": reason})

    calc_positions = [i for i, c in enumerate(normalized) if c["tool"] == "calculate_region_scores"]
    if len(calc_positions) > 1:
        return {"status": "rejected", "reason": "calculate_region_scores는 한 번만 호출할 수 있습니다."}

    if calc_positions:
        calc_i = calc_positions[0]
        covered: set[str] = set()
        for c in normalized[:calc_i]:
            if c["tool"] == "get_region_indicators":
                covered.update(c["indicator_codes"])
        missing = sorted(set(approved_weights) - covered)
        if missing:
            normalized.insert(
                calc_i,
                {
                    "tool": "get_region_indicators",
                    "indicator_codes": missing,
                    "reason": "(Python 보완) 점수 계산에 필요한데 누락된 지표 조회를 자동으로 추가함",
                },
            )
            notes.append(f"AI 계획에 누락된 조회 작업을 자동으로 보완했습니다: {missing}")
    elif approved_weights:
        normalized.append(
            {
                "tool": "calculate_region_scores",
                "weights": dict(approved_weights),
                "reason": "(Python 보완) 승인된 가중치로 점수 계산이 누락되어 자동으로 추가함",
            }
        )
        notes.append("AI 계획에 점수 계산 단계가 빠져 있어 자동으로 추가했습니다.")

    return {"status": "ok", "tool_calls": normalized, "notes": notes}


# ---------------------------------------------------------------------------
# 도구 실행 - 검증을 통과한 계획만 실행한다
# ---------------------------------------------------------------------------
def _execute_tool_calls(regions: list[dict], tool_calls: list[dict], candidate_count: int) -> dict:
    """검증된 tool_calls를 순서대로 실제 실행한다. 반환하는 log는 '실제로 호출한
    도구'만 담으므로, 화면 표시 시 호출하지 않은 도구를 호출한 것처럼 보여줄
    위험이 없다."""
    log: list[dict] = []
    score_result: dict | None = None
    for call in tool_calls:
        tool = call["tool"]
        entry: dict = {"tool": tool, "reason": call.get("reason", "")}
        try:
            if tool == "get_available_indicators":
                entry["result"] = tool_get_available_indicators(regions)
            elif tool == "get_region_indicators":
                data = tool_get_region_indicators(regions, call["indicator_codes"])
                entry["indicator_codes"] = call["indicator_codes"]
                entry["confirmed_indicator_codes"] = sorted(data)
                entry["result"] = {code: v["values"] for code, v in data.items()}
            elif tool == "calculate_region_scores":
                score_result = tool_calculate_region_scores(regions, call["weights"], candidate_count)
                entry["weights"] = call["weights"]
                entry["result"] = {"status": score_result["status"]}
            entry["executed"] = True
            entry["error"] = None
        except Exception as exc:  # 허용 도구는 안정적이어야 하지만 방어적으로 한 번 더 감싼다
            entry["executed"] = False
            entry["error"] = str(exc)
        log.append(entry)
    return {"log": log, "score_result": score_result}


def _default_tool_calls(approved_weights: dict[str, float]) -> list[dict]:
    """AI 계획 없이(Ollama 실패 또는 계획 검증 실패) Python이 직접 실행하는 고정
    순서 - 기존에 _finalize_initial_recommendation()이 하던 일과 동일한 결과를
    내도록, 승인된 가중치로 바로 calculate_region_scores까지 연결한다."""
    calls = [{"tool": "get_available_indicators", "reason": "(기본 절차) 확보된 지표 확인"}]
    if approved_weights:
        calls.append(
            {
                "tool": "get_region_indicators",
                "indicator_codes": sorted(approved_weights),
                "reason": "(기본 절차) 점수 계산에 필요한 지표 조회",
            }
        )
        calls.append(
            {
                "tool": "calculate_region_scores",
                "weights": dict(approved_weights),
                "reason": "(기본 절차) 승인된 가중치로 점수 계산",
            }
        )
    return calls


# ---------------------------------------------------------------------------
# 최상위 진입점
# ---------------------------------------------------------------------------
def run_agent_plan(
    selected_conditions: list[str],
    confirmed_weights: dict[str, float] | None,
    desired_region: str,
    candidate_count: int,
    unscored_inputs: list[str] | None = None,
) -> dict:
    """
    최초 추천 계산의 Agent 진입점. app.py의 _finalize_initial_recommendation()이
    이 함수 하나만 호출하면 된다.

    - regions는 이 함수 안에서 딱 한 번만 조회한다(get_all_changwon_regions()) -
      이후 모든 조회·계산 도구가 같은 스냅샷을 공유하므로 "조회한 데이터와 점수
      계산에 쓴 데이터가 다르다"는 불일치가 구조적으로 생기지 않는다.
    - confirmed_weights가 있으면 그대로 "승인된 가중치"로 쓴다. 없으면(가중치
      질문이 없었거나 동일 가중치로 진행하기로 한 경우) 기존 compute_region_scores()
      를 같은 regions 스냅샷으로 한 번 실행해 그 결과의 used_conditions에서
      "조건별 동일 가중치"를 그대로 뽑아 승인된 가중치로 취급한다.
    - AI 계획 호출이 실패하거나 검증을 통과하지 못하면 "기본 절차"(결정적 도구
      순서)로 안전하게 전환하고, 그 사실을 반환값의 mode/planner_error/notes에
      남긴다 - 이때도 calculate_region_scores는 정상적으로 실행되어 추천 결과를
      만든다(추천 기능 자체는 멈추지 않는다).

    Returns:
        {
            "mode": "ai_planned" | "fallback_default",
            "goals": [str, ...],                         # AI가 식별한 분석 목표(표시용)
            "unsupported_requests": [{"request","reason"}, ...],
            "approved_weights": {indicator_code: float, ...},
            "weights_source": "user_confirmed" | "equal_weight",
            "available_indicators": {indicator_code: bool, ...},
            "planned_tool_calls": [...] | None,   # AI가 "제안"한 원본(검증 전) - 표시 전용, 실행 여부와 무관
            "executed_tool_calls": [...],          # Python이 실제로 실행한 도구 호출 로그만
            "notes": [str, ...],                   # 검증 보완/가중치 무시 등 Python의 안내
            "planner_error": str | None,           # Ollama 실패/JSON 해석 실패 사유
            "score_result": compute_region_scores_from_weights() 반환값과 동일한 형식,
            "candidate_review": analysis.candidates.build_candidate_set() 반환값 -
                점수 계산 뒤 후보 생성(최적·균형·대안) + Critic 점검. 결정적 계산이며
                score_result의 순위·점수는 바꾸지 않는다. unscored_inputs(입력했지만
                대응 데이터가 없는 항목 이름)는 Critic의 데이터 커버리지 점검에만 쓴다.
        }
    """
    regions = get_all_changwon_regions()

    if confirmed_weights:
        approved_weights = {k: float(v) for k, v in confirmed_weights.items() if v}
        weights_source = "user_confirmed"
    else:
        baseline = compute_region_scores(selected_conditions, candidate_count, regions=regions)
        approved_weights = {
            uc["indicator_code"]: round(uc["weight"] * 100, 4) for uc in baseline.get("used_conditions", [])
        }
        weights_source = "equal_weight"

    available_indicators = tool_get_available_indicators(regions)
    available_codes = {code for code, ok in available_indicators.items() if ok}

    planner_error: str | None = None
    plan: dict | None = None
    try:
        plan = call_planner(selected_conditions, approved_weights, desired_region, available_indicators)
    except RuntimeError as exc:
        planner_error = str(exc)

    if planner_error is None:
        validated = validate_and_normalize_plan(plan, approved_weights, available_codes)
    else:
        validated = {"status": "rejected", "reason": planner_error}

    notes: list[str]
    if validated["status"] == "ok":
        mode = "ai_planned"
        tool_calls = validated["tool_calls"]
        notes = validated["notes"]
    else:
        mode = "fallback_default"
        tool_calls = _default_tool_calls(approved_weights)
        notes = []
        if planner_error is None:
            notes.append(f"AI 계획을 검증하지 못해 기본 절차로 전환했습니다: {validated['reason']}")

    executed = _execute_tool_calls(regions, tool_calls, candidate_count)
    score_result = executed["score_result"]
    if score_result is None:
        # 승인된 가중치가 아예 없었던 경우(계산 가능한 조건 0개) - calculate_region_scores가
        # 계획에 없었다는 뜻이므로, 기존과 동일한 "no_usable_conditions" 형태로 안전 반환.
        score_result = compute_region_scores_from_weights(approved_weights, candidate_count, regions=regions)

    # 후보 생성 -> Critic: 종합점수 1위 하나가 아니라 성격이 다른 후보(최적·균형·대안)를
    # 고르고, 근소차·지배 관계·한 축 의존·쏠림·데이터 공백을 Python이 결정적으로 점검한다.
    candidate_review = build_candidate_set(score_result, unscored_inputs)

    # 관찰 -> 판단 -> 행동 반복: AI 계획으로 점수를 계산한 경우에만 결과를 AI에게 보여주고
    # 설명 작성 / 참고 지표 추가 조회 / 가중치 가정 계산 중 하나를 판단하게 한다.
    # 실제 추천(score_result)은 승인된 가중치 결과 그대로이며 이 단계에서 바뀌지 않는다.
    # 자세한 안전장치는 agent/planner_loop.py 참고.
    review: dict = {"agent_steps": [], "review_tool_calls": [], "what_if_results": [],
                    "extra_indicators": {}, "final_answer": None, "review_error": None}
    if mode == "ai_planned" and score_result.get("status") == "ok":
        review = planner_loop.run_review_loop(
            regions=regions, score_result=score_result, approved_weights=approved_weights,
            available_indicators=available_indicators, selected_conditions=selected_conditions,
            candidate_count=candidate_count, model=OLLAMA_MODEL,
            get_indicators_fn=tool_get_region_indicators,
            simulate_fn=compute_region_scores_from_weights,
        )

    return {
        "mode": mode,
        "goals": sanitize_goals(plan.get("goals")) if isinstance(plan, dict) else [],
        "unsupported_requests": sanitize_unsupported_requests(
            plan.get("unsupported_requests") if isinstance(plan, dict) else None
        ),
        "approved_weights": approved_weights,
        "weights_source": weights_source,
        "available_indicators": available_indicators,
        "planned_tool_calls": plan.get("tool_calls") if isinstance(plan, dict) else None,
        "executed_tool_calls": executed["log"],
        "notes": notes,
        "planner_error": planner_error,
        "score_result": score_result,
        "candidate_review": candidate_review,
        # 이하 결과 검토 단계(2회차 이후) - 표시 전용, score_result 에는 영향 없음
        "agent_steps": review["agent_steps"],
        "review_tool_calls": review["review_tool_calls"],
        "what_if_results": review["what_if_results"],
        "extra_indicators": review["extra_indicators"],
        "final_answer": review["final_answer"],
        "review_error": review["review_error"],
    }
