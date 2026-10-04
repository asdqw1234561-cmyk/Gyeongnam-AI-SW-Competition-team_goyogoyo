# 생활권(지역) 데이터 조회
"""
경상남도 비교 단위(창원시 5개 구 + 17개 시·군, 22개 지역) 데이터 조회 서비스.

[데이터 소스 구성]
- data/regions.csv
    비교 단위 목록(마스터 데이터). region_id, region_name, city, province, region_type(구/시/군), description.
    scripts/build_gyeongnam_indicators.py가 data/gyeongnam/ 집계로 만든다.
- data/region_indicators.csv
    실제 운영용 지표 데이터(long format). 아직 실제 공공데이터를 확보하지 못한 항목은
    value를 비워두고 data_status="미확보"로 표시한다. 수치를 임의로 채우지 않는다.
- data/region_indicators_sample_dev.csv
    개발/UI 테스트 전용 예시(더미) 데이터. data_status="예시(개발용)"로 명확히 구분되며,
    모든 행에 "실제 수치 아님" 안내가 note 컬럼에 포함된다. 실제 서비스 응답에는
    기본적으로 포함되지 않으며, 호출 시 include_dev_sample=True 를 명시해야 섞여서 조회된다.

[지표 스키마] (region_indicators.csv / region_indicators_sample_dev.csv 공통)
    region_id, region_name, category, indicator_code, indicator_name,
    value, unit, source, reference_date, data_status, note

    - category: "교통" | "의료" | "생활편의" | "주거비" | "인구"(점수 축 아님, 인구 1만 명당 비교의 분모)
    - data_status: "확보"(실제 공공데이터로 확인됨) | "미확보"(아직 값 없음) | "예시(개발용)"(더미)

[추후 공공데이터 API / DB 교체 방법]
    현재는 CSV를 읽지만, 추후 공공데이터포털 API나 DB로 교체할 때는
    아래 두 private 함수(_load_regions, _load_indicators)의 내부 구현만
    바꾸면 된다. get_available_regions / get_region_detail / compare_regions /
    get_regions_for_user_input 등 공개 함수의 시그니처와 반환 형식은 그대로 유지한다.

[Streamlit(app.py) 입력정보와 연결하는 방법]
    app.py의 st.session_state.initial_input(+ followup_answers)에서 아래 키를 사용해
    get_regions_for_user_input() 을 호출하면 된다.

        user_input = {
            "희망지역": "창원시 의창구",   # str, 필수. "창원" 포함 여부로 지원 지역 판단
            "중요 생활조건": ["교통", "의료"],  # list[str], 선택. app.py 보기 값과 매칭되는
                                              # 항목만 "교통"/"의료"/"생활편의"/"주거비"로 필터링
            "원하는 후보 개수": 3,          # int, 선택. 반환할 생활권 수 상한(기본 5, 최대 5)
        }
        regions = get_regions_for_user_input(user_input)

    반환값은 generate_followup_questions 와 달리 추천 점수를 계산하지 않으며,
    생활권별 원본 지표(확보/미확보 상태 포함)만 정리해서 돌려준다.
    점수화·순위화는 이번 단계의 범위가 아니다(analysis/scoring.py 몫).
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(os.path.dirname(_THIS_DIR), "data")

REGIONS_CSV = os.path.join(_DATA_DIR, "regions.csv")
INDICATORS_CSV = os.path.join(_DATA_DIR, "region_indicators.csv")
INDICATORS_SAMPLE_DEV_CSV = os.path.join(_DATA_DIR, "region_indicators_sample_dev.csv")

TARGET_PROVINCE = "경상남도"
VALID_CATEGORIES = ("교통", "의료", "생활편의", "주거비", "인구")
# 지원 범위 판정용 이름(희망지역 문자열에 이 중 하나가 들어 있으면 지원). 창원시 구 이름은 "창원"으로 판정한다.
SUPPORTED_REGION_KEYWORDS = ("경상남도", "경남", "창원")

# 비교 범위: 규모가 비슷한 같은 유형끼리만 비교한다(2026-10-04 사용자 결정 - 서로 다른 유형을 한 표에서
# 점수로 비교하면 시설 수 그대로는 인구 순위, 인구당은 군 지역 쏠림으로 왜곡이 크다).
REGION_SCOPES: dict[str, str] = {
    "창원시 5개 구": "구",
    "경남 시 지역 (7곳)": "시",
    "경남 군 지역 (10곳)": "군",
}
DEFAULT_REGION_TYPE = "구"

# 화면 표시용 이름. 내부 저장값(REGION_SCOPES 키, 세션의 희망지역, 수요 기록 scope)은 그대로 두고
# 사용자에게 보여줄 때만 바꾼다. 경남의 구 지역 5곳은 모두 창원시에 속한다.
REGION_SCOPE_DISPLAY: dict[str, str] = {
    "창원시 5개 구": "경남 구 지역 (5곳)",
    "경남 시 지역 (7곳)": "경남 시 지역 (7곳)",
    "경남 군 지역 (10곳)": "경남 군 지역 (10곳)",
}


def scope_display(label: str | None) -> str:
    """비교 범위 내부 값 → 화면 표시 이름. 모르는 값은 그대로 돌려준다(빈 값은 기본 범위)."""
    if not label:
        return REGION_SCOPE_DISPLAY["창원시 5개 구"]
    return REGION_SCOPE_DISPLAY.get(label, label)


def region_type_for(region_text: str | None) -> str:
    """희망지역(비교 범위) 문자열 → 비교할 지역 유형(구/시/군). 예전 입력("창원시 의창구" 등)은 구로 본다.
    내부 값("창원시 5개 구")과 화면 표시 이름("경남 구 지역 (5곳)") 모두 받는다."""
    text = region_text or ""
    for label, region_type in REGION_SCOPES.items():
        if label in text or REGION_SCOPE_DISPLAY[label] in text:
            return region_type
    regions = _load_regions()
    for _, row in regions.iterrows():
        if row["region_type"] != "구" and row["city"] and row["city"] in text:
            return row["region_type"]
    return DEFAULT_REGION_TYPE

# app.py의 "중요 생활조건" 보기 값을 지표 category 값으로 매핑한다.
CONDITION_TO_CATEGORY = {
    "교통": "교통",
    "의료": "의료",
    "생활편의(마트/편의점)": "생활편의",
    "주거비": "주거비",
}


def _load_regions(province: str = TARGET_PROVINCE) -> pd.DataFrame:
    df = pd.read_csv(REGIONS_CSV, dtype=str).fillna("")
    if province:
        df = df[df["province"] == province]
    return df.reset_index(drop=True)


def _load_indicators(include_dev_sample: bool = False) -> pd.DataFrame:
    df = pd.read_csv(INDICATORS_CSV, dtype=str)
    if include_dev_sample and os.path.exists(INDICATORS_SAMPLE_DEV_CSV):
        dev_df = pd.read_csv(INDICATORS_SAMPLE_DEV_CSV, dtype=str)
        df = pd.concat([df, dev_df], ignore_index=True)
    return df


def _clean(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _row_to_indicator(row: pd.Series) -> dict:
    return {
        "indicator_code": _clean(row.get("indicator_code")),
        "indicator_name": _clean(row.get("indicator_name")),
        "category": _clean(row.get("category")),
        "value": _clean(row.get("value")),
        "unit": _clean(row.get("unit")),
        "source": _clean(row.get("source")),
        "reference_date": _clean(row.get("reference_date")),
        "data_status": _clean(row.get("data_status")) or "미확보",
        "note": _clean(row.get("note")),
    }


def get_available_regions(province: str = TARGET_PROVINCE) -> list[dict]:
    """지원 대상(경상남도) 비교 단위 22개 목록을 반환한다."""
    regions_df = _load_regions(province)
    return regions_df.to_dict(orient="records")


def is_supported_region(region_text: str) -> bool:
    """사용자가 입력한 '희망지역' 문자열이 현재 지원 범위(경상남도 22개 지역)인지 판단한다."""
    if not region_text:
        return False
    if any(keyword in region_text for keyword in SUPPORTED_REGION_KEYWORDS):
        return True
    return any(name and name in region_text for name in _load_regions()["city"])


def get_region_detail(
    region_id: str,
    categories: Optional[list[str]] = None,
    include_dev_sample: bool = False,
) -> Optional[dict]:
    """
    특정 생활권 1곳의 지표를 category별로 묶어서 반환한다.

    반환 형식:
        {
            "region_id": "CW-SEONGSAN",
            "region_name": "성산구",
            "city": "창원시",
            "categories": {
                "교통": [ {indicator_code, indicator_name, value, unit,
                           source, reference_date, data_status, note}, ... ],
                "의료": [...],
                "생활편의": [...],
                "주거비": [...],
            },
        }
    해당 region_id가 없으면 None을 반환한다.
    """
    regions_df = _load_regions(province="")
    region_row = regions_df[regions_df["region_id"] == region_id]
    if region_row.empty:
        return None
    region_row = region_row.iloc[0]

    indicators_df = _load_indicators(include_dev_sample=include_dev_sample)
    indicators_df = indicators_df[indicators_df["region_id"] == region_id]
    if categories:
        indicators_df = indicators_df[indicators_df["category"].isin(categories)]

    grouped: dict[str, list[dict]] = {cat: [] for cat in (categories or VALID_CATEGORIES)}
    for _, row in indicators_df.iterrows():
        indicator = _row_to_indicator(row)
        grouped.setdefault(indicator["category"], []).append(indicator)

    return {
        "region_id": region_row["region_id"],
        "region_name": region_row["region_name"],
        "city": region_row["city"],
        "region_type": region_row.get("region_type", ""),
        "categories": grouped,
    }


def compare_regions(
    region_ids: list[str],
    categories: Optional[list[str]] = None,
    include_dev_sample: bool = False,
) -> list[dict]:
    """
    여러 생활권의 상세 데이터를 같은 형식으로 묶어 비교 테이블 구성에 사용할 수 있게 한다.
    점수 계산이나 순위 매김은 하지 않는다.
    """
    results = []
    for region_id in region_ids:
        detail = get_region_detail(
            region_id, categories=categories, include_dev_sample=include_dev_sample
        )
        if detail is not None:
            results.append(detail)
    return results


def filter_confirmed_only(indicators: list[dict]) -> list[dict]:
    """
    data_status가 "확보"인 지표만 남긴다.
    추천/점수 계산 로직(analysis/scoring.py 등)에서 미확보·예시 데이터를
    실수로 사용하지 않도록 거를 때 사용한다.
    """
    return [ind for ind in indicators if ind.get("data_status") == "확보"]


def get_regions_for_user_input(
    user_input: dict,
    include_dev_sample: bool = False,
) -> list[dict]:
    """
    app.py의 session_state 입력정보를 받아 비교 대상 생활권 데이터 목록을 반환한다.

    Args:
        user_input: 아래 키를 사용(모두 선택적이나 "희망지역"이 없으면 빈 리스트 반환)
            - "희망지역" (str): "창원" 포함 여부로 지원 지역인지 판단
            - "중요 생활조건" (list[str]): app.py 보기값. 지정 시 해당 category만 반환
            - "원하는 후보 개수" (int): 반환할 생활권 수 상한 (기본 5, 최대 5)
        include_dev_sample: True면 개발용 예시 데이터를 함께 섞어서 반환(테스트용).
            운영 화면에서는 False(기본값)를 사용해 미확보 항목이 그대로 노출되게 한다.

    Returns:
        compare_regions()와 동일한 형식의 list[dict]. 지원하지 않는 지역이거나
        입력이 비어 있으면 빈 리스트를 반환한다.
    """
    region_text = user_input.get("희망지역", "")
    if not is_supported_region(region_text):
        return []

    conditions = user_input.get("중요 생활조건") or []
    categories = [CONDITION_TO_CATEGORY[c] for c in conditions if c in CONDITION_TO_CATEGORY]
    categories = categories or None  # 지정 안 하면 전체 category 반환

    candidate_count = user_input.get("원하는 후보 개수") or 5
    try:
        candidate_count = int(candidate_count)
    except (TypeError, ValueError):
        candidate_count = 5
    candidate_count = max(1, min(candidate_count, 5))

    all_regions = get_available_regions()
    region_ids = [r["region_id"] for r in all_regions][:candidate_count]

    return compare_regions(
        region_ids, categories=categories, include_dev_sample=include_dev_sample
    )


def get_all_regions(
    categories: Optional[list[str]] = None,
    include_dev_sample: bool = False,
    region_type: Optional[str] = None,
) -> list[dict]:
    """
    경상남도 비교 단위 22개 전체(region_type을 주면 그 유형 - 구 5 / 시 7 / 군 10 - 전부)의 비교 데이터를 반환한다.

    get_regions_for_user_input()은 사용자가 입력한 "원하는 후보 개수"만큼 앞에서부터
    잘라서 반환하므로, 적합도 계산(analysis/scoring.py 등) 직전처럼 "후보 개수와 무관하게
    전체 구를 다 봐야 하는" 단계에서는 이 함수를 사용한다. 점수 계산이나 순위화는 하지 않고
    compare_regions()와 동일한 형식의 원본 지표 데이터만 반환한다.
    """
    all_regions = get_available_regions()
    region_ids = [r["region_id"] for r in all_regions if not region_type or r.get("region_type") == region_type]
    return compare_regions(
        region_ids, categories=categories, include_dev_sample=include_dev_sample
    )
