# 생활권 적합도
"""
경상남도 22개 비교 단위(창원시 5개 구 + 17개 시·군) 사이의 "상대 비교 점수"를 계산한다.

중요: 이 점수는 "실제 거주 적합도"가 아니라 "현재 확보된 시설 수를 인구 1만 명당으로 바꿔
22개 지역끼리 min-max 정규화해서 비교한 상대 점수"일 뿐이다. 면적·실제 거리·이동시간은 보정하지
않았고, 100점이 "완벽한 정주환경"을 뜻하지도 않는다(그 지표에서 22개 지역 중 1위라는 뜻일 뿐).
자세한 주의사항은 CAVEATS를 참고.

[인구 1만 명당 비교] (2026-10-04 사용자 승인 - 규모가 수십 배 다른 시·군을 시설 수 그대로 비교하면
사실상 인구 순위가 되기 때문)
    비교값 = 시설 수 ÷ 주민등록 인구 × 10,000. 인구(category '인구', indicator_code 'population')가
    비교 지역 전부 '확보'일 때만 적용하고, 하나라도 없으면 변환하지 않고 시설 수 그대로 비교하며
    결과의 basis로 어느 쪽인지 밝힌다. component_scores의 raw_value는 언제나 실제 시설 수다.

[점수 계산에 쓰는 지표] (요구사항: 현재 이 3개뿐)
    교통         -> bus_stop_count (버스정류장 수)
    의료         -> hospital_count (의료기관 수 - 의원·치과·한의원 등 전 종별 합산)
    생활편의     -> convenience_store_count (편의점 등록 업소 수)

[쓰지 않는 것]
    - 사용자가 입력한 직장/학교 위치, 자가용 보유 여부, 주거비 예산, AI 추가질문
      답변 - 이것들과 대응하는 실제 지표(이동시간, 주거비 등)가 아직 없으므로
      점수 계산에 절대 쓰지 않는다. (app.py에서 이 함수에 넘기는 인자는
      user_conditions와 candidate_count뿐이라 구조적으로 섞여 들어올 수 없다.)
    - mart_count, emergency_hospital_count, 주거비, 대중교통 소요시간,
      교육/안전/자연환경/문화시설 - 전부 미확보이므로 선택되어도 제외한다.

[정규화 방식]
    비교값(인구 1만 명당 값)에 대해 min-max 정규화: (값 - 최소값) / (최대값 - 최소값) * 100
    비교 지역 값이 전부 같으면 0으로 나누지 않고 전부 50.0점(우열을 가릴 근거가 없다는
    뜻)을 준다. 어떤 수식을 썼는지는 compute_region_scores()의 반환값
    (`normalization[indicator_code]["formula"]`)으로 그대로 확인할 수 있다.

[가중치]
    사용자가 선택한 "중요 생활조건" 중, 위 3개 지표에 대응하면서 비교 지역 전부
    data_status=='확보'인 것만 "사용된 조건"으로 채택하고, 그 수만큼 동일 가중치
    (1/n)를 준다. 조건을 하나도 선택하지 않았으면 확보된 지표 전체(현재 3개)에
    동일 가중치를 준다. 선택한 조건이 전부 미확보/대응 지표 없음이면 점수를 억지로
    만들지 않고 status="no_usable_conditions"를 반환한다.
"""

from __future__ import annotations

from services.region_data import DEFAULT_REGION_TYPE, get_all_regions

# 사용자 "중요 생활조건" 선택지(app.py의 multiselect 보기)와 실제 점수 계산용
# 지표 코드의 대응관계. 교육/안전/자연환경/문화시설은 대응하는 지표 자체가 아직
# 없어서 의도적으로 넣지 않았다 - 선택되면 excluded_conditions로 보고된다.
CONDITION_TO_INDICATOR_CODE: dict[str, str] = {
    "교통": "bus_stop_count",
    "의료": "hospital_count",
    "생활편의(마트/편의점)": "convenience_store_count",
}
INDICATOR_CATEGORY: dict[str, str] = {
    "bus_stop_count": "교통",
    "hospital_count": "의료",
    "convenience_store_count": "생활편의",
}

