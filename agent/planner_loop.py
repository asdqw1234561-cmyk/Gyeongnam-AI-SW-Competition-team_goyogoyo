# 최초 추천(planner) 결과 관찰 -> 판단 -> 행동 반복
"""
agent/planner.py 가 승인된 가중치로 창원시 5개 구 점수를 계산한 "뒤"에 붙는 단계다.
AI가 계산 결과를 보고 다음 중 하나를 고른다.

    answer           추천 결과 설명 작성 (왜 1위인지, 주의할 점 등)
    call_tools       설명에 필요한 정보를 더 모은다
        - get_region_indicators : 가중치에 없는 지표(예: 의료기관 수)도 참고용으로 조회
        - simulate_weights      : "가중치를 이렇게 바꾸면 순위가 달라지는가" 가정 계산
                                  (재탐색 판단). 결과는 참고 정보로만 보여주고
                                  실제 추천(score_result)에는 절대 반영하지 않는다.

[안전장치]
    - 실제 추천 점수는 사용자가 승인한 가중치로 이미 계산된 값 그대로다. 이 루프는 그것을
      바꿀 수 없다(가정 계산은 별도 결과로만 저장).
    - 가정 계산도 기존 compute_region_scores_from_weights() 를 같은 regions 스냅샷으로
      부르기만 한다. 새 계산식 없음. 최대 MAX_WHAT_IF 회.
    - 조회 지표는 "5개 구 모두 확보"된 지원 지표만 허용.
    - AI 설명은 숫자를 단위별로 검사한다: "N점"=실제 점수, "N개"=실제 시설 수,
      "N%"=실제 가중치, "N위"=1~5. 배수·시간·금액 등 계산한 값은 거부.
      실패하면 이유를 알려주고 다시 쓰게 하며, 그래도 실패하면 Python 요약을 쓴다.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from agent import llm
from agent.agent_loop import MAX_ANSWER_CHARS, _numbers_in, _parse_json

MAX_REVIEW_ROUNDS = 3
MAX_WHAT_IF = 2

REVIEW_TOOL_LABELS = {
    "get_region_indicators": "지역별 지표 수치 추가 조회(참고용)",
    "simulate_weights": "가중치 가정 시뮬레이션(실제 추천에 미적용)",
}

INDICATOR_LABELS = {
    "bus_stop_count": "버스정류장 수",
    "hospital_count": "의료기관 수",
    "convenience_store_count": "편의점 수",
}

REVIEW_SYSTEM_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 추천 결과 설명 도우미입니다.
Python이 사용자가 승인한 가중치로 창원시 5개 구의 "시설 수 기반 상대 비교 점수"를 이미 계산했습니다.
관찰 내용(JSON)을 보고 다음 중 하나를 결정하세요.

1. 관찰 내용만으로 추천 결과를 설명할 수 있으면 설명을 작성합니다.
   {"action": "answer", "answer": "한국어 설명"}
2. 설명에 필요한 정보가 부족하면 추가 도구를 요청합니다.
   {"action": "call_tools", "reason": "왜 필요한지 한 문장", "tool_calls": [...]}

[추가로 쓸 수 있는 도구]
- get_region_indicators: {"tool": "get_region_indicators", "indicator_codes": ["hospital_count"]}
  가중치에 포함되지 않은 지표도 참고용으로 조회합니다. 조회 가능 지표는 관찰 내용의 available_indicators 중 true 인 것뿐입니다.
- simulate_weights: {"tool": "simulate_weights", "weights": {"bus_stop_count": 30, "hospital_count": 70}}
  "가중치를 이렇게 바꾸면 순위가 어떻게 달라지는지" 가정 계산합니다. 실제 추천에는 반영되지 않으며,
  1위가 근소한 차이인지, 다른 조건을 중시하면 결과가 바뀌는지 확인할 때만 쓰세요. 최대 2번까지 가능합니다.

[규칙 - 반드시 지킬 것]
- 설명에 쓰는 숫자는 관찰 내용에 있는 값(점수, 시설 수, 가중치 %, 순위)만 그대로 쓰세요.
  점수 차이, 배수, 비율, 합계처럼 직접 계산한 숫자를 만들지 말고 "근소하게 앞선다", "크게 앞선다"처럼 말로 표현하세요.
- 이 점수는 시설 수 기반 상대 비교일 뿐 실제 거주 적합도를 확정하지 않는다는 점을 한 번 언급하세요.
- 통근시간, 주거비, 안전, 교육처럼 관찰 내용에 없는 정보는 말하지 마세요.
- 가정 시뮬레이션 결과를 언급할 때는 "가중치를 바꾼다면" 같은 가정임을 분명히 하세요. 실제 추천은 승인된 가중치 기준입니다.
- 설명은 3~6문장으로 간결하게 쓰세요.
- 반드시 위 JSON 형식 중 하나로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.
"""

