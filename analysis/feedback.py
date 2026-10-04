# 피드백 재평가: 방향성 가중치 조정 규칙 + 단일 재평가 경로 + 피드백 이력
"""
사용자 피드백("의료를 더 중요하게", 슬라이더, "의료 80%")을 후보 재평가와 기억(feedback_history)으로
잇는 결정적 로직. LLM은 여기서 아무것도 계산하지 않는다 - 발화에서 "어느 축을, 어느 방향으로,
강도 표현이 있었는지"만 추출하고(agent/ollama_agent.py), 숫자는 전부 이 모듈이 정한다.

[방향성 조정 규칙]  adjust_weights()
    가중치는 교통·의료·생활편의 3축의 백분율(합 100)이다.
    - 높이기: 대상 축 가중치 × 배율 후 합 100으로 재정규화(다른 축끼리의 비율은 유지)
    - 낮추기: 대상 축 가중치 ÷ 배율 후 합 100으로 재정규화
      => 같은 강도로 "높이기 → 낮추기"를 하면 정확히 원래 가중치로 돌아온다.
    - 배율: 강도 표현 없음 = 보통 ×1.5, "조금/약간" = ×1.25, "훨씬/많이/매우" = ×2.0
    - 현재 0%인 축을 높이면 곱해도 0이라, 강도별 고정 비중(10/20/30%)을 주고 나머지 축을 비율대로 줄인다.
    - 0%인 축을 낮추거나, 다른 축이 모두 0%라 비율이 바뀌지 않는 경우는 바꾸지 않고 이유를 남긴다.

[재평가 경로]  reevaluate()
    슬라이더 "다시 비교하기", 숫자 자연어, 방향성 자연어, 되돌리기가 모두 이 함수 하나로
    analysis.scoring.compute_region_scores_from_weights() → analysis.candidates.build_candidate_set()을
    실행한다. 점수 계산식과 후보·Critic 로직은 그대로 재사용한다.

[피드백 이력]  history_entry()
    대상 축, 방향, 강도, 이전·변경 가중치, 승인 여부, 후보 역할 변화(최적·균형·대안), 1위 변화를 남긴다.
"""

from __future__ import annotations

from analysis import scoring
from analysis.candidates import AXIS_BY_CODE, build_candidate_set

STRENGTH_FACTORS: dict[str, float] = {"slight": 1.25, "normal": 1.5, "strong": 2.0}
ZERO_AXIS_SHARE: dict[str, float] = {"slight": 10.0, "normal": 20.0, "strong": 30.0}
STRENGTH_LABELS = {"slight": "조금", "normal": "보통", "strong": "많이"}
DIRECTION_LABELS = {"increase": "더 중요하게", "decrease": "덜 중요하게"}

SCORABLE_CODES: tuple[str, ...] = scoring.VALID_SCORABLE_INDICATOR_CODES
_EPS = 1e-9

# 축 이름 판정은 Python이 한다(LLM이 적은 축 이름을 그대로 믿지 않음). 확보 축은 지표 코드로,
# 미확보 축은 거절 사유로, 그 밖은 "존재하지 않는 평가축"으로 처리한다.
AXIS_ALIASES: dict[str, tuple[str, ...]] = {
    "bus_stop_count": ("교통", "버스", "정류장", "대중교통"),
    "hospital_count": ("의료", "병원", "의원", "진료", "의료기관"),
    "convenience_store_count": ("생활편의", "생활 편의", "편의점", "편의시설", "편의", "마트", "장보기"),
}
MISSING_AXIS_ALIASES: dict[str, tuple[str, ...]] = {
    "주거비": ("주거비", "집값", "월세", "전세", "임대료", "주거", "매매가"),
    "교육": ("교육", "학교", "학군", "학원"),
    "직장 접근성": ("직장 접근성", "직장", "통근", "출퇴근", "출근"),
}


def resolve_axis(name: str) -> tuple[str, str]:
    """('supported', 지표코드) | ('missing', 미확보 축 이름) | ('unknown', 원래 이름)"""
    text = str(name or "").strip()
    if text in SCORABLE_CODES:
        return "supported", text
    for code, aliases in AXIS_ALIASES.items():
        if text in aliases:
            return "supported", code
    for axis, aliases in MISSING_AXIS_ALIASES.items():
        if text in aliases:
            return "missing", axis
    return "unknown", text


def axis_mentioned(indicator_code: str, user_text: str) -> bool:
    """사용자 문장에 그 축을 가리키는 말이 실제로 있는지(LLM이 축을 지어내지 않았는지 확인)."""
    return any(alias in (user_text or "") for alias in AXIS_ALIASES.get(indicator_code, ()))


def weights_from_result(score_result: dict | None) -> dict[str, float]:
    """현재 적용 중인 결과의 가중치를 3축 백분율로(쓰지 않은 축은 0)."""
    used = {uc["indicator_code"]: uc["weight"] * 100 for uc in (score_result or {}).get("used_conditions", [])}
    return {code: float(used.get(code, 0.0)) for code in SCORABLE_CODES}


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    return {c: (w / total * 100 if total > _EPS else 0.0) for c, w in weights.items()}


