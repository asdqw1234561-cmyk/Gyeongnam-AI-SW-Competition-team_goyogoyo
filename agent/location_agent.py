# Ollama(Qwen3.5) 연동 - 위치 기반 주변 시설 분석 Agent
"""
pages/user.py에서 사용자가 지도로 "확정한" 검색 중심 좌표 주변의 생활시설을
자연어로 물으면, 로컬 Ollama(qwen3.5:4b)가 어떤 조회 도구를 쓸지 계획하고
Python이 그 계획을 검증한 뒤 허용된 도구만 실제로 실행하는 작은 Agent다.

agent/planner.py(창원시 5개 구 상대 비교 추천용 Agent)와는 별개의 독립된 모듈
이다. 두 Agent 모두 "LLM의 계획을 그대로 신뢰하지 않고 Python이 검증한다"는
같은 철학을 공유하지만, 이 모듈은 planner.py를 억지로 확장하거나 하나로
합치지 않는다 - 다루는 도구·입력·검증 규칙이 서로 다르기 때문이다
(planner.py: 승인된 가중치로 5개 구 점수 계산 / 이 모듈: 확정된 좌표 주변의
개별 시설 조회).

[가장 중요한 원칙 - 검색 중심 좌표는 항상 Python이 통제]
    AI는 좌표를 전혀 제안하지 않는다(시스템 프롬프트에 좌표 필드 자체가 없다).
    실행 함수(tool_find_nearby_bus_stops 등)는 항상 run_location_agent()의
    search_center 인자로 받은 좌표만 쓴다 - AI의 JSON 응답에 lat/lon이 섞여
    들어와도 애초에 정규화 과정에서 복사하지 않으므로 실행에 영향을 줄 수 없다.
    map_click_candidate(지도에서 클릭했지만 아직 승인하지 않은 좌표)는 이 모듈에
    절대 전달하면 안 된다 - 호출 측(pages/user.py)이 반드시 승인된
    st.session_state.search_center만 넘겨야 한다.

[검색 반경도 항상 Python이 결정]
    사용자의 자연어 문장에 "300m"/"500m"/"1km"처럼 명시적인 반경이 있으면 그
    값을, 없으면 화면에서 현재 선택된 반경을 "해석된 반경"(resolved_radius_m)
    으로 미리 정하고, AI에게도 이 값을 알려준다. AI가 tool_calls에 다른
    radius_m을 적더라도 Python이 항상 해석된 반경으로 강제 치환하며(무시했다는
    사실은 notes에 기록), 자연어에 적힌 반경이 300/500/1000 중 하나가 아니면
    AI 호출 자체를 생략하고 바로 "지원하지 않는 반경"이라고 안내한다(다른 값으로
    조용히 대체하지 않음).

[허용된 도구 - 전부 기존 서비스 함수를 그대로 감싼 얇은 래퍼]
    find_nearby_bus_stops          -> services.bus_stops.find_nearby_bus_stops()
    find_nearby_convenience_stores -> services.convenience.find_nearby_stores()
    compare_nearby_facilities      -> services.bus_stops.count_nearby_by_radius() +
                                       services.convenience.count_nearby_by_radius()
    새로운 시설 수 계산식이나 데이터 조회 경로를 만들지 않는다.

[지원하지 않는 요청]
    실제 버스 이동시간·배차 간격·실시간 도착정보, 실제 도보 경로, 의료기관
    위치 검색, 주거비·매물, 범죄율·안전도, 종합적인 거주 적합도 확정 등은 현재
    데이터로 답할 수 없다. AI는 이런 요청에 관계없는 도구를 억지로 실행하지
    않고 unsupported_requests에만 담아야 한다(Python은 이 목록을 구조만
    검증하고 표시용으로만 쓴다 - 내용 자체를 신뢰해 다른 동작을 하지 않는다).

[Ollama 실패 시]
    연결 실패·JSON 해석 실패·계획 검증 실패는 모두 "기본 절차"로 안전하게
    전환한다 - compare_nearby_facilities(반경별 전체 비교)를 실행해 최소한의
    유용한 정보는 제공하되, 화면에는 "AI 분석 계획을 생성하지 못해 기본 조회
    절차를 사용했습니다"라고 명확히 표시한다(AI가 실행한 것처럼 보이지 않게).
"""