_UNIT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(점|개|곳|%|퍼센트|위|배|분|시간|원|만원|km|m\b)", re.IGNORECASE)
_FORBIDDEN_UNITS = {"배", "분", "시간", "원", "만원", "km", "m"}


def _r1(value) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _ranking_view(score_result: dict) -> list[dict]:
    rows = []
    for row in score_result.get("region_scores") or []:
        rows.append({
            "rank": row.get("rank"),
            "region_name": row.get("region_name"),
            "total_score": _r1(row.get("total_score")),
            "tied": row.get("tied", False),
            "components": {
                code: {"raw_value": _r1(comp.get("raw_value")), "normalized_score": _r1(comp.get("normalized_score"))}
                for code, comp in (row.get("component_scores") or {}).items()
            },
            "reference_indicators": {
                code: _r1(ind.get("raw_value"))
                for code, ind in (row.get("reference_indicators") or {}).items()
                if ind.get("raw_value") is not None
            },
        })
    return rows


def build_observation(*, score_result: dict, approved_weights: dict, available_indicators: dict,
                      selected_conditions: list, extra_indicators: dict, what_ifs: list) -> dict:
    total = sum(float(v) for v in approved_weights.values()) or 1.0
    return {
        "selected_conditions": selected_conditions,
        "approved_weights_percent": {k: _r1(float(v) / total * 100) for k, v in approved_weights.items()},
        "available_indicators": available_indicators,
        "indicator_labels": INDICATOR_LABELS,
        "ranking": _ranking_view(score_result),
        "extra_indicators": extra_indicators,
        "what_if_simulations": what_ifs,
    }


