# AI 설명용 확정 사실(explanation_facts) - 강점·약점·대소관계를 Python이 미리 정한다
"""
AI 설명 단계(agent/planner_loop.py)에 넘기는 "이미 확정된 사실"만 만든다. LLM은 이 구조를 자연어로
옮길 뿐, 강점·약점 판정이나 숫자 대소관계를 스스로 추론하지 않는다. 검증(verify)도 같은 구조를 기준으로 한다.

[판정 규칙 - analysis/candidates.py와 같은 상수]
    축별 순위(5개 구 중, 동점은 같은 순위): 1~STRENGTH_MAX_RANK위 = 강점, WEAKNESS_MIN_RANK위~ = 약점, 그 사이 = 중립
    방향: 현재 확보된 3축(교통·의료·생활편의)은 모두 "높을수록 좋음"(시설 수). 주거비처럼 낮을수록 좋은 축은
          데이터가 확보되면 AXIS_DIRECTION에 추가한다(현재 미확보라 판정 대상 아님).
    비교: 역할을 맡은 구끼리 축마다 어느 쪽이 높은지(같으면 "같음")를 미리 적는다.

새 점수식 없음 - score_result의 normalized_score·raw_value와 candidate_review(확정된 역할·Critic)만 읽는다.
"""

from __future__ import annotations

from analysis.candidates import (
    AXIS_BY_CODE,
    AXIS_STATUS_MISSING,
    AXIS_STATUS_USED,
    EVALUATION_AXES,
    strength_max_rank,
    weakness_min_rank,
    _axis_ranks,
)

JUDGE_STRENGTH, JUDGE_WEAKNESS, JUDGE_NEUTRAL = "강점", "약점", "중립"
AXIS_DIRECTION: dict[str, str] = {  # 지표 코드 -> "높을수록 좋음" | "낮을수록 좋음"
    "bus_stop_count": "높을수록 좋음",
    "hospital_count": "높을수록 좋음",
    "convenience_store_count": "높을수록 좋음",
}
AXIS_INDICATOR_NAMES = {"bus_stop_count": "버스정류장 수", "hospital_count": "의료기관 수",
                        "convenience_store_count": "편의점 수"}


def _r1(value) -> float:
    return round(float(value), 1)


def judge(rank: int, region_count: int = 5) -> str:
    """비교 지역 수에 비례한 판정: 상위 40% 강점, 하위 40% 약점(5곳이면 1~2위 강점, 4~5위 약점)."""
    if rank <= strength_max_rank(region_count):
        return JUDGE_STRENGTH
    if rank >= weakness_min_rank(region_count):
        return JUDGE_WEAKNESS
    return JUDGE_NEUTRAL


def _join(items: list[str]) -> str:
    return ", ".join(items)


