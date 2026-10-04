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

[후보군·Critic 설명 - explanation_facts]  (analysis.explanation_facts.build_explanation_facts)
    관찰 내용에는 Python이 확정한 사실만 넣는다: 후보 역할·Critic 교체, 구별 축 점수·순위·강점/약점/중립,
    역할 구끼리의 축별 높은 순서, 지배 관계, 확보/전체 평가축 수와 미확보 축, 반영 못 한 조건, 판단 한계.
    LLM은 강점·약점 판정이나 숫자 대소관계를 스스로 추론하지 않고 이 사실을 자연어로 옮기기만 한다.
    검증도 같은 사실을 기준으로 결정적으로 한다(verify_fact_claims):
      - 판단 범위·과장 표현·미확보 축 단정(미확보 축을 강점/약점으로 말하는 것 포함)
      - 역할-구 일치, "N개 중 M개" 관계(평가축이면 실제 값)
      - 구의 축 판정: 약점을 강점/앞선다로, 강점을 약점/뒤진다로, '가장 많다/적다'를 1위/최하위가 아닌 축에 쓰면 거부
      - 두 구 비교("A가 B보다 …"), 지배·압도 관계는 확정 사실과 같은 방향이어야 함
      - 계산하지 않은 가정("가중치를 바꿔도 …")은 가정 결과가 없으면 거부
