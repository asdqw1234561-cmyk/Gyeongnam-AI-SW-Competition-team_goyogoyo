# 정부용 화면
"""
창원시 생활권 시설 현황 및 지역 간 차이 분석 - 정부·행정 담당자용 1차 화면.

목적은 "시설 수 기반 지역 간 차이 분석"을 보여주는 것이지, 어떤 지역을 "정주
저해요인 확정"이나 "인프라 부족 지역 확정"으로 판정하는 것이 아니다. 데이터 조회는
전부 services/region_data.py의 get_all_changwon_regions()를 통해서만 하며(원본 CSV를
직접 읽거나 수치를 하드코딩하지 않는다), 이 페이지는 조회만 할 뿐 어떤 경로로도
CSV를 수정하지 않는다 - 조회 대상 구를 바꿔도 원본 데이터는 그대로다.

이주자용 화면(app.py)의 analysis/scoring.py 기반 "상대 비교 점수"와는 분리된 화면이다.
여기서는 점수를 계산하지 않고 실제 확보된 지표값 자체와 그 차이만 보여준다.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services.region_data import get_all_changwon_regions

CHART_ACCENT_COLOR = "#2a78d6"
CATEGORIES = ["교통", "의료", "생활편의"]
CATEGORY_LABELS = {"교통": "🚌 교통", "의료": "🏥 의료", "생활편의": "🏪 생활편의"}

# 확보된 지표라도 "그래서 부족/열악하다"로 해석하지 않도록, 지표별 해석상 한계를
# 화면에 같이 보여준다. 원본 CSV의 note 컬럼(검증 정보 전체)과는 별개로, 여기서는
# 정부용 화면에 필요한 핵심 한계만 간결하게 요약한다.
INDICATOR_LIMITATIONS = {
    "bus_stop_count": "버스정류장 수는 실제 대중교통 접근성이나 이동시간을 의미하지 않습니다.",
    "hospital_count": "병원 수는 의원·치과의원·한의원 등을 포함한 등록 의료기관 수이며, "
    "의료서비스의 질이나 실제 접근성을 뜻하지 않습니다.",
    "convenience_store_count": "편의점 수는 상가정보 등록 업소 기준이며, "
    "실제 영업 매장 수와 다를 수 있습니다.",
}

PAGE_CAVEATS = [
    "시설이 적다는 이유만으로 해당 지역의 인프라가 부족하다고 단정하지 않습니다.",
    "버스정류장 수를 실제 대중교통 접근성이나 이동시간으로 해석하지 않습니다.",
    "의료기관 수를 의료서비스의 질이나 실제 접근성으로 해석하지 않습니다.",
    "편의점 등록 업소 수를 실제 영업 매장 수와 동일하게 취급하지 않습니다.",
    "인구·면적·수요를 보정하지 않은 단순 시설 수 비교입니다.",
]


def _indicator_codes_for_category(regions: list[dict], category: str) -> list[str]:
    codes: list[str] = []
    for region in regions:
        for indicator in region["categories"].get(category, []):
            if indicator["indicator_code"] not in codes:
                codes.append(indicator["indicator_code"])
    return codes


def _get_indicator(region: dict, category: str, code: str) -> dict | None:
    return next(
        (i for i in region["categories"].get(category, []) if i["indicator_code"] == code),
        None,
    )


def _is_fully_confirmed(regions: list[dict], category: str, code: str) -> bool:
    for region in regions:
        indicator = _get_indicator(region, category, code)
        if indicator is None or indicator["data_status"] != "확보":
            return False
    return True


st.set_page_config(page_title="창원시 시설 현황 분석 (정부용)", page_icon="🏛️")

st.title("🏛️ 창원시 생활권 시설 현황 및 지역 간 차이 분석")
st.caption(
    "창원시 5개 구의 공공데이터 확보 지표를 비교합니다. 특정 지역을 "
    "'정주 저해요인 확정' 또는 '인프라 부족 지역 확정'으로 판정하는 화면이 아니며, "
    "인구·면적·수요를 보정하지 않은 단순 시설 수 비교 결과입니다."
)
with st.expander("⚠️ 이 화면을 해석할 때 반드시 유의할 점", expanded=True):
    for c in PAGE_CAVEATS:
        st.write(f"- {c}")

# 항상 창원시 5개 구 전체를 조회한다(점수 계산 없음, 원본 CSV를 그대로 읽기만 함).
regions = get_all_changwon_regions()

# ---------------------------------------------------------------------------
# 1. 창원시 5개 구 전체 시설 수 현황
# ---------------------------------------------------------------------------
st.header("1. 창원시 5개 구 전체 시설 수 현황")

summary_rows = []
for region in regions:
    row = {"구": region["region_name"]}
    for category in CATEGORIES:
        for code in _indicator_codes_for_category(regions, category):
            if _is_fully_confirmed(regions, category, code):
                indicator = _get_indicator(region, category, code)
                row[indicator["indicator_name"]] = indicator["value"]
    summary_rows.append(row)

summary_df = pd.DataFrame(summary_rows)
confirmed_columns = [c for c in summary_df.columns if c != "구"]
if confirmed_columns:
    st.dataframe(summary_df, hide_index=True, width="stretch")
    st.caption(f"현재 5개 구 전체가 확보된 지표: {', '.join(confirmed_columns)}")
else:
    st.info("아직 5개 구 전체가 확보된 지표가 없습니다.")

# ---------------------------------------------------------------------------
# 2. 항목별(교통/의료/생활편의) 비교표와 그래프
# ---------------------------------------------------------------------------
st.header("2. 항목별 비교표와 그래프")

for category in CATEGORIES:
    st.subheader(CATEGORY_LABELS[category])
    codes = _indicator_codes_for_category(regions, category)
    if not codes:
        st.info("아직 등록된 지표가 없습니다.")
        continue

    for code in codes:
        rows = []
        for region in regions:
            indicator = _get_indicator(region, category, code)
            if indicator is None:
                continue
            rows.append(
                {
                    "구": region["region_name"],
                    "값": indicator["value"] if indicator["value"] is not None else "미확보",
                    "단위": indicator["unit"] or "-",
                    "상태": indicator["data_status"],
                    "출처": indicator["source"] or "-",
                    "기준일": indicator["reference_date"] or "-",
                }
            )

        indicator_name = next(
            (r["indicator_name"] for r in [_get_indicator(regions[0], category, code)] if r),
            code,
        )
        rows_df = pd.DataFrame(rows)
        confirmed_rows = [row for row in rows if row["상태"] == "확보"]

        if not confirmed_rows:
            # 5개 구 전체 미확보 - 긴 표 대신 한 줄 + 접기 영역. 0으로 표시하지 않는다.
            st.markdown(f"**{indicator_name}** — 미확보")
            with st.expander("구별 상태 보기"):
                st.dataframe(rows_df[["구", "상태"]], hide_index=True, width="stretch")
            continue

        st.markdown(f"**{indicator_name}**")
        st.dataframe(rows_df[["구", "값", "단위", "상태"]], hide_index=True, width="stretch")

        limitation = INDICATOR_LIMITATIONS.get(code)
        if limitation:
            st.caption(f"ℹ️ {limitation}")

        with st.expander("출처 및 기준일 보기"):
            st.dataframe(rows_df[["구", "출처", "기준일"]], hide_index=True, width="stretch")

        chart_df = pd.DataFrame(
            [{"구": row["구"], "값": float(row["값"])} for row in confirmed_rows]
        ).set_index("구")
        st.bar_chart(chart_df, color=CHART_ACCENT_COLOR)

        missing_regions = [row["구"] for row in rows if row["상태"] != "확보"]
        if missing_regions:
            st.caption(
                f"미확보 구: {', '.join(missing_regions)} "
                "(0이 아니라 '미확보'로 표시되며 그래프에서는 제외됩니다)"
            )

# ---------------------------------------------------------------------------
# 3. 구 선택 상세 조회
# ---------------------------------------------------------------------------
st.header("3. 구 선택 상세 조회")
region_names = [r["region_name"] for r in regions]
selected_name = st.selectbox("조회할 구를 선택하세요", region_names, key="gov_selected_region")
selected_region = next(r for r in regions if r["region_name"] == selected_name)

st.markdown(f"#### {selected_region['region_name']} 상세 지표")
for category in CATEGORIES:
    indicators = selected_region["categories"].get(category, [])
    if not indicators:
        continue
    st.markdown(f"**{CATEGORY_LABELS[category]}**")
    detail_rows = [
        {
            "지표": ind["indicator_name"],
            "값": ind["value"] if ind["value"] is not None else "미확보",
            "단위": ind["unit"] or "-",
            "상태": ind["data_status"],
            "출처": ind["source"] or "-",
            "기준일": ind["reference_date"] or "-",
        }
        for ind in indicators
    ]
    st.dataframe(pd.DataFrame(detail_rows), hide_index=True, width="stretch")

st.caption(
    "위에서 다른 구를 선택해도 조회 결과만 바뀔 뿐, data/region_indicators.csv의 "
    "원본 데이터는 변경되지 않습니다(읽기 전용 조회)."
)

# ---------------------------------------------------------------------------
# 4. 구별 시설 수 차이 비교
# ---------------------------------------------------------------------------
st.header("4. 구별 시설 수 차이 비교")
st.caption("5개 구 사이의 수치 분포(최댓값·최솟값·차이)를 그대로 보여줍니다. 많고 적음이 곧 우열을 뜻하지 않습니다.")

diff_rows = []
for category in CATEGORIES:
    for code in _indicator_codes_for_category(regions, category):
        if not _is_fully_confirmed(regions, category, code):
            continue
        values = [
            (region["region_name"], float(_get_indicator(region, category, code)["value"]))
            for region in regions
        ]
        indicator_name = _get_indicator(regions[0], category, code)["indicator_name"]
        unit = _get_indicator(regions[0], category, code)["unit"] or ""
        max_region, max_value = max(values, key=lambda v: v[1])
        min_region, min_value = min(values, key=lambda v: v[1])
        diff_rows.append(
            {
                "지표": indicator_name,
                "최댓값 구": f"{max_region} ({max_value:g}{unit})",
                "최솟값 구": f"{min_region} ({min_value:g}{unit})",
                "차이": f"{max_value - min_value:g}{unit}",
            }
        )

if diff_rows:
    st.dataframe(pd.DataFrame(diff_rows), hide_index=True, width="stretch")
else:
    st.info("아직 5개 구 전체가 확보된 지표가 없어 차이를 비교할 수 없습니다.")

st.divider()
st.caption(
    "ℹ️ 향후 개선 시나리오 시뮬레이션(analysis/simulation.py)은 이번 단계에 포함되지 "
    "않았습니다. 가상의 시설 추가로 통근시간 단축·정주율 증가·인구 유입 등 실제 "
    "정책 효과를 추정하지 않습니다."
)