def render_explanation(facts: dict) -> str:
    """
    explanation_facts만으로 만드는 기본 설명(결정적). LLM이 실패하거나 검증을 통과하지 못하면 이 문장이 그대로
    화면에 나가므로, 사실만 담고 같은 사실 검증(agent/planner_loop.verify_fact_claims)을 항상 통과하는 문장 형태로 쓴다
    (예: '강점: A / 약점: B' 대신 'X의 강점은 A이고, 약점은 B입니다').
    """
    regions = {r["region"]: r for r in facts["regions"]}
    lines = [f"{facts['scope']}에서 Agent가 고른 정착 후보입니다."]
    for role in facts["roles"]:
        label = role["role"]
        if role["status"] != "산출됨":
            lines.append(f"- {label} 후보는 산출할 수 없습니다. {role['reason']}")
            continue
        r = regions.get(role["region"], {})
        line = f"- {label} 후보는 {role['region']}입니다(종합 {r.get('total_score')}점, {r.get('rank')}위). {role['reason']}."
        strengths, weaknesses = r.get("strengths") or [], r.get("weaknesses") or []
        if strengths and weaknesses:
            line += f" {role['region']}의 강점은 {_join(strengths)}이고, 약점은 {_join(weaknesses)}입니다."
        elif strengths:
            line += f" {role['region']}의 강점은 {_join(strengths)}이며 비교한 {facts['region_count']}개 지역 중 하위권인 축은 없습니다."
        elif weaknesses:
            line += f" {role['region']}의 약점은 {_join(weaknesses)}입니다."
        if role.get("revised_from"):
            first = role["revised_from"]
            line += (f" Critic이 처음 고른 {first['region']}는 {_join(first['dominated_by'])}에 비해 평가에 쓴 모든 축에서 "
                     f"낮거나 같아 {role['region']}(으)로 교체했습니다.")
        lines.append(line)
    for check in facts["critic"]:
        if check["code"] == "single_axis":
            lines.append(f"- 주의: {check['region']}의 종합점수는 {check['axis']} 한 축에 크게 기대고 있습니다.")
        elif check["code"] == "close_gap":
            lines.append(f"- 주의: {check['first']}와 {check['second']}의 종합점수 차이가 작아 가중치에 따라 순위가 바뀔 수 있습니다.")
        elif check["code"] == "concentration":
            lines.append(f"- 주의: 후보 역할이 모두 {check['region']} 한 곳으로 몰렸습니다.")
    suff = facts["data_sufficiency"]
    used = "·".join(d["axis"] for d in facts["dimensions"])
    lines.append(f"- 데이터 범위: 전체 {suff['total_dimensions']}개 평가축 중 {suff['available_dimensions']}개({used})만 확보해 비교했습니다.")
    if suff["missing_dimensions"]:
        lines.append(f"- 미확보 평가축({_join(suff['missing_dimensions'])})은 데이터가 없어 판단에 포함하지 않았습니다.")
    if suff["not_reflected_conditions"]:
        lines.append(f"- 입력하셨지만 대응 데이터가 없어 반영하지 못한 조건: {_join(suff['not_reflected_conditions'])}.")
    lines.append(f"- 판단 한계: {facts['scope']}의 시설 수 상대 비교이며 실제 거주 적합도를 확정하지 않습니다. "
                 "후보 단위가 시·군·구라 같은 지역 안의 생활권(동네) 차이는 판단하지 못합니다.")
    return "\n".join(lines)