"""

from __future__ import annotations

import json
import re
from typing import Callable

from agent import llm
from agent.agent_loop import MAX_ANSWER_CHARS, _numbers_in, _parse_json
from analysis.candidates import AXIS_BY_CODE, EVALUATION_AXES
from analysis.explanation_facts import build_explanation_facts, render_explanation
from analysis.feedback import AXIS_ALIASES, MISSING_AXIS_ALIASES
from services.geo import DISTRICTS

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
- 통근시간, 주거비, 안전, 교육처럼 관찰 내용에 없는 정보에 대해 좋다·나쁘다·저렴하다 같은 판단을 하지 마세요.

[후보군 설명 - 관찰 내용에 explanation_facts가 있으면 반드시 지킬 것]
- explanation_facts는 Python이 이미 확정한 사실입니다. 이 안의 내용만 자연어로 옮기세요. 후보·역할·판정을 바꾸거나
  여기에 없는 비교(어느 구가 더 많다/적다, 앞선다/뒤진다, 지배한다)를 새로 만들지 마세요.
- 강점·약점은 regions[].strengths / weaknesses / neutral 목록 그대로만 말하세요. 숫자를 비교해 스스로 판정하지 마세요.
  "가장 많다/높다"는 그 축 rank가 1일 때만, "가장 적다/낮다"는 최하위일 때만 쓰세요.
- 두 구를 비교할 때는 comparisons[].high_to_low 순서 그대로만 말하세요.
  dominance(지배 관계)는 dominant가 dominated보다 모든 평가축에서 높거나 같다는 뜻입니다. 방향을 바꾸지 마세요.
- 단순히 1위를 설명하지 말고 roles(최적·균형·대안)를 소개하세요. status가 "산출 불가"인 역할은 구 이름 없이 reason만 말하세요.
- 판단 범위(scope)를 먼저 밝히세요. 예: "현재 확보된 교통·의료·생활편의 기준에서는 성산구가 최적 후보입니다."
  "가장 살기 좋은 지역", "최고의 지역"처럼 단정하지 마세요.
- data_sufficiency: 전체 total_dimensions개 평가축 중 available_dimensions개만 확보했습니다. 이 순서를 바꾸지 마세요.
  missing_dimensions(미확보 축)는 "데이터를 확보하지 못해 판단에 포함하지 않았다"고만 말하고 강점·약점·저렴함 등을 말하지 마세요.
  not_reflected_conditions(입력했지만 반영하지 못한 조건)도 밝히세요.
- roles[].revised_from이 있으면 처음 후보와 바뀐 후보, 이유(dominated_by보다 모든 평가축에서 낮거나 같음)를 설명하세요.
  critic의 single_axis(한 축 의존)·close_gap(근소차)·concentration(쏠림) 경고가 있으면 함께 알려 주세요.
- 가정 시뮬레이션 결과를 언급할 때는 "가중치를 바꾼다면" 같은 가정임을 분명히 하세요. 실제 추천은 승인된 가중치 기준입니다.
- 설명은 4~7문장으로 간결하게 쓰세요.
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
            **({"tied": True} if row.get("tied") else {}),
            "components": {
                code: {"raw_value": _r1(comp.get("raw_value")), "normalized_score": _r1(comp.get("normalized_score"))}
                for code, comp in (row.get("component_scores") or {}).items()
            },
            # 점수에 쓰인 지표는 components에 이미 있으므로 참고 지표에서는 빼서 같은 값을 두 번 보내지 않는다
            "reference_indicators": {
                code: _r1(ind.get("raw_value"))
                for code, ind in (row.get("reference_indicators") or {}).items()
                if ind.get("raw_value") is not None and code not in (row.get("component_scores") or {})
            },
        })
        if not rows[-1]["reference_indicators"]:
            del rows[-1]["reference_indicators"]
    return rows


ROLE_STATUS_OK, ROLE_STATUS_UNAVAILABLE = "산출됨", "산출 불가"


def build_observation(*, score_result: dict, approved_weights: dict, available_indicators: dict,
                      selected_conditions: list, extra_indicators: dict, what_ifs: list,
                      candidate_review: dict | None = None) -> dict:
    total = sum(float(v) for v in approved_weights.values()) or 1.0
    observation = {
        "selected_conditions": selected_conditions,
        "approved_weights_percent": {k: _r1(float(v) / total * 100) for k, v in approved_weights.items()},
        "available_indicators": available_indicators,
        "indicator_labels": INDICATOR_LABELS,
        "ranking": _ranking_view(score_result),
        "extra_indicators": extra_indicators,
        "what_if_simulations": what_ifs,
    }
    facts = build_explanation_facts(score_result, candidate_review)
    if facts is not None:
        observation["explanation_facts"] = facts
        # 구별 순위·총점·축 값·점수·판정은 explanation_facts.regions에 있으므로, 순위표에는 facts에 없는 구의
        # 순위·총점만 남긴다(같은 숫자를 두 번 보내지 않아 LLM_MAX_INPUT_CHARS 안에 들어간다). 지표 이름은 dimensions에.
        in_facts = {r["region"] for r in facts["regions"]}
        observation["ranking"] = [{"rank": r["rank"], "region_name": r["region_name"], "total_score": r["total_score"]}
                                  for r in observation["ranking"] if r["region_name"] not in in_facts]
        del observation["indicator_labels"]
    return observation


# ---------------------------------------------------------------------------
# 판단 (AI 호출)
# ---------------------------------------------------------------------------
def call_reviewer(observation: dict, force_answer: bool, feedback: str | None, model: str) -> dict:
    # 공백 없는 JSON - 후보군·Critic이 추가된 관찰 내용이 LLM_MAX_INPUT_CHARS 안에 들어가도록
    lines = ["[관찰 내용]", json.dumps(observation, ensure_ascii=False, separators=(",", ":"))]
    if feedback:
        lines.append(f"[이전 설명이 검증에서 거부된 이유]\n{feedback}\n이 문제를 고쳐서 다시 설명하세요.")
    lines.append("[중요] 더 이상 추가 도구를 쓸 수 없습니다. 반드시 action을 \"answer\"로 하세요."
                 if force_answer else "설명할 수 있으면 answer, 정보가 부족하면 call_tools 로 응답하세요.")
    if observation.get("explanation_facts"):
        lines.append("[참고] 설명 문장은 다음 단계에서 Python이 확정 사실로 만듭니다. 추가 조회가 필요 없으면 "
                     "{\"action\": \"answer\", \"answer\": \"\"} 로만 응답하세요.")
    try:
        response = llm.chat(
            model=model,
            messages=[{"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                      {"role": "user", "content": "\n\n".join(lines)}],
            options={"temperature": 0.0},
            think=False,  # qwen3.5 thinking이 토큰을 다 써서 응답이 비는 문제(CLAUDE.md, DEC-09)
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
    facts = observation.get("explanation_facts") or {}
    for region in facts.get("regions") or []:
        scores.add(region["total_score"])
        for axis in region["axes"].values():
            counts.add(axis["value"])
            scores.add(axis["score"])
    sufficiency = facts.get("data_sufficiency") or {}
    for key in ("available_dimensions", "total_dimensions"):  # "6개 평가축 중 3개"
        if sufficiency.get(key) is not None:
            counts.add(float(sufficiency[key]))
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
    facts = observation.get("explanation_facts")
    if facts:
        ok, why = verify_fact_claims(text, facts, list(DISTRICTS.values()),
                                     has_what_if=bool(observation.get("what_if_simulations")))
        if not ok:
            return False, why
    return True, ""


OVERCLAIM_PHRASES = ("가장 살기 좋은", "살기 가장 좋은", "가장 좋은 지역", "최고의 지역", "최고의 동네", "완벽한")
LIMIT_MARKERS = ("미확보", "확보되지", "확보하지", "확보 못", "반영하지", "반영되지", "반영 못", "포함하지", "포함되지",
                 "데이터가 없", "자료가 없", "알 수 없", "판단하지", "산출할 수 없", "산출 불가", "추정하지")
POSITIVE_MARKERS = ("앞서", "앞선", "앞섭", "강점", "우수", "풍부", "많아", "많고", "많은", "많습", "많다",
                    "높아", "높고", "높은", "높습", "높다", "최다")
NEGATIVE_MARKERS = ("약점", "약하", "약한", "약해", "뒤처", "뒤지", "뒤져", "부족", "떨어", "적고", "적어", "적은",
                    "적습", "적다", "낮아", "낮고", "낮은", "낮습", "낮다", "최하")
MAX_MARKERS = ("가장 많", "가장 높", "최다", "가장 앞")
MIN_MARKERS = ("가장 적", "가장 낮", "최하", "가장 약", "가장 뒤")
HYPOTHETICAL_MARKERS = ("바꾸더라도", "바꿔도", "바꾸어도", "바뀌더라도", "바꾸면", "바꾼다면")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")
_CLAUSE_SPLIT = re.compile(r"[,，;]|지만|으며|이며|이고|이나|(?<=[가-힣])고\s")
_COUNT_RELATION = re.compile(r"(\d+)\s*개\s*(?:평가\s*축\s*|축\s*)?중(?:에서)?\s*(\d+)\s*개")
# 역할 언급: "최적 후보", "균형과 대안 역할", "균형·대안 후보" 등
_ROLE_LABEL = re.compile(r"최적|균형|대안|가성비")
_ROLE_PHRASE = re.compile(r"((?:최적|균형|대안|가성비)(?:\s*(?:과|와|및|·|,|이자|이며)\s*(?:최적|균형|대안|가성비))*)\s*(?:후보|역할)")
_AXIS_ALIASES_BY_LABEL = {AXIS_BY_CODE[code]: aliases for code, aliases in AXIS_ALIASES.items()}


def _axes_in(clause: str) -> list[str]:
    return [axis for axis, aliases in _AXIS_ALIASES_BY_LABEL.items() if any(a in clause for a in aliases)]


def _has(clause: str, markers) -> bool:
    return any(m in clause for m in markers)


def verify_fact_claims(text: str, facts: dict, region_names: list[str], has_what_if: bool = False) -> tuple[bool, str]:
    """explanation_facts와 다른 내용을 말하면 거부한다(결정적 검사). (통과 여부, 첫 위반 사유)"""
    found = fact_violations(text, facts, region_names, has_what_if)
    return (False, found[0]) if found else (True, "")


def fact_violations(text: str, facts: dict, region_names: list[str], has_what_if: bool = False) -> list[str]:
    """verify_fact_claims와 같은 규칙으로 위반을 '모두' 모은다(순서 보존, 중복 제거). 기본 설명 대비 새 위반 비교용."""
    out: list[str] = []
    for phrase in OVERCLAIM_PHRASES:
        if phrase in text:
            out.append(f"'{phrase}'처럼 단정하는 표현은 쓸 수 없습니다. 확보된 평가축 기준의 후보라고 범위를 밝히세요.")
    sufficiency = facts.get("data_sufficiency") or {}
    if sufficiency.get("missing_dimensions") and "확보" not in text:
        out.append("판단 범위(현재 확보된 평가축 기준)를 밝히지 않았습니다.")
    if not has_what_if and _has(text, HYPOTHETICAL_MARKERS) and "가중치" in text:
        out.append("가중치를 바꾼 경우는 계산하지 않았습니다. 가정 결과를 말하지 마세요.")
    sentences = [x for x in _SENTENCE_SPLIT.split(text) if x.strip()]

    missing_keywords = {kw: axis for axis in sufficiency.get("missing_dimensions") or []
                        for kw in MISSING_AXIS_ALIASES.get(axis, (axis,))}
    for sentence in sentences:
        hit = next((kw for kw in missing_keywords if kw in sentence), None)
        if hit is None:
            continue
        if _has(sentence, ("강점", "약점")):
            out.append(f"미확보 평가축({missing_keywords[hit]})을 강점·약점으로 표현했습니다.")
        elif not _has(sentence, LIMIT_MARKERS):
            out.append(f"미확보 평가축({missing_keywords[hit]})을 데이터가 있는 것처럼 언급했습니다. "
                       "미확보라 판단에 포함하지 않았다고만 말하세요.")

    roles = {r["role"]: r for r in facts.get("roles") or []}
    carried = None  # 구 이름이 없는 문장은 직전 문장에서 마지막으로 말한 구를 주어로 본다(주어 생략)
    for sentence in sentences:
        named = [name for name in region_names if name in sentence]
        subjects = named or ([carried] if carried else [])
        for phrase in _ROLE_PHRASE.finditer(sentence):
            for label in _ROLE_LABEL.findall(phrase.group(1)):
                role = roles.get(label)
                if role is None or not subjects:
                    continue
                if role["status"] == ROLE_STATUS_OK and role["region"] not in subjects:
                    out.append(f"{label} 후보는 {role['region']}인데 다른 구({', '.join(subjects)})를 "
                               f"{label} 후보·역할로 설명했습니다.")
                if role["status"] != ROLE_STATUS_OK and named and not _has(sentence, LIMIT_MARKERS):
                    out.append(f"{label} 후보는 산출되지 않았는데 구 이름을 붙여 설명했습니다.")
        if named:
            carried = max(named, key=sentence.rindex)

    total, available = sufficiency.get("total_dimensions"), sufficiency.get("available_dimensions")
    for sentence in sentences:
        for n_text, m_text in _COUNT_RELATION.findall(sentence):
            n, m = int(n_text), int(m_text)
            if m > n:
                out.append(f"'{n}개 중 {m}개'는 성립하지 않습니다(부분이 전체보다 큽니다).")
            elif "축" in sentence and total is not None and (n, m) != (total, available):
                out.append(f"평가축은 {total}개 중 {available}개를 확보했는데 '{n}개 중 {m}개'라고 설명했습니다.")

    _region_axis_violations(sentences, facts, region_names, out)
    _dominance_violations(sentences, facts, region_names, out)
    return list(dict.fromkeys(out))


def _region_axis_violations(sentences: list[str], facts: dict, region_names: list[str], out: list[str]) -> None:
    """
    구별 축 판정·두 구 비교를 확정 사실과 대조한다. 주어는 절에 나온 구(한국어 주어 생략을 고려해 다음 문장까지 유지).
    한 절에 구가 둘이면 'A가 B보다/에 비해' 형태만 비교로 검사하고, 그 밖의 다중 구 절은 모호해서 건너뛴다.
    """
    regions = {r["region"]: r for r in facts.get("regions") or []}
    max_rank = len(region_names)
    subject = None
    for sentence in sentences:
        for clause in _CLAUSE_SPLIT.split(sentence):
            if not clause or not clause.strip():
                continue
            named = sorted((n for n in region_names if n in clause), key=clause.index)
            if len(named) >= 2:
                subject = None
                a, b = named[0], named[1]
                if a in regions and b in regions and (f"{b}보다" in clause or f"{b}에 비해" in clause):
                    for axis in _axes_in(clause):
                        if axis not in regions[a]["axes"]:
                            continue
                        sa, sb = regions[a]["axes"][axis]["score"], regions[b]["axes"][axis]["score"]
                        if _has(clause, POSITIVE_MARKERS) and not _has(clause, NEGATIVE_MARKERS) and sa <= sb:
                            out.append(f"{axis}에서 {a}는 {b}보다 높지 않은데 앞선다고 설명했습니다.")
                        if _has(clause, NEGATIVE_MARKERS) and not _has(clause, POSITIVE_MARKERS) and sa >= sb:
                            out.append(f"{axis}에서 {a}는 {b}보다 낮지 않은데 뒤진다고 설명했습니다.")
                continue
            if len(named) == 1:
                subject = named[0]
            if subject not in regions:
                continue
            facts_r = regions[subject]
            _attributed_number_violations(clause, subject, facts_r, out)
            for axis in _axes_in(clause):
                if axis not in facts_r["axes"]:
                    continue
                rank = facts_r["axes"][axis]["rank"]
                if "강점" in clause and axis not in facts_r["strengths"]:
                    out.append(f"{subject}의 {axis}은(는) 강점이 아닌데 강점이라고 설명했습니다.")
                if "약점" in clause and axis not in facts_r["weaknesses"]:
                    out.append(f"{subject}의 {axis}은(는) 약점이 아닌데 약점이라고 설명했습니다.")
                if _has(clause, MAX_MARKERS) and rank != 1:
                    out.append(f"{subject}의 {axis}은(는) 5개 구 중 {rank}위인데 가장 많다/높다고 설명했습니다.")
                if _has(clause, MIN_MARKERS) and rank != max_rank:
                    out.append(f"{subject}의 {axis}은(는) 5개 구 중 {rank}위인데 가장 적다/낮다고 설명했습니다.")
                if _has(clause, POSITIVE_MARKERS) and not _has(clause, NEGATIVE_MARKERS) and axis in facts_r["weaknesses"]:
                    out.append(f"{subject}의 {axis}은(는) 약점인데 앞서거나 많다고 설명했습니다.")
                if _has(clause, NEGATIVE_MARKERS) and not _has(clause, POSITIVE_MARKERS) and axis in facts_r["strengths"]:
                    out.append(f"{subject}의 {axis}은(는) 강점인데 약하거나 적다고 설명했습니다.")


_ATTRIBUTED_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(점|개|곳|위)")


def _attributed_number_violations(clause: str, subject: str, facts_r: dict, out: list[str]) -> None:
    """
    한 구를 주어로 축을 말하는 절의 'N점/N개/N위'는 그 구의 그 축 값이어야 한다(다른 구의 숫자를 가져오지 않게).
    점수는 축 점수 또는 그 구의 종합점수, 순위는 축 순위 또는 종합 순위를 허용한다.
    """
    axes = [a for a in _axes_in(clause) if a in facts_r["axes"]]
    if not axes:
        return
    allowed = {"점": {facts_r["total_score"]} | {facts_r["axes"][a]["score"] for a in axes},
               "개": {facts_r["axes"][a]["value"] for a in axes},
               "위": {float(facts_r["rank"])} | {float(facts_r["axes"][a]["rank"]) for a in axes}}
    allowed["곳"] = allowed["개"]
    allowed["점"] |= {float(int(v)) for v in allowed["점"] if v == int(v)}
    for number, unit in _ATTRIBUTED_NUMBER.findall(clause):
        value = float(number.replace(",", ""))
        if value not in allowed[unit]:
            out.append(f"{subject}의 {'·'.join(axes)} 값이 아닌 숫자({number}{unit})를 {subject}의 값처럼 설명했습니다. "
                       "explanation_facts.regions에 있는 그 구의 값만 쓰세요.")


def _dominance_violations(sentences: list[str], facts: dict, region_names: list[str], out: list[str]) -> None:
    """'A가 B를 지배/압도한다'는 확정된 dominance와 같은 방향이어야 한다('B에 지배된다' 같은 수동형은 반대로 읽음)."""
    pairs = {(d["dominant"], d["dominated"]) for d in facts.get("dominance") or []}
    for sentence in sentences:
        for clause in _CLAUSE_SPLIT.split(sentence):
            if not clause or not _has(clause, ("지배", "압도")):
                continue
            named = sorted((n for n in region_names if n in clause), key=clause.index)
            if len(named) < 2:
                continue
            passive = _has(clause, ("지배되", "지배당", "압도당", "압도되", "에 의해"))
            first, others = named[0], named[1:]
            for other in others:
                pair = (other, first) if passive else (first, other)
                if pair not in pairs:
                    out.append(f"{pair[0]}가 {pair[1]}를 모든 평가축에서 앞선다(지배)는 사실은 확인되지 않았습니다.")


def python_summary(observation: dict) -> str:
    fact_rows = [{"rank": r["rank"], "region_name": r["region"], "total_score": r["total_score"]}
                 for r in (observation.get("explanation_facts") or {}).get("regions") or []]
    rows = sorted(fact_rows + list(observation.get("ranking") or []), key=lambda r: r["rank"])
    if not rows:
        return "계산된 추천 결과가 없습니다."
    labels = INDICATOR_LABELS
    weights = ", ".join(f"{labels.get(k, k)} {v}%" for k, v in (observation.get("approved_weights_percent") or {}).items())
    lines = [f"승인된 가중치({weights}) 기준 시설 수 상대 비교 결과입니다."]
    fact_regions = {r["region"]: r for r in (observation.get("explanation_facts") or {}).get("regions") or []}
    for row in rows[:3]:
        if "components" in row:
            parts = ", ".join(f"{labels.get(c, c)} {int(v['raw_value']) if v['raw_value'] is not None else '-'}개"
                              for c, v in row["components"].items())
        else:  # explanation_facts가 있으면 축 값은 그쪽에 있다
            axes = (fact_regions.get(row["region_name"]) or {}).get("axes") or {}
            parts = ", ".join(f"{axis} {int(v['value'])}개" for axis, v in axes.items()) or "-"
        lines.append(f"- {row['rank']}위 {row['region_name']}: {row['total_score']}점 ({parts})")
    for sim in observation.get("what_if_simulations") or []:
        top = (sim.get("ranking") or [{}])[0]
        w = ", ".join(f"{labels.get(k, k)} {v}%" for k, v in sim.get("weights_percent", {}).items())
        lines.append(f"- (가정) 가중치를 {w}로 바꾸면 1위는 {top.get('region_name', '-')}입니다. 실제 추천에는 반영되지 않았습니다.")
    facts = observation.get("explanation_facts")
    if facts:
        lines.append(f"{facts['scope']}에서 Agent가 고른 정착 후보:")
        regions = {r["region"]: r for r in facts["regions"]}
        for role in facts["roles"]:
            if role["status"] == ROLE_STATUS_OK:
                extra = (f" (Critic이 처음 고른 {role['revised_from']['region']}에서 교체)"
                         if role.get("revised_from") else "")
                region = regions.get(role["region"], {})
                profile = (f" · 강점: {', '.join(region.get('strengths') or []) or '없음'}"
                           f" · 약점: {', '.join(region.get('weaknesses') or []) or '없음'}")
                lines.append(f"- {role['role']} 후보 {role['region']}: {role['reason']}{extra}{profile}")
            else:
                lines.append(f"- {role['role']} 후보: 산출 불가 - {role['reason']}")
        for check in facts["critic"]:
            if check["code"] == "single_axis":
                lines.append(f"- 주의: {check['region']}의 점수는 {check['axis']} 한 축에 크게 기대고 있습니다.")
            elif check["code"] == "close_gap":
                lines.append(f"- 주의: {check['first']}와 {check['second']}의 종합점수 차이가 작아 순위가 바뀔 수 있습니다.")
        sufficiency = facts["data_sufficiency"]
        lines.append(f"- 전체 {sufficiency['total_dimensions']}개 평가축 중 {sufficiency['available_dimensions']}개만 확보했습니다.")
        if sufficiency["missing_dimensions"]:
            lines.append(f"- 미확보 평가축({', '.join(sufficiency['missing_dimensions'])})은 판단에 포함하지 않았습니다.")
        if sufficiency["not_reflected_conditions"]:
            lines.append(f"- 입력하셨지만 반영하지 못한 조건: {', '.join(sufficiency['not_reflected_conditions'])}")
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
    candidate_review: dict | None = None,
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
                                 extra_indicators=extra_indicators, what_ifs=what_ifs,
                                 candidate_review=candidate_review)

    for round_no in range(1, max_rounds + 1):
        observation = observe()
        force = round_no == max_rounds or feedback is not None or no_new
        try:
            decision = call_reviewer(observation, force, feedback, model)
        except RuntimeError as exc:
            review_error = str(exc)
            steps.append({"round": round_no, "action": "error", "reason": review_error})
            break

        if candidate_review is not None and (decision["action"] == "answer" or force):
            # 후보군이 있으면 설명은 공용 경로(explain_from_facts)가 만든다 - 여기서는 "추가 도구 불필요" 판단만 받는다
            steps.append({"round": round_no, "action": "answer", "note": "추가 조회 종료 - 설명은 Python 기본 설명 + AI 다듬기"})
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

    if candidate_review is not None:
        explanation = explain_from_facts(score_result=score_result, candidate_review=candidate_review, model=model)
        if explanation["final_answer"] is not None:
            steps.extend(explanation["agent_steps"])
            return {"agent_steps": steps, "review_tool_calls": review_log, "what_if_results": what_ifs,
                    "extra_indicators": extra_indicators, "final_answer": explanation["final_answer"],
                    "review_error": review_error or explanation["review_error"]}

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


PARAPHRASE_SYSTEM_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 문장 다듬기 도우미입니다.
[기본 설명]은 Python이 실제 공공데이터로 이미 확정한 설명입니다. 이주 예정자가 읽기 쉬운 자연스러운 한국어로 다듬기만 하세요.

[규칙 - 반드시 지킬 것]
- 기본 설명에 없는 사실·비교·숫자·후보 역할을 추가하지 마세요. 숫자는 기본 설명에 있는 것만 그대로 쓰세요(생략은 가능).
- 구 이름과 후보 역할(최적·균형·대안·가성비), 구 이름과 강점·약점의 짝을 바꾸지 마세요.
- 다음은 빠뜨리지 마세요: 판단 범위("현재 확보된 … 기준"), 각 후보 역할과 구, 산출 불가 역할과 이유,
  미확보 평가축, 반영하지 못한 조건, 판단 한계, Critic 교체가 있으면 그 이유.
- "가장 살기 좋은 지역" 같은 단정 표현은 쓰지 마세요.
- 반드시 JSON으로만 응답하세요: {"answer": "다듬은 설명"}
"""
PARAPHRASE_ATTEMPTS = 1          # 재시도하지 않는다 - 실패하면 기본 설명을 그대로 쓴다
MAX_PARAPHRASE_CHARS = 1500