def adjust_weights(current: dict[str, float], adjustments: list[dict]) -> dict:
    """
    Args:
        current: {indicator_code: 백분율} - 현재 적용 중인 가중치(합이 100이 아니어도 먼저 정규화).
        adjustments: [{"indicator_code", "direction": "increase"|"decrease", "strength": "slight"|"normal"|"strong"}]
            순서대로 적용한다.

    Returns:
        {"status": "ok" | "no_change", "before", "after", "steps": [...], "message"}
        steps[i]: {"indicator_code","axis","direction","strength","rule","before","after","changed","note"}
    """
    before = _normalize({c: float(current.get(c, 0.0)) for c in SCORABLE_CODES})
    weights = dict(before)
    steps = []
    for adj in adjustments:
        code, direction = adj["indicator_code"], adj["direction"]
        strength = adj.get("strength") or "normal"
        if code not in SCORABLE_CODES or direction not in DIRECTION_LABELS or strength not in STRENGTH_FACTORS:
            raise ValueError(f"잘못된 조정 요청: {adj}")
        factor = STRENGTH_FACTORS[strength]
        step_before = dict(weights)
        w = weights[code]
        others = sum(v for c, v in weights.items() if c != code)
        note = None
        if direction == "increase":
            if others <= _EPS:
                note = f"{AXIS_BY_CODE[code]}이(가) 이미 100%라 더 높일 수 없습니다."
                rule = "변경 없음"
            elif w <= _EPS:
                share = ZERO_AXIS_SHARE[strength]
                weights = {c: (share if c == code else v * (100 - share) / others) for c, v in weights.items()}
                rule = f"0%였던 축에 {share:g}%를 주고 나머지 축을 비율대로 줄임"
            else:
                weights[code] = w * factor
                weights = _normalize(weights)
                rule = f"×{factor:g} 후 합 100%로 재정규화"
        else:
            if w <= _EPS:
                note = f"{AXIS_BY_CODE[code]}은(는) 이미 0%라 더 낮출 수 없습니다."
                rule = "변경 없음"
            elif others <= _EPS:
                note = (f"다른 평가축이 모두 0%라 {AXIS_BY_CODE[code]}만 낮춰서는 비율이 바뀌지 않습니다. "
                        "함께 높일 축을 말씀해 주세요.")
                rule = "변경 없음"
            else:
                weights[code] = w / factor
                weights = _normalize(weights)
                rule = f"÷{factor:g} 후 합 100%로 재정규화"
        changed = any(abs(weights[c] - step_before[c]) > 1e-6 for c in SCORABLE_CODES)
        steps.append({
            "indicator_code": code, "axis": AXIS_BY_CODE[code], "direction": direction, "strength": strength,
            "strength_explicit": bool(adj.get("strength_explicit")), "rule": rule,
            "before": step_before, "after": dict(weights), "changed": changed, "note": note,
        })
    changed_any = any(s["changed"] for s in steps)
    return {
        "status": "ok" if changed_any else "no_change",
        "before": before,
        "after": weights,
        "steps": steps,
        "message": None if changed_any else " ".join(s["note"] for s in steps if s["note"]) or "가중치가 바뀌지 않습니다.",
    }


def reevaluate(weights: dict[str, float], candidate_count: int, unscored_inputs: list[str] | None = None,
               regions: list[dict] | None = None) -> dict:
    """모든 피드백이 공유하는 유일한 재평가 경로: 점수 재계산 → 후보·Critic 재산출."""
    score_result = scoring.compute_region_scores_from_weights(weights, candidate_count, regions=regions)
    return {"score_result": score_result, "candidate_review": build_candidate_set(score_result, unscored_inputs)}


def _role_regions(review: dict | None) -> dict[str, str | None]:
    return {
        r["role"]: (r.get("region_name") if r.get("status") == "ok" else None)
        for r in (review or {}).get("roles", [])
    }


def candidate_changes(before_review: dict | None, after_review: dict | None) -> list[dict]:
    """후보 역할별 이전·이후 구 이름. 산출 불가 역할은 None."""
    before, after = _role_regions(before_review), _role_regions(after_review)
    labels = {r["role"]: r["role_label"] for r in (after_review or before_review or {}).get("roles", [])}
    return [
        {"role": role, "role_label": labels.get(role, role), "before": before.get(role), "after": after.get(role),
         "changed": before.get(role) != after.get(role)}
        for role in dict.fromkeys(list(before) + list(after))
    ]


def _rounded(weights: dict[str, float]) -> dict[str, float]:
    return {c: round(v, 1) for c, v in weights.items()}


def history_entry(*, source: str, text: str | None, before_weights: dict[str, float],
                  after_weights: dict[str, float], approved: bool, adjustments: list[dict] | None = None,
                  before_result: dict | None = None, after_result: dict | None = None,
                  before_review: dict | None = None, after_review: dict | None = None) -> dict:
    """
    feedback_history 한 건. source: "slider" | "nl_weights" | "nl_direction" | "reset".
    승인하지 않은(무시한) 제안도 approved=False로 남기며, 이때 후보 변화는 비어 있다.
    """
    def top(result):
        rows = (result or {}).get("region_scores") or []
        return rows[0]["region_name"] if rows else None

    return {
        "source": source,
        "text": text,
        "targets": [
            {"axis": a.get("axis") or AXIS_BY_CODE.get(a["indicator_code"]), "indicator_code": a["indicator_code"],
             "direction": a["direction"], "strength": a.get("strength", "normal"),
             "strength_explicit": bool(a.get("strength_explicit"))}
            for a in (adjustments or [])
        ],
        "before_weights": _rounded(before_weights),
        "after_weights": _rounded(after_weights),
        "approved": approved,
        "top_before": top(before_result) if approved else None,
        "top_after": top(after_result) if approved else None,
        "candidate_changes": candidate_changes(before_review, after_review) if approved else [],
    }