# 조건을 하나도 선택하지 않았을 때 기본으로 쓰는 전체 후보(현재 확보 여부는
# 매번 실제 데이터로 다시 확인하며, 이 목록은 "후보"일 뿐 확정이 아니다).
ALL_SCORABLE_CONDITIONS: tuple[str, ...] = tuple(CONDITION_TO_INDICATOR_CODE)

# 가중치를 직접 조정하는 피드백 화면에서 "조정 가능한 지표"로 노출하는 전부.
# 이 3개 외의 지표는(교육/안전/주거비 등) 가중치 슬라이더 자체를 만들지 않으므로
# 구조적으로 점수 계산에 섞여 들어올 수 없다.
VALID_SCORABLE_INDICATOR_CODES: tuple[str, ...] = tuple(CONDITION_TO_INDICATOR_CODE.values())

NO_DATA_MESSAGE = "현재 비교 가능한 데이터가 없습니다."
ALL_WEIGHTS_ZERO_MESSAGE = "모든 가중치가 0입니다. 하나 이상의 조건에 가중치를 주세요."

PER_CAPITA_UNIT = 10_000
POPULATION_CATEGORY = "인구"
POPULATION_CODE = "population"
BASIS_PER_CAPITA = "per_10k_population"
BASIS_COUNT = "count"

CAVEATS: list[str] = [
    "시설 수를 인구 1만 명당으로 바꿔 비교했지만, 면적·실제 거리·이동시간은 반영하지 않았습니다.",
    "점수는 '시설 수 기반 상대 비교 점수'일 뿐, 실제 거주 적합도를 확정하는 값이 아닙니다.",
    "100점은 해당 지표에서 경남 22개 지역 중 인구 1만 명당 값이 가장 높다는 뜻일 뿐, "
    "완벽한 정주환경을 의미하지 않습니다.",
]


def _normalize_min_max(raw_values: dict[str, float]) -> tuple[dict[str, float], str]:
    """
    min-max 정규화. 반환: (region_id -> 0~100 점수, 적용한 계산식 설명 문자열)
    """
    values = list(raw_values.values())
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        return (
            {rid: 50.0 for rid in raw_values},
            f"비교 지역 값이 모두 {vmin:g}로 동일 -> 우열을 가릴 수 없어 전 지역 50.0점 부여",
        )
    formula = f"(값 - {vmin:g}) / ({vmax:g} - {vmin:g}) * 100"
    scores = {rid: (v - vmin) / (vmax - vmin) * 100 for rid, v in raw_values.items()}
    return scores, formula


def _collect_confirmed_indicator(
    regions: list[dict], indicator_code: str
) -> tuple[dict[str, float], str] | None:
    """
    비교 지역 전부 해당 indicator_code가 data_status=='확보'이고 숫자로 변환 가능해야
    (raw_values, indicator_name)을 반환한다. 하나라도 아니면 None(사용 불가).
    """
    category = INDICATOR_CATEGORY[indicator_code]
    raw_values: dict[str, float] = {}
    indicator_name = indicator_code
    for region in regions:
        indicator = next(
            (i for i in region["categories"].get(category, []) if i["indicator_code"] == indicator_code),
            None,
        )
        if indicator is None or indicator["data_status"] != "확보" or indicator["value"] is None:
            return None
        try:
            raw_values[region["region_id"]] = float(indicator["value"])
        except (TypeError, ValueError):
            return None
        indicator_name = indicator["indicator_name"]
    return raw_values, indicator_name


def collect_confirmed_indicator(
    regions: list[dict], indicator_code: str
) -> tuple[dict[str, float], str] | None:
    """
    다른 모듈(agent/planner.py, analysis/simulation.py)이 쓰는 공개 이름. 판정 로직은
    _collect_confirmed_indicator()와 완전히 같다 - 내부 함수를 그대로 호출할 뿐이라
    "확보" 판정 기준이 모듈마다 갈라질 수 없다.
    """
    return _collect_confirmed_indicator(regions, indicator_code)


def population_by_region(regions: list[dict]) -> dict[str, float] | None:
    """비교 지역 전부 인구가 '확보'이고 양수일 때만 {region_id: 인구}. 하나라도 아니면 None(변환 안 함)."""
    populations: dict[str, float] = {}
    for region in regions:
        indicator = next((i for i in region["categories"].get(POPULATION_CATEGORY, [])
                          if i["indicator_code"] == POPULATION_CODE), None)
        if indicator is None or indicator["data_status"] != "확보" or indicator["value"] is None:
            return None
        try:
            value = float(indicator["value"])
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        populations[region["region_id"]] = value
    return populations