def validate_paraphrase(text: str, base_text: str, facts: dict) -> tuple[bool, str]:
    """
    AI가 다듬은 문장 검증. 기본 설명(base_text)은 확정 사실로 만든 신뢰 기준이다.
    - 숫자: 기본 설명에 있는 숫자만(새 숫자 금지), 배·시간·금액 단위 금지
    - 사실: fact_violations 중 기본 설명에는 없던 '새 위반'이 있으면 거부(기본 설명 자체에서 나오는 휴리스틱 오탐은 무시)
    - 빠뜨림: 산출된 역할의 구 이름, 역할 이름, 미확보 평가축, 반영 못 한 조건이 남아 있어야 함
    """
    t = (text or "").strip()
    if not t:
        return False, "AI가 빈 문장을 돌려주었습니다."
    if len(t) > MAX_PARAPHRASE_CHARS:
        return False, f"AI 문장이 너무 깁니다({len(t)}자)."
    for number, unit in _UNIT_RE.findall(t):
        if unit.lower() in _FORBIDDEN_UNITS:
            return False, f"기본 설명에 없는 단위의 값({number}{unit})을 썼습니다."
    new_numbers = sorted(_numbers_in(t) - _numbers_in(base_text))
    if new_numbers:
        return False, f"기본 설명에 없는 숫자를 썼습니다: {', '.join(new_numbers[:5])}"
    regions = list(DISTRICTS.values())
    base_violations = set(fact_violations(base_text, facts, regions))
    new_violations = [v for v in fact_violations(t, facts, regions) if v not in base_violations]
    if new_violations:
        return False, new_violations[0]
    required = []
    for role in facts["roles"]:
        required.append(role["role"])
        if role["status"] == ROLE_STATUS_OK:
            required.append(role["region"])
    required += facts["data_sufficiency"]["missing_dimensions"] + facts["data_sufficiency"]["not_reflected_conditions"]
    missing = [item for item in dict.fromkeys(required) if item not in t]
    if missing:
        return False, f"기본 설명의 내용을 빠뜨렸습니다: {', '.join(missing)}"
    return True, ""


