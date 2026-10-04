"""
방향성 가중치 피드백의 결정적 조정 규칙과 피드백 이력(G3).

"병원을 더 중요하게", "교통은 덜 봐도 돼"처럼 숫자 없이 방향만 있는 요청을
AI가 {지표: "increase" | "decrease"}로 분류하면, 실제 새 가중치는 이 모듈이
**현재 적용 중인 가중치**를 기준으로 정해진 규칙에 따라 계산한다. AI는 숫자를
만들지 않는다(DEC-01). 결과는 항상 사용자 승인 뒤에만 적용되며, 점수 계산은
여전히 analysis.scoring.compute_region_scores_from_weights()가 유일한 경로다
(DEC-03 - 이 모듈은 점수식을 건드리지 않고 가중치만 제안한다).

[조정 규칙]
- 모든 가중치는 percent(0~100)이며 계산 전에 합계 100으로 맞춘다.
- 한 번에 움직이는 폭(STEP_PCT): 보통 20%p, 강하게("훨씬", "가장") 30%p.
- 올리기만 있을 때: 올릴 지표마다 +STEP, 그만큼을 나머지 지표에서 현재 비중에
  비례해 뺀다(나머지가 부족하면 있는 만큼만 옮긴다).
- 내리기만 있을 때: 내릴 지표마다 -STEP(0 미만으로 내려가지 않음), 뺀 만큼을
  나머지 지표에 현재 비중에 비례해 나눈다(나머지가 전부 0이면 똑같이 나눈다).
- 올리기·내리기가 함께 있을 때: 내릴 지표에서 뺀 만큼을 올릴 지표에 똑같이
  나눠 준다. 언급하지 않은 지표는 그대로 둔다.
- 세 지표를 전부 올리거나 전부 내리라는 요청은 상대 가중치로 표현할 수 없으므로
  조정하지 않고 되묻는다.
- 소수 첫째 자리로 반올림하고, 반올림 오차는 가장 큰 가중치에 더해 합계를 정확히
  100.0으로 맞춘다.
"""

from __future__ import annotations

SUPPORTED_CODES = ("bus_stop_count", "hospital_count", "convenience_store_count")

INDICATOR_LABELS = {
    "bus_stop_count": "교통",
    "hospital_count": "의료",
    "convenience_store_count": "생활편의",
}

STEP_PCT = {"normal": 20.0, "strong": 30.0}

VALID_DIRECTIONS = ("increase", "decrease")

_EPS = 1e-9


def _normalize_to_100(weights: dict[str, float]) -> dict[str, float]:
    """지원 지표 3개를 모두 키로 갖고 합계 100인 percent 가중치로 맞춘다.
    합계가 0이면 3개 지표에 똑같이 나눈다."""
    base = {code: max(0.0, float(weights.get(code, 0.0) or 0.0)) for code in SUPPORTED_CODES}
    total = sum(base.values())
    if total <= _EPS:
        return {code: 100.0 / len(SUPPORTED_CODES) for code in SUPPORTED_CODES}
    return {code: value / total * 100.0 for code, value in base.items()}


def _round_to_100(weights: dict[str, float]) -> dict[str, float]:
    """소수 첫째 자리로 반올림하고 오차를 가장 큰 값에 몰아 합계를 100.0으로 맞춘다."""
    rounded = {code: round(value, 1) for code, value in weights.items()}
    residual = round(100.0 - sum(rounded.values()), 1)
    if abs(residual) > _EPS:
        largest = max(rounded, key=lambda c: (rounded[c], -SUPPORTED_CODES.index(c)))
        rounded[largest] = round(rounded[largest] + residual, 1)
    return rounded


def _distribute(amount: float, receivers: list[str], weights: dict[str, float]) -> None:
    """amount를 receivers에게 현재 비중에 비례해 더한다(전부 0이면 균등)."""
    if not receivers or amount <= _EPS:
        return
    pool = sum(weights[c] for c in receivers)
    for code in receivers:
        share = weights[code] / pool if pool > _EPS else 1.0 / len(receivers)
        weights[code] += amount * share


def _take(amount: float, donors: list[str], weights: dict[str, float]) -> float:
    """donors에게서 현재 비중에 비례해 최대 amount를 빼고, 실제로 뺀 양을 돌려준다."""
    pool = sum(weights[c] for c in donors)
    taken = min(amount, pool)
    if taken <= _EPS:
        return 0.0
    for code in donors:
        weights[code] -= taken * (weights[code] / pool)
        weights[code] = max(0.0, weights[code])
    return taken


