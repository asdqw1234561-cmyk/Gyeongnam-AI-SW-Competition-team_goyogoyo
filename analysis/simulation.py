# 개선 시뮬레이션
"""
창원시 5개 구 중 한 곳의 시설 수를 "가상으로" 증감시켰을 때, 시설 수 기준 상대
비교 점수(analysis/scoring.py)와 5개 구 사이의 수치 차이가 어떻게 달라지는지
보여주는 가정 기반 시뮬레이션이다.

이것은 실제 정책 효과 예측이 아니다 - 실제 통근시간, 의료 접근성, 정주율, 인구
유입, 사업비 대비 효과 등은 이 모듈 어디에서도 계산하지 않는다. "시설 수가 이만큼
바뀌면 시설 수 기반 상대 점수가 이렇게 바뀐다"는 사실만 보여준다.

[원본 데이터 보호]
    data/region_indicators.csv는 이 모듈에서 전혀 쓰지(write) 않는다.
    services.region_data.get_all_changwon_regions()로 매번 새로 읽은 뒤
    copy.deepcopy()한 사본에만 가상값을 적용하고, 원본 리스트/딕셔너리는 절대
    변형(mutate)하지 않는다. simulate_facility_change()를 몇 번 호출하든 매번
    get_all_changwon_regions()부터 다시 시작하므로 가상 변경량이 누적되지 않는다.

[점수 계산 재사용]
    min-max 정규화·가중합·정렬은 전부 analysis.scoring.compute_region_scores_from_weights()
    (새로 추가된 regions 매개변수로 원본/가상 데이터를 각각 주입)를 그대로 쓴다.
    새로운 정규화·집계 계산식을 따로 구현하지 않는다. 정규화는 매번 "5개 구 전체"를
    기준으로 다시 계산되므로, 한 구의 값만 바꿔도 다른 구의 정규화 점수가 바뀔 수
    있다 - 선택한 구의 점수만 따로 증감시키지 않는다.
"""

from __future__ import annotations

import copy

from analysis.scoring import (
    CAVEATS,
    INDICATOR_CATEGORY,
    VALID_SCORABLE_INDICATOR_CODES,
    collect_confirmed_indicator,
    compute_region_scores_from_weights,
)
from services.region_data import get_all_changwon_regions

# 현재 시뮬레이션 대상 지표(요구사항: 이 3개뿐). VALID_SCORABLE_INDICATOR_CODES와
# 항상 같은 값을 쓰되, 시뮬레이션 모듈에서도 의미가 드러나도록 별칭을 둔다.
SIMULATABLE_INDICATOR_CODES: tuple[str, ...] = VALID_SCORABLE_INDICATOR_CODES

SIMULATION_CAVEATS: list[str] = [
    "이 결과는 가상의 시설 수 변화가 시설 수 기준 상대 비교 점수에 미치는 영향만 "
    "보여줍니다. 실제 의료 접근성 향상, 정주율 상승, 인구 유입 효과 등을 추정한 "
    "것이 아닙니다.",
    "가중치는 상대 비교 점수 계산에만 쓰이며, 실제 시설 수 자체에는 영향을 주지 "
    "않습니다.",
    *CAVEATS,
]


def _find_indicator(region: dict, category: str, indicator_code: str) -> dict | None:
    return next(
        (i for i in region["categories"].get(category, []) if i["indicator_code"] == indicator_code),
        None,
    )


def default_equal_weights(regions: list[dict]) -> dict[str, float]:
    """
    comparison_weights를 지정하지 않았을 때 쓰는 기본값: 지금 실제로 5개 구 전부
    확보된 지표끼리 동일 가중치(요구사항: "확보된 3개 지표를 동일하게 적용하는
    방식으로 시작"). 지표가 아직 다 확보되지 않은 미래 상황도 깨지지 않도록
    고정된 "3개"가 아니라 매번 실제로 확인해서 정한다.
    """
    confirmed_codes = [
        code for code in SIMULATABLE_INDICATOR_CODES
        if collect_confirmed_indicator(regions, code) is not None
    ]
    if not confirmed_codes:
        return {}
    equal_weight = 100.0 / len(confirmed_codes)
    return {code: equal_weight for code in confirmed_codes}