def _build_ok_result(
    regions: list[dict],
    used_meta: list[dict],
    excluded_conditions: list[dict],
    candidate_count: int,
) -> dict:
    """
    used_meta(각 항목에 최소 indicator_code/indicator_name/weight가 있어야 함, "condition"은
    선택)로 min-max 정규화 + 가중합 + 비교 지역 정렬/동점 판정까지 공통 처리한다.
    compute_region_scores()와 compute_region_scores_from_weights() 둘 다 이 함수로
    귀결되므로, 정규화 방식과 집계 로직은 호출 경로와 무관하게 완전히 동일하다.
    """
    region_name_by_id = {r["region_id"]: r["region_name"] for r in regions}
    populations = population_by_region(regions)
    basis = BASIS_PER_CAPITA if populations else BASIS_COUNT

    indicator_raw_values: dict[str, dict[str, float]] = {}
    compare_values_by_indicator: dict[str, dict[str, float]] = {}
    normalization: dict[str, dict] = {}
    normalized_by_indicator: dict[str, dict[str, float]] = {}
    for meta in used_meta:
        code = meta["indicator_code"]
        raw_values, _name = _collect_confirmed_indicator(regions, code)
        indicator_raw_values[code] = raw_values
        compare_values = (
            {rid: v / populations[rid] * PER_CAPITA_UNIT for rid, v in raw_values.items()} if populations else raw_values
        )
        compare_values_by_indicator[code] = compare_values
        normalized, formula = _normalize_min_max(compare_values)
        if populations:
            formula = f"인구 1만 명당 값(= 개수 ÷ 인구 × 10,000)으로 {formula}"
        normalized_by_indicator[code] = normalized
        normalization[code] = {"formula": formula, "raw_values": raw_values, "compare_values": compare_values,
                               "normalized": normalized, "basis": basis}

    # 점수 계산에 실제로 쓰였는지와 무관하게, 화면에서 후보지역의 "실제 수치"를
    # 보여줄 때 참고하도록 확보된 3개 지표를 전부 모아둔다.
    reference_data: dict[str, dict] = {}
    for code in CONDITION_TO_INDICATOR_CODE.values():
        collected = _collect_confirmed_indicator(regions, code)
        if collected is not None:
            raw_values, indicator_name = collected
            reference_data[code] = {"indicator_name": indicator_name, "raw_values": raw_values}

    region_rows = []
    for region in regions:
        rid = region["region_id"]
        component_scores = {}
        total = 0.0
        for meta in used_meta:
            code = meta["indicator_code"]
            raw_value = indicator_raw_values[code][rid]
            norm_score = normalized_by_indicator[code][rid]
            weighted = norm_score * meta["weight"]
            total += weighted
            component_scores[code] = {
                "raw_value": raw_value,
                "per_10k": compare_values_by_indicator[code][rid] if populations else None,
                "normalized_score": norm_score,
                "weight": meta["weight"],
                "weighted_score": weighted,
            }
        reference_indicators = {
            code: {"indicator_name": info["indicator_name"], "raw_value": info["raw_values"][rid]}
            for code, info in reference_data.items()
        }
        region_rows.append(
            {
                "region_id": rid,
                "region_name": region_name_by_id[rid],
                "total_score": total,
                "component_scores": component_scores,
                "reference_indicators": reference_indicators,
                "population": populations.get(rid) if populations else None,
            }
        )

    # 내림차순 정렬. 동점이면 region_id로 안정적으로 순서를 고정(일관성 보장).
    region_rows.sort(key=lambda r: (-round(r["total_score"], 6), r["region_id"]))

    rounded_totals = [round(r["total_score"], 6) for r in region_rows]
    for idx, row in enumerate(region_rows):
        row["rank"] = idx + 1
        row["tied"] = rounded_totals.count(rounded_totals[idx]) > 1

    candidate_count = max(1, int(candidate_count or 1))

    return {
        "status": "ok",
        "basis": basis,
        "used_conditions": used_meta,
        "excluded_conditions": excluded_conditions,
        "caveats": CAVEATS,
        "normalization": normalization,
        "region_scores": region_rows,
        "top_candidates": region_rows[:candidate_count],
    }


