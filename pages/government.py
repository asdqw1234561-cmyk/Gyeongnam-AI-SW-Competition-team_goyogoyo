# 정부용 화면
"""
창원시 생활권 시설 현황 및 지역 간 차이 분석 - 정부·행정 담당자용 1차 화면.

목적은 "시설 수 기반 지역 간 차이 분석"을 보여주는 것이지, 어떤 지역을 "정주
저해요인 확정"이나 "인프라 부족 지역 확정"으로 판정하는 것이 아니다. 데이터 조회는
전부 services/region_data.py의 get_all_regions()를 통해서만 하며(원본 CSV를
직접 읽거나 수치를 하드코딩하지 않는다), 이 페이지는 조회만 할 뿐 어떤 경로로도
CSV를 수정하지 않는다 - 조회 대상 구를 바꿔도 원본 데이터는 그대로다.

이주자용 화면(app.py)의 analysis/scoring.py 기반 "상대 비교 점수"와는 분리된 화면이다.
여기서는 점수를 계산하지 않고 실제 확보된 지표값 자체와 그 차이만 보여준다.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from analysis import simulation
from analysis.scoring import INDICATOR_CATEGORY
from services.region_data import REGION_SCOPES, get_all_regions

CHART_ACCENT_COLOR = "#2a78d6"
CATEGORIES = ["교통", "의료", "생활편의"]
CATEGORY_LABELS = {"교통": "🚌 교통", "의료": "🏥 의료", "생활편의": "🏪 생활편의"}

# 확보된 지표라도 "그래서 부족/열악하다"로 해석하지 않도록, 지표별 해석상 한계를
# 화면에 같이 보여준다. 원본 CSV의 note 컬럼(검증 정보 전체)과는 별개로, 여기서는
# 정부용 화면에 필요한 핵심 한계만 간결하게 요약한다.
INDICATOR_LIMITATIONS = {
    "bus_stop_count": "버스정류장 수는 실제 대중교통 접근성이나 이동시간을 의미하지 않습니다.",
    "hospital_count": "의료기관 수는 의원·치과의원·한의원 등을 모두 포함한 등록 기관 수이며, "
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


st.set_page_config(page_title="경남 시설 현황 분석 (정부용)", page_icon="🏛️")

st.title("🏛️ 경남 생활권 시설 현황 및 지역 간 차이 분석")
st.caption(
    "경남의 같은 유형 지역(창원시 구끼리 / 시끼리 / 군끼리)의 공공데이터 확보 지표를 비교합니다. 특정 지역을 "
    "'정주 저해요인 확정' 또는 '인프라 부족 지역 확정'으로 판정하는 화면이 아니며, "
    "표의 시설 수는 인구·면적·수요를 보정하지 않은 단순 개수이며, 시뮬레이션 점수만 인구 1만 명당으로 비교합니다."
)
with st.expander("⚠️ 이 화면을 해석할 때 반드시 유의할 점", expanded=True):
    for c in PAGE_CAVEATS:
        st.write(f"- {c}")

# 비교 범위(같은 유형끼리)를 고른 뒤 그 유형 전체를 조회한다(점수 계산 없음, 원본 CSV를 그대로 읽기만 함).
scope_label = st.selectbox("비교 범위", list(REGION_SCOPES), key="gov_scope")
regions = get_all_regions(region_type=REGION_SCOPES[scope_label])

# ---------------------------------------------------------------------------
# 1. 비교 범위 전체 시설 수 현황
# ---------------------------------------------------------------------------
st.header(f"1. {scope_label} 시설 수 현황")

summary_rows = []
for region in regions:
    row = {"지역": region["region_name"]}
    for category in CATEGORIES:
        for code in _indicator_codes_for_category(regions, category):
            if _is_fully_confirmed(regions, category, code):
                indicator = _get_indicator(region, category, code)
                row[indicator["indicator_name"]] = indicator["value"]
    summary_rows.append(row)

summary_df = pd.DataFrame(summary_rows)
confirmed_columns = [c for c in summary_df.columns if c != "지역"]
if confirmed_columns:
    st.dataframe(summary_df, hide_index=True, width="stretch")
    st.caption(f"현재 {scope_label} 전체가 확보된 지표: {', '.join(confirmed_columns)}")
else:
    st.info("아직 비교 범위 전체가 확보된 지표가 없습니다.")

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
selected_name = st.selectbox("조회할 지역을 선택하세요", region_names, key="gov_selected_region")
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
st.caption("비교 지역 사이의 수치 분포(최댓값·최솟값·차이)를 그대로 보여줍니다. 많고 적음이 곧 우열을 뜻하지 않습니다.")

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
    st.info("아직 비교 범위 전체가 확보된 지표가 없어 차이를 비교할 수 없습니다.")

# ---------------------------------------------------------------------------
# 5. 가상 시설 증감 시뮬레이션
# ---------------------------------------------------------------------------
st.divider()
st.header("5. 가상 시설 증감 시뮬레이션")
st.caption(
    "비교 범위 중 한 곳의 시설 수를 가상으로 늘리거나 줄였을 때, 인구 1만 명당 기준 "
    "상대 비교 점수와 같은 유형 지역 사이의 수치 차이가 어떻게 달라지는지 보여줍니다. "
    "**이것은 가정에 따른 수치 변화 시뮬레이션이며 실제 정책 효과 예측이 아닙니다** "
    "(실제 통근시간, 의료 접근성, 정주율, 인구 유입, 사업비 대비 효과 등은 계산하지 "
    "않습니다). 원본 데이터(data/region_indicators.csv)는 이 시뮬레이션으로 전혀 "
    "수정되지 않습니다."
)

if "gov_sim_result" not in st.session_state:
    st.session_state.gov_sim_result = None

# 5개 구 전부 확보된 지표만 시뮬레이션 대상으로 노출한다(미확보 지표 제외).
simulatable_codes = [
    code
    for code in simulation.SIMULATABLE_INDICATOR_CODES
    if _is_fully_confirmed(regions, INDICATOR_CATEGORY[code], code)
]
indicator_name_by_code = {
    code: (_get_indicator(regions[0], INDICATOR_CATEGORY[code], code) or {}).get("indicator_name", code)
    for code in simulatable_codes
}

if not simulatable_codes:
    st.info("현재 시뮬레이션 가능한(비교 범위 전부 확보된) 지표가 없습니다.")
else:
    col1, col2, col3 = st.columns(3)
    with col1:
        sim_region_name = st.selectbox("① 분석 대상 지역", region_names, key="gov_sim_region")
    with col2:
        sim_indicator_code = st.selectbox(
            "② 분석 대상 시설 지표",
            simulatable_codes,
            format_func=lambda c: indicator_name_by_code[c],
            key="gov_sim_indicator",
        )
    with col3:
        sim_delta = st.number_input(
            "③ 시설 수 가상 증감량(음수 가능)", value=0, step=1, key="gov_sim_delta"
        )

    st.markdown("**④ 비교 점수 가중치** (상대 점수 계산에만 쓰이며, 실제 시설 수에는 영향을 주지 않습니다)")
    if "gov_sim_weights_initialized" not in st.session_state:
        for code, weight in simulation.default_equal_weights(regions).items():
            st.session_state[f"gov_sim_weight_{code}"] = weight
        st.session_state.gov_sim_weights_initialized = True

    weight_cols = st.columns(len(simulatable_codes))
    sim_weights: dict[str, float] = {}
    for col, code in zip(weight_cols, simulatable_codes):
        with col:
            sim_weights[code] = st.slider(
                indicator_name_by_code[code], 0, 100, key=f"gov_sim_weight_{code}"
            )

    run_col, reset_col = st.columns(2)
    with run_col:
        run_clicked = st.button("⑤ 시뮬레이션 실행", type="primary", width="stretch")
    with reset_col:
        if st.button("초기화", width="stretch"):
            st.session_state.gov_sim_result = None

    if run_clicked:
        sim_region_id = next(r["region_id"] for r in regions if r["region_name"] == sim_region_name)
        st.session_state.gov_sim_result = simulation.simulate_facility_change(
            sim_region_id, sim_indicator_code, int(sim_delta), comparison_weights=sim_weights
        )

    sim_result = st.session_state.gov_sim_result
    if sim_result is None:
        st.caption("시뮬레이션을 실행하면 결과가 여기에 표시됩니다. 실행 전까지는 아무 값도 바뀌지 않습니다.")
    elif sim_result["status"] == "error":
        st.error(f"⚠️ {sim_result['message']}")
    else:
        st.success(
            f"'{sim_result['region_name']}'의 '{sim_result['indicator_name']}'을(를) 가상으로 "
            f"{sim_result['delta']:+d}개 변경했을 때의 시설 수 기반 상대 비교 결과입니다."
        )
        for caveat in sim_result["caveats"]:
            st.caption(f"· {caveat}")

        st.markdown("**⑥ 실제값 vs 가상값**")
        st.dataframe(
            pd.DataFrame(
                [
                    {"구분": "실제 시설 수", "값": f"{sim_result['actual_value']:g}개"},
                    {"구분": "가상 시설 수", "값": f"{sim_result['simulated_value']:g}개"},
                    {"구분": "가상 증감량", "값": f"{sim_result['delta']:+d}개"},
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            f"원본 데이터 출처: {sim_result['source'] or '-'} · 기준일: {sim_result['reference_date'] or '-'} "
            "(가상값은 이 화면 표시용일 뿐 원본 CSV에는 반영되지 않습니다)"
        )

        st.markdown("**⑦ 비교 범위 전체 변경 전후 비교**")
        compare_rows = [
            {
                "구": sim_result["score_change"][region["region_id"]]["region_name"],
                "실제 시설 수": f"{sim_result['facility_counts_before'][region['region_id']]:g}",
                "가상 시설 수": f"{sim_result['facility_counts_after'][region['region_id']]:g}",
                "변경 전 점수": round(sim_result["score_change"][region["region_id"]]["score_before"], 1),
                "변경 후 점수": round(sim_result["score_change"][region["region_id"]]["score_after"], 1),
                "점수 변화": round(sim_result["score_change"][region["region_id"]]["score_delta"], 1),
                "변경 전 순위": sim_result["score_change"][region["region_id"]]["rank_before"],
                "변경 후 순위": sim_result["score_change"][region["region_id"]]["rank_after"],
            }
            for region in regions
        ]
        compare_df = pd.DataFrame(compare_rows)
        st.dataframe(compare_df, hide_index=True, width="stretch")

        st.markdown("**⑧ 변경 전후 그래프**")
        chart_df = compare_df[["구", "변경 전 점수", "변경 후 점수"]].set_index("구")
        st.bar_chart(chart_df, color=[CHART_ACCENT_COLOR, "#e07b39"])

        st.caption(
            "사용된 가중치: "
            + ", ".join(
                f"{indicator_name_by_code.get(code, code)} {weight:.0f}%"
                for code, weight in sim_result["comparison_weights"].items()
            )
        )