def simulate_facility_change(
    region_id: str,
    indicator_code: str,
    delta: int,
    comparison_weights: dict[str, float] | None = None,
) -> dict:
    """
    창원시 5개 구 중 region_id 한 곳의 indicator_code 시설 수를 delta만큼(정수,
    음수 가능) 가상으로 바꿨을 때, 5개 구 전체를 다시 정규화·채점한 전후 결과를
    돌려준다. 원본 데이터(data/region_indicators.csv)는 전혀 수정하지 않는다.

    Args:
        region_id: 창원시 5개 구 중 하나(예: "CW-JINHAE"). 그 외 값이면 오류.
        indicator_code: SIMULATABLE_INDICATOR_CODES(bus_stop_count/hospital_count/
            convenience_store_count) 중 하나. 그 외 값이거나, 해당 지표가 5개 구
            전부 확보되지 않았으면 오류(미확보 지표는 시뮬레이션 대상에서 제외).
        delta: 가상 증감량(정수, 예: +20, -15, 0). 적용 후 시설 수가 음수가 되면 오류.
        comparison_weights: {indicator_code: 0~100 상대 가중치}. 생략하면(None)
            현재 확보된 지표끼리 동일 가중치로 자동 설정한다(default_equal_weights).
            compute_region_scores_from_weights()에 그대로 전달되므로 합계가 100이
            아니어도 내부에서 정규화된다.

    Returns:
        status=="error"일 때:
            {"status": "error", "message": str}
        status=="ok"일 때:
            {
                "status": "ok",
                "region_id", "region_name",
                "indicator_code", "indicator_name",
                "actual_value": float,       # 실제(원본 CSV) 시설 수
                "simulated_value": float,    # 가상 시설 수 = actual_value + delta
                "delta": int,
                "source": str | None, "reference_date": str | None,  # 원본 데이터 출처/기준일
                "comparison_weights": {indicator_code: 0~100, ...},  # 입력받은(또는 자동 설정된) 가중치
                "facility_counts_before": {region_id: value, ...},   # 5개 구 전체, 변경 전
                "facility_counts_after": {region_id: value, ...},    # 5개 구 전체, 변경 후(선택 구만 다름)
                "baseline_scores": compute_region_scores_from_weights() 결과 전체(원본 데이터 기준),
                "simulated_scores": compute_region_scores_from_weights() 결과 전체(가상 데이터 기준),
                "score_change": {
                    region_id: {"region_name", "score_before", "score_after", "score_delta",
                                "rank_before", "rank_after"}, ...
                },
                "caveats": SIMULATION_CAVEATS,
            }
    """
    # 매번 새로 조회한다 - 이전 호출의 가상값이 남아있을 수 없다(누적 방지).
    baseline_regions = get_all_changwon_regions()

    valid_region_ids = {r["region_id"] for r in baseline_regions}
    if region_id not in valid_region_ids:
        return {
            "status": "error",
            "message": f"'{region_id}'은(는) 창원시 5개 구에 해당하지 않는 지역 ID입니다. "
            f"가능한 값: {sorted(valid_region_ids)}",
        }

    if indicator_code not in SIMULATABLE_INDICATOR_CODES:
        return {
            "status": "error",
            "message": f"'{indicator_code}'은(는) 현재 시뮬레이션 대상 지표가 아닙니다. "
            f"가능한 값: {list(SIMULATABLE_INDICATOR_CODES)}",
        }

    try:
        delta_int = int(delta)
    except (TypeError, ValueError):
        return {"status": "error", "message": "증감량(delta)은 정수여야 합니다."}

    category = INDICATOR_CATEGORY[indicator_code]
    target_region = next(r for r in baseline_regions if r["region_id"] == region_id)
    target_indicator = _find_indicator(target_region, category, indicator_code)

    if target_indicator is None or target_indicator["data_status"] != "확보" or target_indicator["value"] is None:
        return {
            "status": "error",
            "message": f"'{indicator_code}' 지표는 아직 확보되지 않아 시뮬레이션할 수 없습니다.",
        }

    # 5개 구 전부 확보돼야 compute_region_scores_from_weights()가 이 지표를 쓸 수
    # 있다(scoring.py의 기존 규칙 그대로) - 미리 확인해서 더 명확한 오류를 준다.
    for region in baseline_regions:
        indicator = _find_indicator(region, category, indicator_code)
        if indicator is None or indicator["data_status"] != "확보" or indicator["value"] is None:
            return {
                "status": "error",
                "message": f"'{indicator_code}' 지표가 창원시 5개 구 전부 확보되지 않아 "
                f"시뮬레이션할 수 없습니다({region['region_name']} 미확보).",
            }

    try:
        actual_value = float(target_indicator["value"])
    except (TypeError, ValueError):
        return {"status": "error", "message": f"'{indicator_code}' 지표의 값을 숫자로 읽을 수 없습니다."}

    simulated_value = actual_value + delta_int
    if simulated_value < 0:
        return {
            "status": "error",
            "message": f"시설 수가 음수가 될 수 없습니다 (실제 {actual_value:g}개 + "
            f"증감량 {delta_int:+d}개 = {simulated_value:g}개).",
        }

    weights = dict(comparison_weights) if comparison_weights else default_equal_weights(baseline_regions)

    baseline_scores = compute_region_scores_from_weights(weights, candidate_count=5, regions=baseline_regions)
    if baseline_scores["status"] != "ok":
        return {
            "status": "error",
            "message": baseline_scores.get("message", "가중치 설정으로는 비교 점수를 계산할 수 없습니다."),
        }

    # 원본은 절대 바꾸지 않는다 - 깊은 복사본에만 가상값을 적용한다.
    simulated_regions = copy.deepcopy(baseline_regions)
    simulated_region = next(r for r in simulated_regions if r["region_id"] == region_id)
    simulated_indicator = _find_indicator(simulated_region, category, indicator_code)
    simulated_indicator["value"] = str(int(simulated_value))  # 시설 수는 항상 정수

    simulated_scores = compute_region_scores_from_weights(weights, candidate_count=5, regions=simulated_regions)
    if simulated_scores["status"] != "ok":
        return {
            "status": "error",
            "message": simulated_scores.get("message", "가중치 설정으로는 비교 점수를 계산할 수 없습니다."),
        }

    facility_counts_before = {
        r["region_id"]: float(_find_indicator(r, category, indicator_code)["value"])
        for r in baseline_regions
    }
    facility_counts_after = {
        r["region_id"]: float(_find_indicator(r, category, indicator_code)["value"])
        for r in simulated_regions
    }

    baseline_by_region = {row["region_id"]: row for row in baseline_scores["region_scores"]}
    simulated_by_region = {row["region_id"]: row for row in simulated_scores["region_scores"]}
    score_change = {
        rid: {
            "region_name": baseline_by_region[rid]["region_name"],
            "score_before": baseline_by_region[rid]["total_score"],
            "score_after": simulated_by_region[rid]["total_score"],
            "score_delta": simulated_by_region[rid]["total_score"] - baseline_by_region[rid]["total_score"],
            "rank_before": baseline_by_region[rid]["rank"],
            "rank_after": simulated_by_region[rid]["rank"],
        }
        for rid in baseline_by_region
    }

    return {
        "status": "ok",
        "region_id": region_id,
        "region_name": target_region["region_name"],
        "indicator_code": indicator_code,
        "indicator_name": target_indicator["indicator_name"],
        "actual_value": actual_value,
        "simulated_value": simulated_value,
        "delta": delta_int,
        "source": target_indicator["source"],
        "reference_date": target_indicator["reference_date"],
        "comparison_weights": weights,
        "facility_counts_before": facility_counts_before,
        "facility_counts_after": facility_counts_after,
        "baseline_scores": baseline_scores,
        "simulated_scores": simulated_scores,
        "score_change": score_change,
        "caveats": SIMULATION_CAVEATS,
    }
