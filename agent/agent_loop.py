# 관찰 -> 판단 -> 행동 반복 루프 (위치 분석 Agent용)
"""
location_agent 가 처음 세운 계획대로 도구를 실행한 "뒤"에 붙는 단계다.

    [1회차 계획·실행]  (기존 location_agent 그대로)
          │
          ▼
    ┌─► 관찰: 실행 결과를 Python이 요약해 AI에게 보여준다 (원본 대신 핵심 수치·상위 목록)
    │     │
    │     ▼
    │   판단: AI가 둘 중 하나를 고른다
    │     ├─ "answer"     : 지금 결과로 사용자 질문에 답할 수 있음 → 답변 작성
    │     └─ "call_tools" : 정보가 부족함 → 추가 조회 도구 제안
    │     │
    │     ▼
    └── 행동: 추가 도구는 1회차와 똑같은 Python 검증(허용 도구·반경 고정·요청 범위 보정·
              중복 제거)을 통과한 것만 실행 → 다시 관찰로

[안전장치]
    - 반복 횟수 상한(MAX_REVIEW_ROUNDS). 마지막 회차에는 추가 조회를 금지하고 답변만 요구한다.
    - 좌표·반경은 여전히 Python이 고정한다(검증 함수를 그대로 재사용).
    - AI 최종 답변은 그대로 보여주지 않고 Python이 검사한다. 검사에 실패하면 실패 이유를
      AI에게 알려주고 남은 회차 안에서 다시 쓰게 한다(자기 수정). 그래도 실패하면 아래처럼 처리.
        · 답변 속 모든 숫자가 실제 조회 결과(또는 사용자 문장)에 있는 숫자여야 한다.
          특히 "N개/곳"은 실제 개수 값, "Nm"는 실제 거리·반경 값과 일치해야 하고,
          배수·%·시간처럼 계산한 값은 허용하지 않는다.
        · 거리 숫자(m/km)를 말하면 "직선거리"라는 표현이 있어야 한다.
        · "N분", "도보 N" 처럼 계산하지 않은 이동시간·도보거리를 말하면 안 된다.
      하나라도 어기면 AI 답변을 버리고 Python이 만든 요약으로 대신한다(사유를 기록).
    - AI 호출이 실패하면(연결 실패·사용량 제한 등) 반복을 멈추고 Python 요약을 쓴다.
      1회차에 실행한 결과는 그대로 유지된다.

이 모듈은 location_agent 의 검증·실행 함수를 인자로 받아서 쓴다(순환 import 방지).
"""

from __future__ import annotations

import json
import re
from typing import Callable

from agent import llm
from agent.agent_state import history_to_prompt

MAX_REVIEW_ROUNDS = 3        # AI 판단 호출 최대 횟수 (1회차 계획 호출 포함 시 최대 4회)
TOP_N_FOR_OBSERVATION = 5    # AI에게 보여줄 시설 목록 상위 개수
MAX_ANSWER_CHARS = 800

REVIEW_SYSTEM_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 위치 기반 분석 도우미입니다.
사용자의 요청을 위해 Python이 이미 조회 도구를 실행했고, 그 결과(관찰 내용)를 JSON으로 받습니다.
관찰 내용을 보고 다음 중 하나를 결정하세요.

1. 관찰 내용만으로 사용자의 질문에 답할 수 있으면 답변을 작성합니다.
   {"action": "answer", "answer": "한국어 답변"}
2. 사용자의 질문에 답하기에 정보가 부족하고, 아래 도구로 보완할 수 있으면 추가 조회를 요청합니다.
   답변에 "정보가 없다", "판단할 수 없다"고 쓰려는데 그 정보를 아래 도구로 얻을 수 있다면
   answer 대신 반드시 call_tools 를 선택하세요. (예: 반경을 넓혔을 때의 변화가 궁금한데
   관찰 내용에 반경별 비교가 없으면 compare_nearby_facilities 를 요청)
   {"action": "call_tools", "reason": "왜 더 조회해야 하는지 한 문장", "tool_calls": [{"tool": "도구이름", "max_results": 숫자(선택)}]}