# ---------------------------------------------------------------------------
# 판단 (AI 호출)
# ---------------------------------------------------------------------------
def call_reviewer(observation: dict, force_answer: bool, feedback: str | None, model: str) -> dict:
    lines = ["[관찰 내용]", json.dumps(observation, ensure_ascii=False)]
    if feedback:
        lines.append(f"[이전 설명이 검증에서 거부된 이유]\n{feedback}\n이 문제를 고쳐서 다시 설명하세요.")
    lines.append("[중요] 더 이상 추가 도구를 쓸 수 없습니다. 반드시 action을 \"answer\"로 하세요."
                 if force_answer else "설명할 수 있으면 answer, 정보가 부족하면 call_tools 로 응답하세요.")
    try:
        response = llm.chat(
            model=model,
            messages=[{"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                      {"role": "user", "content": "\n\n".join(lines)}],
            options={"temperature": 0.0},
        )
    except Exception as exc:
        raise RuntimeError(f"{llm.backend_label()} 추천 결과 검토 호출에 실패했습니다: {exc}") from exc
    decision = _parse_json(response["message"]["content"])
    if decision is None or decision.get("action") not in ("answer", "call_tools"):
        raise RuntimeError("AI의 추천 결과 검토 응답을 해석하지 못했습니다.")
    return decision


# ---------------------------------------------------------------------------
# 설명 검증
# ---------------------------------------------------------------------------
def _fact_sets(observation: dict) -> dict[str, set[float]]:
    scores, counts, percents = set(), set(), set()
    ranks = {float(i) for i in range(1, 6)}

    def add_ranking(rows):
        for row in rows or []:
            if row.get("total_score") is not None:
                scores.add(row["total_score"])
            for comp in (row.get("components") or {}).values():
                if comp.get("normalized_score") is not None:
                    scores.add(comp["normalized_score"])
                if comp.get("raw_value") is not None:
                    counts.add(comp["raw_value"])
            for value in (row.get("reference_indicators") or {}).values():
                counts.add(value)

    add_ranking(observation.get("ranking"))
    for value in (observation.get("approved_weights_percent") or {}).values():
        percents.add(value)
    for data in (observation.get("extra_indicators") or {}).values():
        for value in (data.get("values") or {}).values():
            if value is not None:
                counts.add(_r1(value))
    for sim in observation.get("what_if_simulations") or []:
        for value in (sim.get("weights_percent") or {}).values():
            percents.add(value)
        add_ranking(sim.get("ranking"))
    counts.add(5.0)  # "창원시 5개 구"
    # 정수로 표기한 점수(예: 100점, 80점)도 같은 값이면 허용
    scores |= {float(int(s)) for s in scores if s == int(s)}
    return {"점": scores, "개": counts, "곳": counts, "%": percents, "퍼센트": percents, "위": ranks}


def verify_answer(answer: str, observation: dict) -> tuple[bool, str]:
    text = (answer or "").strip()
    if not text:
        return False, "AI 설명이 비어 있습니다."
    if len(text) > MAX_ANSWER_CHARS:
        return False, f"AI 설명이 너무 깁니다({len(text)}자)."
    facts = _fact_sets(observation)
    for number, unit in _UNIT_RE.findall(text):
        unit = unit.lower()
        value = float(number.replace(",", ""))
        if unit in _FORBIDDEN_UNITS:
            return False, f"관찰 내용에 없는 값({number}{unit})을 사용했습니다."
        if value not in facts.get(unit, set()):
            return False, f"관찰 내용에 없는 숫자({number}{unit})를 사용했습니다."
    allowed = _numbers_in(json.dumps(observation, ensure_ascii=False)) | {str(i) for i in range(1, 6)}  # 순위, "5개 구"
    unknown = sorted(_numbers_in(text) - allowed)
    if unknown:
        return False, f"관찰 내용에 없는 숫자를 사용했습니다: {', '.join(unknown[:5])}"
    return True, ""


def python_summary(observation: dict) -> str:
    rows = observation.get("ranking") or []
    if not rows:
        return "계산된 추천 결과가 없습니다."
    labels = INDICATOR_LABELS
    weights = ", ".join(f"{labels.get(k, k)} {v}%" for k, v in (observation.get("approved_weights_percent") or {}).items())
    lines = [f"승인된 가중치({weights}) 기준 시설 수 상대 비교 결과입니다."]
    for row in rows[:3]:
        parts = ", ".join(f"{labels.get(c, c)} {int(v['raw_value']) if v['raw_value'] is not None else '-'}개"
                          for c, v in row["components"].items())
        lines.append(f"- {row['rank']}위 {row['region_name']}: {row['total_score']}점 ({parts})")
    for sim in observation.get("what_if_simulations") or []:
        top = (sim.get("ranking") or [{}])[0]
        w = ", ".join(f"{labels.get(k, k)} {v}%" for k, v in sim.get("weights_percent", {}).items())
        lines.append(f"- (가정) 가중치를 {w}로 바꾸면 1위는 {top.get('region_name', '-')}입니다. 실제 추천에는 반영되지 않았습니다.")
    lines.append("점수는 시설 수 기반 상대 비교이며 실제 거주 적합도를 확정하지 않습니다.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 추가 도구 검증·실행
# ---------------------------------------------------------------------------
def _validate_review_calls(raw_calls, available_codes: set[str], what_if_used: int) -> tuple[list[dict], list[str]]:
    if not isinstance(raw_calls, list) or not raw_calls:
        return [], ["추가 도구 요청 형식이 올바르지 않습니다."]
    calls, notes = [], []
    for call in raw_calls[:3]:
        if not isinstance(call, dict):
            notes.append("형식이 올바르지 않은 도구 요청을 무시했습니다.")
            continue
        tool = call.get("tool")
        if tool == "get_region_indicators":
            codes = [c for c in (call.get("indicator_codes") or []) if c in available_codes]
            dropped = [c for c in (call.get("indicator_codes") or []) if c not in available_codes]
            if dropped:
                notes.append(f"확보되지 않았거나 지원하지 않는 지표 {dropped}는 조회하지 않았습니다.")
            if codes:
                calls.append({"tool": tool, "indicator_codes": list(dict.fromkeys(codes))})
        elif tool == "simulate_weights":
            if what_if_used + sum(c["tool"] == "simulate_weights" for c in calls) >= MAX_WHAT_IF:
                notes.append(f"가정 시뮬레이션은 최대 {MAX_WHAT_IF}번까지만 실행합니다.")
                continue
            weights = call.get("weights")
            if not isinstance(weights, dict):
                notes.append("가정 시뮬레이션의 가중치 형식이 올바르지 않습니다.")
                continue
            clean = {}
            for code, value in weights.items():
                if code not in available_codes or isinstance(value, bool):
                    continue
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    continue
                if v > 0:
                    clean[code] = v
            if not clean:
                notes.append("가정 시뮬레이션에 쓸 수 있는 유효한 가중치가 없습니다.")
                continue
            calls.append({"tool": tool, "weights": clean})
        else:
            notes.append(f"허용되지 않은 도구 '{tool}' 요청을 무시했습니다.")
    return calls, notes


def run_review_loop(
    *,
    regions: list[dict],
    score_result: dict,
    approved_weights: dict,
    available_indicators: dict,
    selected_conditions: list,
    candidate_count: int,
    model: str,
    get_indicators_fn: Callable,
    simulate_fn: Callable,
    max_rounds: int = MAX_REVIEW_ROUNDS,
) -> dict:
    """
    Returns:
        {"agent_steps": [...], "review_tool_calls": [...], "what_if_results": [...],
         "extra_indicators": {...}, "final_answer": {"text","source","rejected_reason"},
         "review_error": str | None}
    """
    available_codes = {c for c, ok in available_indicators.items() if ok}
    extra_indicators: dict = {}
    what_ifs: list[dict] = []
    review_log: list[dict] = []
    steps: list[dict] = []
    review_error = None
    final_text, source, rejected_reason = None, "python_summary", None
    feedback: str | None = None
    no_new = False

    def observe() -> dict:
        return build_observation(score_result=score_result, approved_weights=approved_weights,
                                 available_indicators=available_indicators,
                                 selected_conditions=selected_conditions,
                                 extra_indicators=extra_indicators, what_ifs=what_ifs)

    for round_no in range(1, max_rounds + 1):
        observation = observe()
        force = round_no == max_rounds or feedback is not None or no_new
        try:
            decision = call_reviewer(observation, force, feedback, model)
        except RuntimeError as exc:
            review_error = str(exc)
            steps.append({"round": round_no, "action": "error", "reason": review_error})
            break

        if decision["action"] == "answer" or force:
            answer = str(decision.get("answer") or "").strip()
            ok, why = verify_answer(answer, observation)
            steps.append({"round": round_no, "action": "answer",
                          "note": "숫자 검증 통과" if ok else f"검증 실패: {why}"})
            if ok:
                final_text, source, rejected_reason = answer, "ai_verified", None
                break
            rejected_reason = why
            if round_no < max_rounds:
                feedback = why
                continue
            break

        reason = str(decision.get("reason") or "").strip()[:200]
        calls, notes = _validate_review_calls(decision.get("tool_calls"), available_codes, len(what_ifs))
        executed_names = []
        for call in calls:
            entry = {"tool": call["tool"], "reason": reason, "step": round_no + 1}
            try:
                if call["tool"] == "get_region_indicators":
                    new_codes = [c for c in call["indicator_codes"] if c not in extra_indicators]
                    if not new_codes:
                        notes.append("요청한 지표는 이미 조회했습니다.")
                        continue
                    data = get_indicators_fn(regions, new_codes)
                    names = {r["region_id"]: r["region_name"] for r in score_result.get("region_scores") or []}
                    for code, v in data.items():
                        extra_indicators[code] = {
                            "indicator_name": v.get("indicator_name"),
                            "values": {names.get(rid, rid): _r1(val) for rid, val in v["values"].items()},
                        }
                    entry["indicator_codes"] = new_codes
                    entry["result"] = {c: extra_indicators[c]["values"] for c in data}
                else:
                    sim = simulate_fn(call["weights"], candidate_count, regions=regions)
                    total = sum(call["weights"].values())
                    sim_view = {
                        "weights_percent": {k: _r1(v / total * 100) for k, v in call["weights"].items()},
                        "status": sim.get("status"),
                        "ranking": [{"rank": r["rank"], "region_name": r["region_name"],
                                     "total_score": r["total_score"]} for r in _ranking_view(sim)],
                    }
                    what_ifs.append(sim_view)
                    entry["weights"] = call["weights"]
                    entry["result"] = sim_view
                entry["executed"], entry["error"] = True, None
            except Exception as exc:
                entry["executed"], entry["error"] = False, str(exc)
            review_log.append(entry)
            executed_names.append(call["tool"])
        steps.append({"round": round_no, "action": "call_tools", "reason": reason,
                      "requested": decision.get("tool_calls"), "executed_tools": executed_names,
                      "note": "; ".join(notes) or None})
        if not executed_names:
            no_new = True

    if final_text is None:
        final_text = python_summary(observe())

    return {
        "agent_steps": steps,
        "review_tool_calls": review_log,
        "what_if_results": what_ifs,
        "extra_indicators": extra_indicators,
        "final_answer": {"text": final_text, "source": source, "rejected_reason": rejected_reason},
        "review_error": review_error,
    }