def build_explanation_facts(score_result: dict, candidate_review: dict | None) -> dict | None:
    """
    Returns (candidate_review가 ok일 때만):
        {"scope": "현재 확보된 교통·의료·생활편의 기준",
         "data_sufficiency": {"available_dimensions", "total_dimensions", "missing_dimensions",
                              "unused_confirmed_dimensions", "not_reflected_conditions"},
         "dimensions": [{"axis", "indicator", "direction"}],                    # 평가에 쓴 축
         "regions": [{"region", "roles", "rank", "total_score", "strengths", "weaknesses", "neutral",
                      "axes": {축: {"value", "score", "rank"}}}],              # 요청 후보 + 역할 구
         "comparisons": [{"axis", "high_to_low": [구...], "tie"?: [구...]}],   # 역할 구끼리
         "dominance": [{"dominant", "dominated"}],                              # 모든 축에서 낮거나 같음
         "roles": [...], "critic": [...]}
    사용자에게 보이는 판단 한계 문장은 render_explanation()이 쓴다.
    """
    if not candidate_review or candidate_review.get("status") != "ok" or score_result.get("status") != "ok":
        return None
    rows = score_result["region_scores"]
    used_codes = [uc["indicator_code"] for uc in score_result.get("used_conditions", [])]
    normalized = {r["region_id"]: {c: r["component_scores"][c]["normalized_score"] for c in used_codes} for r in rows}
    ranks = _axis_ranks(normalized, used_codes)

    axes = candidate_review.get("axes") or []
    used_axes = [a["axis"] for a in axes if a["status"] == AXIS_STATUS_USED]
    missing = [a["axis"] for a in axes if a["status"] == AXIS_STATUS_MISSING]
    unused = [a["axis"] for a in axes if a["status"] not in (AXIS_STATUS_USED, AXIS_STATUS_MISSING)]
    checks = (candidate_review.get("critic") or {}).get("checks") or []
    coverage = next((c.get("facts") or {} for c in checks if c.get("code") == "coverage"), {})

    roles_by_region: dict[str, list[str]] = {}
    roles = []
    for r in candidate_review.get("roles") or []:
        if r.get("status") != "ok":
            roles.append({"role": r["role_label"], "status": "산출 불가", "reason": r["reason"]})
            continue
        roles_by_region.setdefault(r["region_name"], []).append(r["role_label"])
        entry = {"role": r["role_label"], "status": "산출됨", "region": r["region_name"], "reason": r["reason"]}
        if r.get("revised_from"):
            entry["revised_from"] = {"region": r["revised_from"]["region_name"],
                                     "dominated_by": r["revised_from"]["dominated_by"]}
        roles.append(entry)

    keep = [r for r in rows if r["region_name"] in roles_by_region
            or r["region_name"] in {t["region_name"] for t in score_result.get("top_candidates") or []}]
    regions = []
    for row in keep:
        rid = row["region_id"]
        axis_facts = {}
        for code in used_codes:
            rank = ranks[rid][code]
            axis_facts[AXIS_BY_CODE[code]] = {"value": _r1(row["component_scores"][code]["raw_value"]),
                                              "score": _r1(normalized[rid][code]), "rank": rank}
        regions.append({
            "region": row["region_name"], "roles": roles_by_region.get(row["region_name"], []),
            "rank": row["rank"], "total_score": _r1(row["total_score"]),
            # 축별 판정(강점·약점·중립)은 이 세 목록으로만 전달한다(축마다 중복 저장하지 않음 - 입력 길이 절약)
            "strengths": [a for a, f in axis_facts.items() if judge(f["rank"], len(rows)) == JUDGE_STRENGTH],
            "weaknesses": [a for a, f in axis_facts.items() if judge(f["rank"], len(rows)) == JUDGE_WEAKNESS],
            "neutral": [a for a, f in axis_facts.items() if judge(f["rank"], len(rows)) == JUDGE_NEUTRAL],
            "axes": axis_facts,
        })

    role_regions = list(dict.fromkeys(r["region"] for r in roles if r["status"] == "산출됨"))
    by_name = {r["region_name"]: r["region_id"] for r in rows}
    comparisons = []  # 역할 구끼리 축마다 높은 순서(같은 값은 tie에 함께)
    if len(role_regions) >= 2:
        for code in used_codes:
            order = sorted(role_regions, key=lambda name: -normalized[by_name[name]][code])
            entry = {"axis": AXIS_BY_CODE[code], "high_to_low": order}
            values = [round(normalized[by_name[n]][code], 6) for n in order]
            ties = [n for n, v in zip(order, values) if values.count(v) > 1]
            if ties:
                entry["tie"] = ties
            comparisons.append(entry)

    dominance = [{"dominant": d, "dominated": c["facts"]["region"]}
                 for c in checks if c.get("code") == "dominated" for d in c["facts"]["dominated_by"]]
    scope = f"현재 확보된 {'·'.join(used_axes)} 기준"
    return {
        "scope": scope,
        "region_count": len(rows),  # 비교한 지역 수(같은 유형끼리: 구 5 / 시 7 / 군 10)
        "region_names": [r["region_name"] for r in rows],  # 설명 검증이 지역 이름을 찾을 때 쓰는 전체 목록
        "data_sufficiency": {"available_dimensions": len(used_axes), "total_dimensions": len(EVALUATION_AXES),
                             "missing_dimensions": missing, "unused_confirmed_dimensions": unused,
                             "not_reflected_conditions": coverage.get("not_reflected", [])},
        # 평가에 쓴 축의 지표·방향만(미확보 축은 data_sufficiency.missing_dimensions에 있음)
        "dimensions": [{"axis": a["axis"], "indicator": AXIS_INDICATOR_NAMES[a["indicator_code"]],
                        "direction": AXIS_DIRECTION[a["indicator_code"]]} for a in axes if a["status"] == AXIS_STATUS_USED],
        "regions": regions,
        "comparisons": comparisons,
        "dominance": dominance,
        "roles": roles,
        "critic": [{"code": c["code"], "level": c["level"], **(c.get("facts") or {})} for c in checks
                   if c["code"] in ("single_axis", "close_gap", "concentration")],
    }