def compute_region_scores(
    user_conditions: list[str] | None = None,
    candidate_count: int = 5,
    regions: list[dict] | None = None,
) -> dict:
    """
    경남 22개 지역을 사용자가 선택한 "중요 생활조건" 중 실제 데이터가 확보된 것만으로
    상대 비교한다(최초 추천용 - 조건 기반, 동일 가중치).

    Args:
        user_conditions: app.py의 "중요 생활조건" multiselect 선택값. None/빈 리스트면
            조건 미선택으로 간주해 확보된 지표 전체를 동일 가중치로 쓴다.
        candidate_count: 최종으로 잘라서 보여줄 후보 구 개수(1~5 권장이나 값 자체를
            강제하지는 않음 - 화면단 number_input에서 1~5로 제한한다).
        regions: 생략하면(기본값 None) 기존처럼 get_all_regions()로 실제
            공공데이터를 조회한다. 값을 넘기면 그 목록을 그대로 쓴다 - 경남 22개 지역
            전체가 포함된 get_all_regions()와 같은 형식의 데이터여야 하며,
            analysis/simulation.py가 "원본의 깊은 복사본에 가상값만 바꾼" 목록을
            넘길 때 쓰는 용도다. 이주자용 추천(app.py)은 이 인자를 쓰지 않으므로
            동작이 전혀 바뀌지 않는다.

    Returns:
        공통 키:
            status: "ok" | "no_usable_conditions"
            used_conditions: [{"condition","indicator_code","indicator_name","category","weight"}]
            excluded_conditions: [{"condition","reason"}]
            caveats: [문구, ...]
        status=="no_usable_conditions"일 때 추가:
            message: str
        status=="ok"일 때 추가:
            normalization: {indicator_code: {"formula","raw_values","normalized"}}
            region_scores: 비교 지역 전체, total_score 내림차순 정렬
                [{"region_id","region_name","rank","total_score","tied",
                  "component_scores": {indicator_code: {"raw_value","normalized_score",
                                                           "weight","weighted_score"}},
                  "reference_indicators": {indicator_code: {"indicator_name","raw_value"}}
                      # 점수 계산에 썼는지와 무관하게 확보된 3개 지표 전부(화면 표시용)
                  }, ...]
            top_candidates: region_scores[:candidate_count]
    """
    # 비교 대상을 넘기지 않으면 기본 범위(창원시 5개 구)만 비교한다 - 유형이 다른 지역을 섞지 않는다.
    regions = regions if regions is not None else get_all_regions(region_type=DEFAULT_REGION_TYPE)

    conditions = list(user_conditions) if user_conditions else []
    target_conditions = conditions if conditions else list(ALL_SCORABLE_CONDITIONS)

    used_conditions: list[dict] = []
    excluded_conditions: list[dict] = []

    for condition in target_conditions:
        indicator_code = CONDITION_TO_INDICATOR_CODE.get(condition)
        if indicator_code is None:
            excluded_conditions.append(
                {"condition": condition, "reason": "이 조건에 대응하는 지표가 아직 없습니다(미확보)"}
            )
            continue

        collected = _collect_confirmed_indicator(regions, indicator_code)
        if collected is None:
            excluded_conditions.append(
                {"condition": condition, "reason": f"'{indicator_code}' 지표가 비교 지역 전부 확보되지 않아 제외"}
            )
            continue

        _raw_values, indicator_name = collected
        used_conditions.append(
            {
                "condition": condition,
                "indicator_code": indicator_code,
                "indicator_name": indicator_name,
                "category": INDICATOR_CATEGORY[indicator_code],
            }
        )

    if not used_conditions:
        return {
            "status": "no_usable_conditions",
            "message": NO_DATA_MESSAGE,
            "used_conditions": [],
            "excluded_conditions": excluded_conditions,
            "caveats": CAVEATS,
        }

    weight = 1.0 / len(used_conditions)
    for uc in used_conditions:
        uc["weight"] = weight

    return _build_ok_result(regions, used_conditions, excluded_conditions, candidate_count)