def explain_from_facts(*, score_result: dict, candidate_review: dict | None, model: str, use_llm: bool = True) -> dict:
    """
    최초 추천과 피드백 재평가가 함께 쓰는 설명 경로:
        candidate_review -> explanation_facts -> Python 기본 설명(항상 사실) -> (선택) AI 다듬기 1회 -> 검증
        -> 통과하면 다듬은 문장, 아니면(시간 초과·빈 응답·검증 실패 포함) 기본 설명 그대로.
    Returns: {"final_answer": {"text","source": "ai_paraphrase"|"deterministic","rejected_reason","deterministic_text"} | None,
              "agent_steps": [...], "review_error": str | None}
    """
    facts = build_explanation_facts(score_result, candidate_review)
    if facts is None:
        return {"final_answer": None, "agent_steps": [], "review_error": None}
    base = render_explanation(facts)
    final = {"text": base, "source": "deterministic", "rejected_reason": None, "deterministic_text": base}
    steps: list[dict] = [{"round": "설명", "action": "deterministic", "note": "Python이 확정 사실로 기본 설명 작성"}]
    error = None
    if use_llm:
        for _attempt in range(PARAPHRASE_ATTEMPTS):
            try:
                response = llm.chat(
                    model=model,
                    messages=[{"role": "system", "content": PARAPHRASE_SYSTEM_PROMPT},
                              {"role": "user", "content": f"[기본 설명]\n{base}"}],
                    options={"temperature": 0.0},
                    think=False,  # qwen3.5 thinking이 토큰을 다 써서 응답이 비는 문제(CLAUDE.md, DEC-09)
                )
                content = (response.get("message") or {}).get("content") or ""
            except Exception as exc:  # 시간 초과·연결 실패·호출 제한 등 - 기본 설명으로 진행
                error = f"{llm.backend_label()} 문장 다듬기 호출 실패: {exc}"
                final["rejected_reason"] = error
                steps.append({"round": "설명", "action": "error", "reason": error})
                break
            parsed = _parse_json(content) if content.strip() else None
            answer = str((parsed or {}).get("answer") or "").strip() if isinstance(parsed, dict) else ""
            ok, why = validate_paraphrase(answer, base, facts) if answer else (False, "AI가 빈 응답 또는 해석할 수 없는 응답을 돌려주었습니다.")
            steps.append({"round": "설명", "action": "paraphrase", "note": "새 사실·숫자 없음 검증 통과" if ok else f"검증 실패: {why}"})
            if ok:
                final.update(text=answer, source="ai_paraphrase")
                break
            final["rejected_reason"] = why
    return {"final_answer": final, "agent_steps": steps, "review_error": error}


def explain_candidates(*, score_result: dict, candidate_review: dict | None, selected_conditions: list,
                       model: str) -> dict:
    """피드백 재평가 뒤 설명 - 최초 추천과 같은 explain_from_facts 경로를 쓴다(selected_conditions는 호환용)."""
    return explain_from_facts(score_result=score_result, candidate_review=candidate_review, model=model)