from __future__ import annotations

import json
import re

import ollama

from services import bus_stops, convenience
from services.bus_stops import PRESET_RADII_M as SUPPORTED_RADII_M

OLLAMA_MODEL = "qwen3.5:4b"

ALLOWED_TOOLS: tuple[str, ...] = (
    "find_nearby_bus_stops",
    "find_nearby_convenience_stores",
    "compare_nearby_facilities",
)

TOOL_LABELS: dict[str, str] = {
    "find_nearby_bus_stops": "주변 버스정류장 조회",
    "find_nearby_convenience_stores": "주변 편의점 조회",
    "compare_nearby_facilities": "반경별(300m/500m/1km) 시설 수 비교",
}

DEFAULT_RADIUS_M = 500
MAX_RESULTS_LIMIT = 30  # pages/user.py 슬라이더 상한과 동일하게 맞춤
MAX_TOOL_CALLS = 4


# ---------------------------------------------------------------------------
# 도구 실행 함수 - 전부 기존 서비스 함수를 그대로 호출(새 계산식 없음)
# ---------------------------------------------------------------------------
def tool_find_nearby_bus_stops(lat: float, lon: float, radius_m: int, max_results: int) -> dict:
    return bus_stops.find_nearby_bus_stops(lat, lon, radius_m=radius_m, max_results=max_results)


def tool_find_nearby_convenience_stores(lat: float, lon: float, radius_m: int, max_results: int) -> dict:
    return convenience.find_nearby_stores(lat, lon, radius_m=radius_m, max_results=max_results)


def tool_compare_nearby_facilities(lat: float, lon: float) -> dict:
    return {
        "bus_stops": bus_stops.count_nearby_by_radius(lat, lon),
        "convenience_stores": convenience.count_nearby_by_radius(lat, lon),
    }


# ---------------------------------------------------------------------------
# 반경 해석 - 자연어에 명시된 반경을 Ollama 호출 전에 결정적으로 뽑아낸다
# ---------------------------------------------------------------------------
_RADIUS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(킬로미터|킬로|km|미터|m)\b", re.IGNORECASE)


def extract_explicit_radius_m(text: str) -> dict:
    """
    사용자 문장에서 "300m", "500미터", "1km"처럼 명시적인 반경 표현을 찾는다.

    Returns:
        {"found": False, "radius_m": None, "raw_text": None}  # 반경 언급 없음 - 화면 기본값 사용
        {"found": True, "radius_m": 500, "raw_text": "500m"}   # 지원하는 반경을 명시함
        {"found": True, "radius_m": None, "raw_text": "200m"}  # 반경을 명시했지만 지원하지 않는 값
    """
    match = _RADIUS_PATTERN.search(text or "")
    if not match:
        return {"found": False, "radius_m": None, "raw_text": None}

    value = float(match.group(1))
    unit = match.group(2).lower()
    meters = value * 1000 if unit in ("km", "킬로미터", "킬로") else value
    meters_int = int(round(meters))

    if meters_int in SUPPORTED_RADII_M:
        return {"found": True, "radius_m": meters_int, "raw_text": match.group(0)}
    return {"found": True, "radius_m": None, "raw_text": match.group(0)}


# ---------------------------------------------------------------------------
# 계획 수립 (Ollama 호출)
# ---------------------------------------------------------------------------
LOCATION_AGENT_SYSTEM_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 위치 기반 분석 도우미입니다.
사용자가 이미 지도에서 확정한 위치 주변의 생활시설을 조회하기 위해, 어떤 조회 도구를
사용할지 "계획"만 세우세요. 실제 데이터 조회는 Python이 담당합니다.