[사용 가능한 도구 - 이 3개뿐]
- find_nearby_bus_stops: 주변 버스정류장을 가까운 순으로 조회
- find_nearby_convenience_stores: 주변 편의점을 가까운 순으로 조회
- compare_nearby_facilities: 300m/500m/1km 반경별 버스정류장·편의점 전체 건수 비교

[규칙 - 반드시 지킬 것]
- 답변에 쓰는 숫자(개수, 거리 등)는 반드시 관찰 내용에 있는 값만 그대로 쓰세요. 계산하거나 추정하거나 반올림한 새 숫자를 만들지 마세요.
  배수(예: 2배), 비율, 합계, 차이처럼 직접 계산한 값도 쓰지 말고, "더 많다", "크게 늘어난다"처럼 말로 표현하세요.
- "N개", "N곳"은 관찰 내용의 total_count나 반경별 건수만 쓰세요. 가까운 순 목록을 소개할 때는
  "가까운 3곳"처럼 개수를 숫자로 쓰지 말고 이름과 직선거리를 나열하세요.
- 거리를 말할 때는 반드시 "직선거리"라고 쓰세요. 도보 거리, 걸어서 몇 분, 이동시간처럼 관찰 내용에 없는 정보는 절대 말하지 마세요.
- 관찰 내용에 이미 같은 도구 결과가 있으면 같은 도구를 다시 요청하지 마세요.
- 검색 위치와 반경은 이미 정해져 있습니다. 좌표나 반경을 바꾸려 하지 마세요.
- 답변은 3~5문장으로 간결하게 쓰고, 사용자가 실제로 물은 내용에 집중하세요.
- 데이터로 답할 수 없는 부분이 있으면 그렇다고 솔직하게 말하세요.
- 반드시 위 JSON 형식 중 하나로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.
"""

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_DISTANCE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(m\b|미터|km|킬로)", re.IGNORECASE)
# 검색 반경 표기("500m 안", "1km 이내")는 시설까지의 거리가 아니므로 '직선거리' 표기 검사에서 뺀다
_RADIUS_METERS = {300.0, 500.0, 1000.0}
_TIME_RE = re.compile(r"\d+\s*분|도보\s*\d|걸어서\s*\d")
# 숫자 + 단위. 단위에 따라 허용되는 값의 집합이 다르다.
_UNIT_NUMBER_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(개|곳|건|m\b|미터|km|킬로|배|%|퍼센트|프로|시간)", re.IGNORECASE)
_FORBIDDEN_UNITS = {"배", "%", "퍼센트", "프로", "시간"}  # 계산한 비율·배수·시간은 관찰 내용에 없다


# ---------------------------------------------------------------------------
# 관찰 - 실행 결과를 AI에게 보여줄 핵심 정보로 요약 (Python이 계산, 새 수치 없음)
# ---------------------------------------------------------------------------
def summarize_observations(executed: list[dict]) -> list[dict]:
    observations = []
    for entry in executed:
        obs: dict = {"tool": entry.get("tool"), "step": entry.get("step", 1)}
        if not entry.get("executed"):
            obs["error"] = entry.get("error") or "실행 실패"
            observations.append(obs)
            continue
        result = entry.get("result") or {}
        tool = entry.get("tool")
        if tool == "find_nearby_bus_stops":
            obs.update(
                radius_m=entry.get("radius_m"),
                distance_type="직선거리",
                total_count=result.get("total_count"),
                nearest=[
                    {"rank": s.get("rank"), "name": s.get("stop_name"),
                     "district": s.get("district"), "straight_distance_m": s.get("straight_distance_m")}
                    for s in (result.get("stops") or [])[:TOP_N_FOR_OBSERVATION]
                ],
                warnings=result.get("warnings") or [],
            )
        elif tool == "find_nearby_convenience_stores":
            obs.update(
                radius_m=entry.get("radius_m"),
                distance_type="직선거리",
                total_count=result.get("total_count"),
                nearest=[
                    {"rank": s.get("rank"),
                     "name": f"{s.get('facility_name', '')} {s.get('branch_name', '')}".strip(),
                     "road_address": s.get("road_address"),
                     "straight_distance_m": s.get("straight_distance_m")}
                    for s in (result.get("stores") or [])[:TOP_N_FOR_OBSERVATION]
                ],
                warnings=result.get("warnings") or [],
            )
        elif tool == "compare_nearby_facilities":
            obs.update(
                distance_type="직선거리",
                bus_stop_counts_by_radius_m=(result.get("bus_stops") or {}).get("counts"),
                convenience_store_counts_by_radius_m=(result.get("convenience_stores") or {}).get("counts"),
            )
        if result.get("status") not in (None, "ok"):
            obs["status"] = result.get("status")
            obs["message"] = result.get("message")
        observations.append(obs)
    return observations


# ---------------------------------------------------------------------------
# 판단 - AI 호출
# ---------------------------------------------------------------------------
def _build_review_prompt(user_text: str, radius_m: int, observations: list[dict],
                         force_answer: bool, feedback: str | None = None,
                         history: list[dict] | None = None) -> str:
    context = history_to_prompt(history)
    lines = ([context] if context else []) + [
        f"[사용자 요청]\n{user_text}",
        f"[적용된 검색 반경(고정)]\n{radius_m}m",
        "[관찰 내용 - 지금까지 실행한 도구 결과]",
        json.dumps(observations, ensure_ascii=False),
    ]
    if feedback:
        lines.append(f"[이전 답변이 검증에서 거부된 이유]\n{feedback}\n이 문제를 고쳐서 다시 답변하세요.")
    if force_answer:
        lines.append("[중요] 더 이상 추가 조회를 할 수 없습니다. 반드시 action을 \"answer\"로 하여 "
                     "관찰 내용만으로 답변하세요.")
    else:
        lines.append("관찰 내용으로 답할 수 있으면 answer, 부족하면 call_tools 로 응답하세요.")
    return "\n\n".join(lines)


def _parse_json(raw_text: str) -> dict | None:
    match = re.search(r"\{.*\}", raw_text or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def call_reviewer(user_text: str, radius_m: int, observations: list[dict],
                  force_answer: bool, model: str, feedback: str | None = None,
                  history: list[dict] | None = None) -> dict:
    try:
        response = llm.chat(
            model=model,
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": _build_review_prompt(user_text, radius_m, observations, force_answer, feedback, history)},
            ],
            options={"temperature": 0.0},
            think=False,
        )
    except Exception as exc:
        raise RuntimeError(f"{llm.backend_label()} 결과 검토 호출에 실패했습니다: {exc}") from exc
    decision = _parse_json(response["message"]["content"])
    if decision is None or decision.get("action") not in ("answer", "call_tools"):
        raise RuntimeError("AI의 결과 검토 응답을 해석하지 못했습니다.")
    return decision


# ---------------------------------------------------------------------------
# 최종 답변 검증 - 숫자·거리 표현
# ---------------------------------------------------------------------------
def _numbers_in(text: str) -> set[str]:
    out = set()
    for raw in _NUMBER_RE.findall((text or "").replace(",", "")):
        out.add(raw)
        if "." in raw:
            out.add(raw.rstrip("0").rstrip("."))
    return out


def _fact_sets(observations: list[dict]) -> tuple[set[float], set[float]]:
    """관찰 내용에서 '개수'로 쓸 수 있는 값과 '거리(m)'로 쓸 수 있는 값을 모은다."""
    counts: set[float] = set()
    distances: set[float] = {300.0, 500.0, 1000.0}
    for obs in observations:
        if obs.get("total_count") is not None:
            counts.add(float(obs["total_count"]))
        # 가까운 순 목록의 길이는 '전체 개수'와 헷갈리기 쉬워 개수로 인정하지 않는다
        nearest = obs.get("nearest") or []
        for item in nearest:
            if item.get("straight_distance_m") is not None:
                distances.add(float(item["straight_distance_m"]))
        if obs.get("radius_m") is not None:
            distances.add(float(obs["radius_m"]))
        for key in ("bus_stop_counts_by_radius_m", "convenience_store_counts_by_radius_m"):
            for radius, value in (obs.get(key) or {}).items():
                distances.add(float(radius))
                if value is not None:
                    counts.add(float(value))
    return counts, distances


def allowed_numbers(observations: list[dict], user_text: str, radius_m: int) -> set[str]:
    allowed = _numbers_in(json.dumps(observations, ensure_ascii=False)) | _numbers_in(user_text)
    allowed |= {str(radius_m), "300", "500", "1000", "1", "0.3", "0.5"}  # 1km 등 반경 표기
    return allowed


def verify_answer(answer: str, observations: list[dict], user_text: str, radius_m: int) -> tuple[bool, str]:
    text = (answer or "").strip()
    if not text:
        return False, "AI 답변이 비어 있습니다."
    if len(text) > MAX_ANSWER_CHARS:
        return False, f"AI 답변이 너무 깁니다({len(text)}자)."
    if _TIME_RE.search(text):
        return False, "계산하지 않은 이동시간·도보 정보를 언급했습니다."
    if "직선" not in text:
        for number, unit in _DISTANCE_RE.findall(text):
            meters = float(number.replace(",", "")) * (1000 if unit.lower() in ("km", "킬로") else 1)
            if meters not in _RADIUS_METERS:
                return False, "시설까지의 거리를 말하면서 '직선거리'라고 밝히지 않았습니다."
    counts, distances = _fact_sets(observations)
    user_numbers = {float(n) for n in _numbers_in(user_text)}
    for number, unit in _UNIT_NUMBER_RE.findall(text):
        value = float(number.replace(",", ""))
        unit = unit.lower()
        if unit in _FORBIDDEN_UNITS:
            return False, f"조회 결과에 없는 계산 값({number}{unit})을 사용했습니다."
        if unit in ("개", "곳", "건") and value not in counts | user_numbers:
            return False, f"조회 결과에 없는 개수({number}{unit})를 사용했습니다."
        if unit in ("m", "미터") and value not in distances | user_numbers:
            return False, f"조회 결과에 없는 거리({number}{unit})를 사용했습니다."
        if unit in ("km", "킬로") and value * 1000 not in distances:
            return False, f"조회 결과에 없는 거리({number}{unit})를 사용했습니다."
    unknown = sorted(_numbers_in(text) - allowed_numbers(observations, user_text, radius_m))
    if unknown:
        return False, f"조회 결과에 없는 숫자를 사용했습니다: {', '.join(unknown[:5])}"
    return True, ""


# ---------------------------------------------------------------------------
# AI 답변을 쓸 수 없을 때의 Python 요약 (결정적)
# ---------------------------------------------------------------------------
def python_summary(observations: list[dict]) -> str:
    lines = []
    for obs in observations:
        tool = obs.get("tool")
        if obs.get("error"):
            lines.append(f"- {tool} 조회에 실패했습니다.")
            continue
        if tool in ("find_nearby_bus_stops", "find_nearby_convenience_stores"):
            kind = "버스정류장" if tool == "find_nearby_bus_stops" else "편의점"
            total = obs.get("total_count")
            line = f"- 반경 {obs.get('radius_m')}m(직선거리) 안 {kind}: {total if total is not None else '확인 불가'}개"
            nearest = obs.get("nearest") or []
            if nearest:
                first = nearest[0]
                line += f", 가장 가까운 곳은 {first.get('name')}(직선거리 {first.get('straight_distance_m')}m)"
            lines.append(line + ".")
        elif tool == "compare_nearby_facilities":
            bus = obs.get("bus_stop_counts_by_radius_m") or {}
            conv = obs.get("convenience_store_counts_by_radius_m") or {}
            for r in sorted({*bus.keys(), *conv.keys()}, key=lambda x: int(x)):
                lines.append(f"- 반경 {r}m(직선거리): 버스정류장 {bus.get(r, '-')}개, 편의점 {conv.get(r, '-')}개.")
    return "\n".join(lines) if lines else "조회 결과가 없습니다."


# ---------------------------------------------------------------------------
# 반복 루프
# ---------------------------------------------------------------------------
def run_review_loop(
    *,
    user_text: str,
    lat: float,
    lon: float,
    resolved_radius_m: int,
    ui_max_results: int,
    constraints: dict | None,
    executed: list[dict],
    validate_fn: Callable,
    execute_fn: Callable,
    model: str,
    max_rounds: int = MAX_REVIEW_ROUNDS,
    history: list[dict] | None = None,
) -> dict:
    """
    Returns:
        {
          "executed_tool_calls": [...],   # 1회차 + 추가 실행분(각 항목에 step 번호)
          "agent_steps": [ {"round", "action", "reason", "requested", "executed_tools", "note"} ],
          "final_answer": {"text", "source": "ai_verified" | "python_summary", "rejected_reason"},
          "review_error": str | None,
        }
    """
    executed = [dict(e, step=e.get("step", 1)) for e in executed]
    signatures = {(e["tool"], e.get("radius_m"), e.get("max_results")) for e in executed}
    steps: list[dict] = []
    review_error = None
    final_text, source, rejected_reason = None, "python_summary", None
    no_new_tools = False
    feedback: str | None = None

    for round_no in range(1, max_rounds + 1):
        observations = summarize_observations(executed)
        force = round_no == max_rounds or no_new_tools or feedback is not None
        try:
            decision = call_reviewer(user_text, resolved_radius_m, observations, force, model, feedback, history)
        except RuntimeError as exc:
            review_error = str(exc)
            steps.append({"round": round_no, "action": "error", "reason": review_error})
            break

        if decision["action"] == "answer" or force:
            answer = str(decision.get("answer") or "").strip()
            ok, why = verify_answer(answer, observations, user_text, resolved_radius_m)
            steps.append({"round": round_no, "action": "answer",
                          "note": "숫자·거리 표현 검증 통과" if ok else f"검증 실패: {why}"})
            if ok:
                final_text, source, rejected_reason = answer, "ai_verified", None
                break
            rejected_reason = why
            if round_no < max_rounds:
                feedback = why   # 거부 이유를 알려주고 한 번 더 답변하게 한다(자기 수정)
                continue
            break

        # call_tools: 1회차와 같은 검증(허용 도구·반경 고정·중복 제거·시설 종류 범위)을 통과해야 실행.
        # 단, 반경별 비교 도구는 AI가 결과를 본 뒤 필요하다고 판단한 것이므로, 요청 문장에
        # "비교/반경별" 단어가 없다는 이유로 단일 조회로 바꾸지 않는다.
        review_constraints = dict(constraints, wants_radius_comparison=True) if constraints else constraints
        validated = validate_fn({"tool_calls": decision.get("tool_calls")}, resolved_radius_m,
                                ui_max_results, constraints=review_constraints)
        reason = str(decision.get("reason") or "").strip()[:200]
        if validated["status"] != "ok":
            steps.append({"round": round_no, "action": "call_tools", "reason": reason,
                          "requested": decision.get("tool_calls"),
                          "note": f"추가 조회 계획 거부: {validated['reason']}"})
            no_new_tools = True
            continue

        new_calls = [c for c in validated["tool_calls"]
                     if (c["tool"], c.get("radius_m"), c.get("max_results")) not in signatures]
        if not new_calls:
            steps.append({"round": round_no, "action": "call_tools", "reason": reason,
                          "requested": decision.get("tool_calls"),
                          "note": "요청한 조회가 이미 실행된 것과 같아 다시 실행하지 않았습니다."})
            no_new_tools = True
            continue

        new_executed = execute_fn(lat, lon, new_calls)
        for e in new_executed:
            e["step"] = round_no + 1
            signatures.add((e["tool"], e.get("radius_m"), e.get("max_results")))
        executed.extend(new_executed)
        steps.append({"round": round_no, "action": "call_tools", "reason": reason,
                      "requested": decision.get("tool_calls"),
                      "executed_tools": [e["tool"] for e in new_executed],
                      "note": "; ".join(validated.get("notes") or []) or None})

    if final_text is None:
        final_text = python_summary(summarize_observations(executed))

    return {
        "executed_tool_calls": executed,
        "agent_steps": steps,
        "final_answer": {"text": final_text, "source": source, "rejected_reason": rejected_reason},
        "review_error": review_error,
    }