def initial_feedback_weights(scoring_result: dict) -> dict[str, float]:
    """
    피드백(가중치 조정) 슬라이더의 초기값을 만든다. 최초 추천(compute_region_scores)
    결과에서 실제로 쓰인 지표는 그 가중치를, 쓰이지 않은 나머지 확보 지표는 0을
    percent(0~100) 단위로 돌려준다. 요구사항: "초기 가중치는 최초 추천 계산에
    사용된 가중치와 동일하게 설정".
    """
    used_weight_by_code = {
        uc["indicator_code"]: uc["weight"] for uc in scoring_result.get("used_conditions", [])
    }
    return {
        code: round(used_weight_by_code.get(code, 0.0) * 100, 1)
        for code in VALID_SCORABLE_INDICATOR_CODES
    }


def compute_region_scores_from_weights(
    indicator_weights: dict[str, float],
    candidate_count: int = 5,
    regions: list[dict] | None = None,
) -> dict:
    """
    사용자가 "조건 바꿔서 다시 보기"에서 직접 지정한 가중치로 22개 지역을
    재평가한다(피드백 전용 경로). min-max 정규화와 지표값 자체는
    compute_region_scores()와 완전히 동일한 내부 함수(_normalize_min_max,
    _collect_confirmed_indicator)를 그대로 쓴다 - 바뀌는 건 가중치뿐이다.

    Args:
        indicator_weights: {indicator_code: 0~100 사이의 상대 가중치, ...}.
            키는 VALID_SCORABLE_INDICATOR_CODES(교통/의료/생활편의 3개)만 유효하고,
            그 외 키나 값이 0 이하인 항목은 무시한다. 합계가 100이 아니어도 되며
            내부에서 합계 100%로 정규화한다.
        candidate_count: 최종 후보 개수.
        regions: compute_region_scores()와 동일 - 생략하면 실제 공공데이터를 조회하고,
            넘기면 그 목록을 그대로 쓴다(analysis/simulation.py가 가상 데이터를 넣을
            때 사용). 이주자용 피드백 화면(app.py)은 이 인자를 쓰지 않는다.

    Returns:
        compute_region_scores()와 동일한 형식. 유효한 가중치가 하나도 없으면
        (전부 0 이하이거나, 가리키는 지표가 아직 확보되지 않았으면)
        status="no_usable_conditions"를 반환하고 계산하지 않는다.
    """
    # 비교 대상을 넘기지 않으면 기본 범위(창원시 5개 구)만 비교한다 - 유형이 다른 지역을 섞지 않는다.
    regions = regions if regions is not None else get_all_regions(region_type=DEFAULT_REGION_TYPE)

    positive_weights = {
        code: w
        for code, w in (indicator_weights or {}).items()
        if code in VALID_SCORABLE_INDICATOR_CODES and w and w > 0
    }
    if not positive_weights:
        return {
            "status": "no_usable_conditions",
            "message": ALL_WEIGHTS_ZERO_MESSAGE,
            "used_conditions": [],
            "excluded_conditions": [],
            "caveats": CAVEATS,
        }

    # 먼저 "실제로 확보된" 지표만 걸러낸 다음 그 부분집합을 기준으로 정규화한다.
    # (weight_sum을 필터링 전에 구하면, 나중에 한 지표가 미확보로 바뀌어 제외될 때
    # 남은 지표들의 가중치 합이 100%에 못 미치게 된다 - 순서가 중요하다.)
    confirmed_weights: dict[str, tuple[float, str]] = {}
    for code, w in positive_weights.items():
        collected = _collect_confirmed_indicator(regions, code)
        if collected is None:
            continue  # 방어적 처리 - 현재는 3개 다 확보 상태라 실제로는 거의 발생하지 않음
        _raw_values, indicator_name = collected
        confirmed_weights[code] = (w, indicator_name)

    if not confirmed_weights:
        return {
            "status": "no_usable_conditions",
            "message": NO_DATA_MESSAGE,
            "used_conditions": [],
            "excluded_conditions": [],
            "caveats": CAVEATS,
        }

    weight_sum = sum(w for w, _name in confirmed_weights.values())
    used_meta: list[dict] = [
        {
            "indicator_code": code,
            "indicator_name": name,
            "category": INDICATOR_CATEGORY[code],
            "weight": w / weight_sum,
        }
        for code, (w, name) in confirmed_weights.items()
    ]

    return _build_ok_result(regions, used_meta, excluded_conditions=[], candidate_count=candidate_count)