[사용 가능한 도구 - 이 3개뿐, 그 외 도구는 존재하지 않습니다]
1. find_nearby_bus_stops: 확정된 위치 주변의 버스정류장을 가까운 순으로 조회합니다.
   정류소명·소속 구·직선거리·데이터 품질 주의사항을 알 수 있습니다.
2. find_nearby_convenience_stores: 확정된 위치 주변의 편의점을 가까운 순으로 조회합니다.
   상호명·주소·직선거리를 알 수 있습니다.
3. compare_nearby_facilities: 300m/500m/1km 반경별 버스정류장·편의점 "전체 건수"를
   한 번에 비교합니다. 특정 반경 하나만 묻는 게 아니라 여러 반경을 비교하고 싶어하는
   요청에 적합합니다.

[규칙 - 반드시 지킬 것]
- 검색 위치(위도·경도)는 이미 사용자가 지도에서 확정했습니다. 당신은 좌표를 전혀
  언급하거나 만들어낼 수 없습니다 - tool_calls에 lat/lon 필드를 넣지 마세요.
- 검색 반경은 이미 결정되어 있습니다(아래 사용자 메시지에 안내됩니다). radius_m
  필드를 적어도 참고용일 뿐이며, 실제 조회에는 항상 그 결정된 값만 쓰입니다.
- max_results(표시 개수)는 선택 사항입니다. "가장 가까운 정류장"처럼 1곳만 필요하면
  max_results를 1로 제안하세요. 생략하면 화면에 설정된 기본값이 쓰입니다.
- 사용자가 요청한 것이 위 3개 도구로 답할 수 없는 내용이면(예: 실제 버스 이동시간·
  배차 간격·실시간 도착정보, 실제 도보 경로, 의료기관 위치, 주거비·매물, 범죄율·
  안전도, 종합적인 거주 적합도 확정 등) 그 도구를 억지로 호출하지 말고
  unsupported_requests에 요청 내용과 짧은 이유만 적으세요. 절대로 시설 수·거리·
  주소·이동시간 등 실제 데이터를 지어내지 마세요.
- 같은 도구를 중복해서 제안하지 마세요.
- 추론 과정을 출력하지 마세요. 각 도구 호출의 reason은 한 문장으로 간단히만 적으세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.