def apply_direction(
    current_weights: dict[str, float],
    directions: dict[str, str],
    strength: str = "normal",
) -> dict:
    """
    현재 가중치에 방향성 요청을 결정적으로 적용한 새 가중치를 계산한다.

    Args:
        current_weights: {indicator_code: percent}. 지금 실제로 점수 계산에 쓰이는
            가중치(승인된 피드백 결과가 있으면 그것, 없으면 최초 추천 가중치).
        directions: {indicator_code: "increase" | "decrease"}.
        strength: "normal"(20%p) | "strong"(30%p). 그 외 값은 "normal"로 본다.

    Returns:
        {
            "status": "ok" | "no_change" | "invalid",
            "before": {code: percent, ...},   # 합계 100으로 맞춘 기준 가중치
            "after": {code: percent, ...} | None,
            "step": float,                    # 이번에 쓴 조정 폭(%p)
            "rule": str,                      # 화면에 보여줄 규칙 설명
            "message": str | None,            # no_change / invalid일 때 사용자 안내
        }
    """
    step = STEP_PCT.get(strength, STEP_PCT["normal"])
    before = _round_to_100(_normalize_to_100(current_weights))

    clean: dict[str, str] = {}
    for code, direction in (directions or {}).items():
        if code not in SUPPORTED_CODES or direction not in VALID_DIRECTIONS:
            return {
                "status": "invalid", "before": before, "after": None, "step": step, "rule": "",
                "message": "교통·의료·생활편의에 대한 '더/덜 중요하게' 요청만 조정할 수 있습니다.",
            }
        clean[code] = direction
    if not clean:
        return {
            "status": "invalid", "before": before, "after": None, "step": step, "rule": "",
            "message": "어떤 조건을 더(또는 덜) 중요하게 볼지 알려주세요.",
        }

    increase = [c for c in SUPPORTED_CODES if clean.get(c) == "increase"]
    decrease = [c for c in SUPPORTED_CODES if clean.get(c) == "decrease"]
    if len(increase) == len(SUPPORTED_CODES) or len(decrease) == len(SUPPORTED_CODES):
        return {
            "status": "invalid", "before": before, "after": None, "step": step, "rule": "",
            "message": (
                "가중치는 서로의 상대 비율이라 세 조건을 모두 올리거나 모두 내릴 수는 없습니다. "
                "가장 중요하게(또는 덜 중요하게) 볼 조건을 한두 개만 골라 주세요."
            ),
        }

    weights = dict(_normalize_to_100(current_weights))
    if increase and decrease:
        freed = 0.0
        for code in decrease:
            cut = min(step, weights[code])
            weights[code] -= cut
            freed += cut
        share = freed / len(increase)
        for code in increase:
            weights[code] += share
        rule = f"내릴 조건에서 각각 최대 {step:g}%p를 빼서 올릴 조건에 똑같이 나눴습니다."
    elif increase:
        others = [c for c in SUPPORTED_CODES if c not in increase]
        taken = _take(step * len(increase), others, weights)
        for code in increase:
            weights[code] += taken / len(increase)
        rule = f"올릴 조건에 {step:g}%p를 더하고, 그만큼을 나머지 조건에서 현재 비중대로 뺐습니다."
    else:
        others = [c for c in SUPPORTED_CODES if c not in decrease]
        freed = 0.0
        for code in decrease:
            cut = min(step, weights[code])
            weights[code] -= cut
            freed += cut
        _distribute(freed, others, weights)
        rule = f"내릴 조건에서 {step:g}%p를 빼고, 그만큼을 나머지 조건에 현재 비중대로 나눴습니다."

    after = _round_to_100(weights)
    if all(abs(after[c] - before[c]) < 0.05 for c in SUPPORTED_CODES):
        target = increase or decrease
        names = ", ".join(INDICATOR_LABELS[c] for c in target)
        limit = "최대(100%)" if increase else "최소(0%)"
        return {
            "status": "no_change", "before": before, "after": None, "step": step, "rule": rule,
            "message": f"{names} 가중치가 이미 {limit}라 더 조정할 수 없습니다.",
        }
    return {"status": "ok", "before": before, "after": after, "step": step, "rule": rule, "message": None}