{"goals": ["주변 버스정류장 조회"], "tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "사용자가 버스정류장 수를 물었음"}], "unsupported_requests": []}
"""


def _build_user_prompt(user_text: str, resolved_radius_m: int) -> str:
    return (
        f"[사용자 요청]\n{user_text}\n\n"
        f"[이번 조회에 실제로 적용될 반경(이미 결정됨)]\n{resolved_radius_m}m\n\n"
        "위 요청을 분석해 JSON 계획을 작성하세요."
    )


def _parse_plan(raw_text: str) -> dict | None:
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def call_location_planner(user_text: str, resolved_radius_m: int) -> dict:
    """로컬 Ollama(qwen3.5:4b)를 호출해 위치 분석 계획(JSON)을 받는다. 연결
    실패와 JSON 해석 실패를 모두 RuntimeError 하나로 통일한다 - run_location_
    agent()가 이 예외 하나만 잡으면 "기본 절차" 폴백으로 안전하게 전환할 수
    있다."""
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": LOCATION_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(user_text, resolved_radius_m)},
            ],
            options={"temperature": 0.0},
            # qwen3.5는 기본적으로 "생각 과정"(thinking)을 먼저 길게 생성한다 - 실측
            # 결과, 이 시스템 프롬프트처럼 긴 지시문에서는 생각 과정만으로 응답 토큰
            # 예산을 다 써버려 최종 JSON(content)이 아예 비어서 돌아오는 경우가
            # 있었다(검증 단계에서 안전하게 기본 절차로 폴백되긴 하지만, AI가 실제로
            # 계획을 세울 기회조차 못 받는 것은 이 기능의 취지와 맞지 않는다).
            # think=False로 생각 과정 생성을 끄면 모델이 곧바로 JSON을 출력한다.
            think=False,
        )
    except Exception as exc:  # Ollama 서버 미실행 등
        raise RuntimeError(f"Ollama 위치 분석 계획 호출에 실패했습니다: {exc}") from exc

    raw_text = response["message"]["content"]
    plan = _parse_plan(raw_text)
    if plan is None:
        raise RuntimeError("AI가 반환한 위치 분석 계획을 JSON으로 해석하지 못했습니다.")
    return plan


# ---------------------------------------------------------------------------
# 계획 검증 - LLM의 계획을 그대로 신뢰하지 않는다
# ---------------------------------------------------------------------------
def validate_and_normalize_plan(plan: object, resolved_radius_m: int, ui_max_results: int) -> dict:
    """
    AI가 제안한 계획(plan)을 검증해 Python이 실제로 실행할 수 있는 형태로
    정규화한다. tool_calls가 빈 리스트인 것 자체는 유효하다(지원하지 않는
    요청만 들어와서 실행할 도구가 없는 정상적인 경우) - 그 경우는 run_location_
    agent()가 unsupported_requests 존재 여부로 다시 한 번 판단한다.

    확인하는 것:
    1. plan이 올바른 구조(dict + tool_calls가 리스트)인지
    2. 각 tool_call의 "tool"이 ALLOWED_TOOLS 안에 있는지(없으면 전체 계획 거부)
    3. 도구 호출 수가 MAX_TOOL_CALLS를 넘지 않는지
    4. radius_m은 AI가 뭘 제안했든 항상 resolved_radius_m으로 강제 치환(다르면
       notes에 기록)
    5. max_results는 AI가 제안했다면 1~MAX_RESULTS_LIMIT로 clamp, 아니면 화면
       기본값(ui_max_results) 사용
    6. lat/lon 필드가 섞여 있으면 완전히 무시하고 notes에 기록(검색 중심 보호)
    7. 동일한 (도구, 반경, 표시개수) 조합이 중복 제안되면 한 번만 실행

    Returns:
        {"status": "ok", "tool_calls": [...], "notes": [...]} | {"status": "rejected", "reason": str}
    """
    if not isinstance(plan, dict):
        return {"status": "rejected", "reason": "AI 응답이 JSON 객체가 아닙니다."}

    raw_calls = plan.get("tool_calls")
    if not isinstance(raw_calls, list):
        return {"status": "rejected", "reason": "tool_calls가 리스트가 아닙니다."}
    if len(raw_calls) > MAX_TOOL_CALLS:
        return {"status": "rejected", "reason": f"제안된 도구 호출이 너무 많습니다(최대 {MAX_TOOL_CALLS}개)."}

    notes: list[str] = []
    normalized: list[dict] = []
    seen_signatures: set[tuple] = set()

    for i, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            return {"status": "rejected", "reason": f"{i + 1}번째 도구 호출 형식이 올바르지 않습니다."}

        tool = call.get("tool")
        if tool not in ALLOWED_TOOLS:
            return {"status": "rejected", "reason": f"허용되지 않은 도구 '{tool}'입니다."}

        if "lat" in call or "lon" in call:
            notes.append("AI가 좌표를 제안했지만 무시하고 승인된 검색 중심 좌표만 사용했습니다.")

        reason = str(call.get("reason") or "").strip()[:200]
        entry: dict = {"tool": tool, "reason": reason}

        if tool != "compare_nearby_facilities":
            proposed_radius = call.get("radius_m")
            if proposed_radius is not None and proposed_radius != resolved_radius_m:
                notes.append(
                    f"AI가 제안한 반경({proposed_radius}m)을 무시하고 실제 적용 반경"
                    f"({resolved_radius_m}m)을 사용했습니다."
                )
            entry["radius_m"] = resolved_radius_m

            max_results = ui_max_results
            proposed_mr = call.get("max_results")
            if isinstance(proposed_mr, (int, float)) and not isinstance(proposed_mr, bool):
                max_results = max(1, min(int(proposed_mr), MAX_RESULTS_LIMIT))
            entry["max_results"] = max_results

        sig = (tool, entry.get("radius_m"), entry.get("max_results"))
        if sig in seen_signatures:
            notes.append(f"'{TOOL_LABELS.get(tool, tool)}' 도구가 중복 제안되어 한 번만 실행합니다.")
            continue
        seen_signatures.add(sig)
        normalized.append(entry)

    return {"status": "ok", "tool_calls": normalized, "notes": notes}


# ---------------------------------------------------------------------------
# 도구 실행 - 검증을 통과한 계획만, 승인된 좌표로만 실행
# ---------------------------------------------------------------------------
def _execute_tool_calls(lat: float, lon: float, tool_calls: list[dict]) -> list[dict]:
    log: list[dict] = []
    for call in tool_calls:
        tool = call["tool"]
        entry: dict = {"tool": tool, "reason": call.get("reason", "")}
        try:
            if tool == "find_nearby_bus_stops":
                entry["radius_m"] = call["radius_m"]
                entry["max_results"] = call["max_results"]
                entry["result"] = tool_find_nearby_bus_stops(lat, lon, call["radius_m"], call["max_results"])
            elif tool == "find_nearby_convenience_stores":
                entry["radius_m"] = call["radius_m"]
                entry["max_results"] = call["max_results"]
                entry["result"] = tool_find_nearby_convenience_stores(lat, lon, call["radius_m"], call["max_results"])
            elif tool == "compare_nearby_facilities":
                entry["result"] = tool_compare_nearby_facilities(lat, lon)
            entry["executed"] = True
            entry["error"] = None
        except Exception as exc:  # 허용 도구는 안정적이어야 하지만 방어적으로 한 번 더 감싼다
            entry["executed"] = False
            entry["error"] = str(exc)
        log.append(entry)
    return log


def _sanitize_unsupported_requests(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        request = str(item.get("request") or "").strip()[:150]
        reason = str(item.get("reason") or "").strip()[:200]
        if request:
            out.append({"request": request, "reason": reason})
    return out


def _sanitize_goals(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(g).strip()[:100] for g in raw[:10] if str(g).strip()]


_DEFAULT_FALLBACK_CALL = {
    "tool": "compare_nearby_facilities",
    "reason": "(기본 절차) AI 계획을 사용할 수 없어 반경별 기본 비교를 실행",
}


# ---------------------------------------------------------------------------
# 최상위 진입점
# ---------------------------------------------------------------------------
def run_location_agent(
    user_text: str,
    search_center: tuple[float, float],
    ui_radius_m: int,
    ui_max_results: int,
) -> dict:
    """
    pages/user.py의 '🤖 AI 분석 실행' 버튼 클릭 시에만 호출해야 한다(지도 이동·
    레이어 토글·반경 변경 등 다른 재실행에서는 호출하면 안 됨 - 호출 측이 보장).

    Args:
        user_text: 사용자가 입력한 자연어 요청.
        search_center: 반드시 사용자가 "승인"한 확정 좌표(st.session_state.
            search_center)만 넘긴다. map_click_candidate(미확정 좌표)는 여기
            넘기면 안 된다.
        ui_radius_m: 화면에서 현재 선택된 반경(300/500/1000). 자연어에 명시적
            반경이 없을 때 기본값으로 쓰인다.
        ui_max_results: 화면에서 현재 선택된 최대 표시 개수. AI가 max_results를
            제안하지 않았을 때 기본값으로 쓰인다.

    Returns:
        {
            "status": "ok" | "rejected_input",
            "message": str | None,              # status=="rejected_input"일 때만(예: 지원 안 하는 반경)
            "mode": "ai_planned" | "fallback_default" | None,
            "user_text": str,
            "search_center": (lat, lon),         # 이 결과가 어느 좌표에서 실행됐는지(이후 비교용)
            "resolved_radius_m": int | None,
            "radius_source": "explicit_text" | "ui_default" | None,
            "goals": [str, ...],
            "unsupported_requests": [{"request","reason"}, ...],
            "planned_tool_calls": [...] | None,  # AI가 "제안"한 원본(검증 전) - 표시 전용
            "executed_tool_calls": [...],         # Python이 실제로 실행한 도구 호출 로그만
            "notes": [str, ...],
            "planner_error": str | None,
        }
    """
    lat, lon = search_center

    extract = extract_explicit_radius_m(user_text)
    if extract["found"] and extract["radius_m"] is None:
        supported_labels = ", ".join(f"{r}m" if r != 1000 else "1km" for r in SUPPORTED_RADII_M)
        return {
            "status": "rejected_input",
            "message": f"'{extract['raw_text']}'은(는) 현재 지원하지 않는 반경입니다. {supported_labels}만 지원합니다.",
            "mode": None,
            "user_text": user_text,
            "search_center": search_center,
            "resolved_radius_m": None,
            "radius_source": None,
            "goals": [],
            "unsupported_requests": [],
            "planned_tool_calls": None,
            "executed_tool_calls": [],
            "notes": [],
            "planner_error": None,
        }

    if extract["found"]:
        resolved_radius_m = extract["radius_m"]
        radius_source = "explicit_text"
    else:
        resolved_radius_m = ui_radius_m if ui_radius_m in SUPPORTED_RADII_M else DEFAULT_RADIUS_M
        radius_source = "ui_default"

    planner_error: str | None = None
    plan: dict | None = None
    try:
        plan = call_location_planner(user_text, resolved_radius_m)
    except RuntimeError as exc:
        planner_error = str(exc)

    if planner_error is None:
        validated = validate_and_normalize_plan(plan, resolved_radius_m, ui_max_results)
    else:
        validated = {"status": "rejected", "reason": planner_error}

    notes: list[str] = []
    if validated["status"] == "ok":
        tool_calls = validated["tool_calls"]
        notes = list(validated["notes"])
        unsupported = _sanitize_unsupported_requests(plan.get("unsupported_requests") if isinstance(plan, dict) else None)
        goals = _sanitize_goals(plan.get("goals") if isinstance(plan, dict) else None)
        if not tool_calls and not unsupported:
            # AI가 실행할 도구도, 지원 불가 사유도 제시하지 않은 빈 응답 - 기본 절차로.
            mode = "fallback_default"
            tool_calls = [_DEFAULT_FALLBACK_CALL]
            notes.append("AI가 실행할 도구나 지원 불가 사유를 제시하지 않아 기본 절차로 전환했습니다.")
        else:
            mode = "ai_planned"
    else:
        mode = "fallback_default"
        tool_calls = [_DEFAULT_FALLBACK_CALL]
        unsupported = []
        goals = []
        if planner_error is None:
            notes.append(f"AI 계획을 검증하지 못해 기본 절차로 전환했습니다: {validated['reason']}")

    executed = _execute_tool_calls(lat, lon, tool_calls)

    return {
        "status": "ok",
        "message": None,
        "mode": mode,
        "user_text": user_text,
        "search_center": search_center,
        "resolved_radius_m": resolved_radius_m,
        "radius_source": radius_source,
        "goals": goals,
        "unsupported_requests": unsupported,
        "planned_tool_calls": plan.get("tool_calls") if isinstance(plan, dict) else None,
        "executed_tool_calls": executed,
        "notes": notes,
        "planner_error": planner_error,
    }