def resolve_direction_proposal(proposal: dict, current_weights: dict[str, float]) -> dict:
    """
    interpret_weight_feedback()의 "adjust_direction" 결과를 현재 가중치 기준의
    구체적인 제안으로 바꾼다. 다른 type은 그대로 돌려준다.

    - 조정 성공: type을 "set_weights"로 바꾸고 weights(새 percent 가중치)와
      direction(before/after/rule)을 채운다 - 기존 승인 화면을 그대로 쓸 수 있다.
    - 조정 불가(no_change/invalid): type을 "ask_clarification"으로 바꾸고 안내 문구를
      message에 넣는다. 임의로 다른 숫자를 만들지 않는다.
    """
    if proposal.get("status") != "ok" or proposal.get("type") != "adjust_direction":
        return proposal

    outcome = apply_direction(
        current_weights, proposal.get("directions") or {}, proposal.get("strength") or "normal"
    )
    if outcome["status"] != "ok":
        return {
            "status": "ok",
            "type": "ask_clarification",
            "weights": None,
            "message": outcome["message"],
        }
    return {
        "status": "ok",
        "type": "set_weights",
        "weights": outcome["after"],
        "message": proposal.get("message"),
        "direction": {
            "directions": dict(proposal.get("directions") or {}),
            "strength": proposal.get("strength") or "normal",
            "before": outcome["before"],
            "after": outcome["after"],
            "step": outcome["step"],
            "rule": outcome["rule"],
        },
    }


def describe_directions(directions: dict[str, str]) -> str:
    """{'hospital_count': 'increase'} -> '의료 ↑'"""
    arrow = {"increase": "↑", "decrease": "↓"}
    return ", ".join(
        f"{INDICATOR_LABELS.get(c, c)} {arrow.get(d, d)}"
        for c, d in sorted(directions.items(), key=lambda kv: SUPPORTED_CODES.index(kv[0]) if kv[0] in SUPPORTED_CODES else 99)
    )


def format_weights(weights: dict[str, float] | None) -> str:
    """{'hospital_count': 60.0, ...} -> '교통 20% · 의료 60% · 생활편의 20%' (0%는 생략)"""
    if not weights:
        return "-"
    parts = [
        f"{INDICATOR_LABELS[c]} {weights.get(c, 0.0):g}%"
        for c in SUPPORTED_CODES
        if weights.get(c, 0.0) > 0
    ]
    return " · ".join(parts) if parts else "-"


# ---------------------------------------------------------------------------
# 피드백 이력 (Memory)
# ---------------------------------------------------------------------------
def weights_from_result(scoring_result: dict | None) -> dict[str, float]:
    """점수 결과(used_conditions)에서 percent 가중치를 꺼낸다(미사용 지표는 0)."""
    used = {
        uc["indicator_code"]: uc["weight"] * 100.0
        for uc in (scoring_result or {}).get("used_conditions", [])
    }
    return {code: round(used.get(code, 0.0), 1) for code in SUPPORTED_CODES}


def _top_region(scoring_result: dict | None) -> str | None:
    rows = (scoring_result or {}).get("region_scores") or []
    for row in rows:
        if row.get("rank") == 1:
            return row.get("region_name")
    return rows[0].get("region_name") if rows else None


def append_history(
    history: list[dict] | None,
    source: str,
    request_text: str | None,
    before_result: dict | None,
    after_result: dict | None,
    rule: str | None = None,
) -> list[dict]:
    """
    피드백 한 번(승인 또는 슬라이더 재계산)을 이력에 덧붙인 **새 목록**을 돌려준다.
    점수·순위는 넘겨받은 결과에서 그대로 읽기만 하고 새로 계산하지 않는다.

    source: "자연어(방향)" | "자연어(비율)" | "슬라이더" | "초기화"
    """
    entries = list(history or [])
    entries.append(
        {
            "round": len(entries) + 1,
            "source": source,
            "request": (request_text or "").strip() or None,
            "before": weights_from_result(before_result),
            "after": weights_from_result(after_result),
            "top_before": _top_region(before_result),
            "top_after": _top_region(after_result),
            "rule": rule,
        }
    )
    return entries


def history_rows(history: list[dict] | None) -> list[dict]:
    """화면 표로 보여줄 행 목록."""
    rows = []
    for entry in history or []:
        top_before, top_after = entry.get("top_before"), entry.get("top_after")
        if top_before and top_after and top_before != top_after:
            top_change = f"{top_before} → {top_after}"
        else:
            top_change = top_after or "-"
        rows.append(
            {
                "회차": entry["round"],
                "방식": entry["source"],
                "요청": entry.get("request") or "-",
                "변경 전": format_weights(entry["before"]),
                "변경 후": format_weights(entry["after"]),
                "1위 구": top_change,
            }
        )
    return rows
