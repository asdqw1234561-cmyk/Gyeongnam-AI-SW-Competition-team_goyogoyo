# 개인용 화면
"""
📍 관심 위치 주변 생활시설 탐색 - 위치 기반 생활권 탐색 1단계.

app.py(창원시 5개 구 시설 수 기준 상대 비교 추천)와는 완전히 독립된 화면이다.
이 화면은 구별 추천 점수를 다시 계산하지 않는다 - "의창구의 버스정류장 수는
831개입니다"(구별 집계, analysis/scoring.py)와 "선택한 위치에서 직선거리 500m
이내에 버스정류장이 N개 있습니다"(이 화면, services/bus_stops.py·services/
convenience.py)는 서로 다른 분석이며, 이 둘을 섞어 새로운 추천 점수를 만들지
않는다.

현재 위치 기반 조회가 가능한 시설은 버스정류장과 편의점뿐이다(의료기관은 구별
집계만 연결되어 있어 이번 범위에 포함하지 않는다). 주소 지오코딩, 지도 클릭으로
좌표 선택하는 기능은 다음 단계 범위라 이번에는 구현하지 않는다.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from services import bus_stops, convenience

CHART_ACCENT_COLOR = "#2a78d6"

# docs/convenience_data.md에 기록된 창원시청 부근 근사 좌표. 정확한 건물 출입구나
# 특정 주거지 좌표가 아니다 - 예시 위치로만 쓴다.
EXAMPLE_LOCATIONS = {
    "창원시청 부근 예시 위치(근사 좌표)": (35.2280, 128.6811),
}

RADII_M = (300, 500, 1000)

# 창원시 대략 범위(참고용 경고 표시 - services 모듈의 bbox와 동일한 값)
_CHANGWON_BBOX = {"lat": (34.9, 35.45), "lon": (128.35, 128.95)}

DISTANCE_CAVEATS = [
    "반경은 직선거리(하버사인 공식) 기준입니다. 실제 도보 경로 거리가 아닙니다.",
    "실제 도보 이동시간과 대중교통 소요시간은 계산하지 않습니다.",
    "버스 노선 수나 배차 간격(운행 빈도)은 분석하지 않습니다 - 정류장 '개수'만 셉니다.",
    "편의점 수는 상가정보에 등록된 업소 기준이며, 동일 주소 중복 등록 등으로 실제 "
    "영업 매장 수와 다를 수 있습니다.",
    "현재는 버스정류장과 편의점만 위치 기반 조회가 가능합니다. 의료기관은 구별 집계만 "
    "연결되어 있어 이 화면의 위치 기반 검색에는 포함되지 않습니다.",
    "이 화면은 창원시 5개 구 비교 추천(상대 점수)과는 별개의 참고 분석입니다 - "
    "구별 추천 점수를 다시 계산하지 않습니다.",
]


def _is_outside_changwon(lat: float, lon: float) -> bool:
    return not (
        _CHANGWON_BBOX["lat"][0] <= lat <= _CHANGWON_BBOX["lat"][1]
        and _CHANGWON_BBOX["lon"][0] <= lon <= _CHANGWON_BBOX["lon"][1]
    )


def _render_comparison_table(lat: float, lon: float) -> None:
    """300m/500m/1km 반경별 버스정류장·편의점 수 비교표. 실제 0건과 조회 실패를
    구분해서 표시한다(실패는 상태 문구, 0건은 숫자 0 그대로)."""
    bus_result = bus_stops.count_nearby_by_radius(lat, lon, radii=RADII_M)
    store_result = convenience.count_nearby_by_radius(lat, lon, radii=RADII_M)

    def _cell(result: dict, radius: int) -> str:
        if result["status"] != "ok":
            return "조회 실패" if result["status"] != "no_data" else "미확보"
        value = result["counts"].get(radius)
        return "미확보" if value is None else f"{value}건"

    rows = [
        {"시설": "🚌 버스정류장", **{f"{r}m" if r != 1000 else "1km": _cell(bus_result, r) for r in RADII_M}},
        {"시설": "🏪 편의점", **{f"{r}m" if r != 1000 else "1km": _cell(store_result, r) for r in RADII_M}},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if bus_result["status"] != "ok":
        st.warning(f"⚠️ 버스정류장 반경별 조회 실패: {bus_result['message']}")
    if store_result["status"] not in ("ok",):
        st.info(f"ℹ️ 편의점 데이터: {store_result['message']}")

    for w in bus_result.get("warnings", []) + store_result.get("warnings", []):
        st.caption(f"⚠️ {w}")


def _render_nearby_list(lat: float, lon: float, radius_m: int, max_results: int) -> None:
    col_bus, col_store = st.columns(2)

    with col_bus:
        st.markdown(f"**🚌 버스정류장 (반경 {radius_m}m 이내, 직선거리 가까운 순)**")
        result = bus_stops.find_nearby_bus_stops(lat, lon, radius_m=radius_m, max_results=max_results)
        st.caption(result["message"])
        if result["status"] == "ok" and result["stops"]:
            df = pd.DataFrame(
                [
                    {
                        "순위": s["rank"],
                        "정류소명": s["stop_name"],
                        "소속 구": s["district"],
                        "직선거리(m)": s["straight_distance_m"],
                        "품질 주의": bus_stops.QUALITY_FLAG_LABELS.get(s["quality_flag"], "-")
                        if s["quality_flag"]
                        else "-",
                    }
                    for s in result["stops"]
                ]
            )
            st.dataframe(df, hide_index=True, width="stretch")
            st.caption(f"출처: {result['source']} (기준일 {result['reference_date']})")
        elif result["status"] == "invalid_input":
            st.error(result["message"])
        elif result["status"] == "no_data":
            st.warning(result["message"])

    with col_store:
        st.markdown(f"**🏪 편의점 (반경 {radius_m}m 이내, 직선거리 가까운 순)**")
        result = convenience.find_nearby_stores(lat, lon, radius_m=radius_m, max_results=max_results)
        st.caption(result["message"])
        if result["status"] == "ok" and result["stores"]:
            df = pd.DataFrame(
                [
                    {
                        "순위": s["rank"],
                        "상호명": s["facility_name"],
                        "소속 구": s["district"],
                        "직선거리(m)": s["straight_distance_m"],
                        "도로명주소": s["road_address"],
                    }
                    for s in result["stores"]
                ]
            )
            st.dataframe(df, hide_index=True, width="stretch")
            if result["source"]:
                st.caption(f"출처: {result['source']} (기준년월 {result['reference_date']})")
        elif result["status"] == "invalid_input":
            st.error(result["message"])
        elif result["status"] == "no_data":
            st.warning(result["message"])


st.set_page_config(page_title="관심 위치 주변 생활시설 탐색", page_icon="📍")

st.title("📍 관심 위치 주변 생활시설 탐색")
st.caption(
    "관심 있는 위치를 지정하면 그 주변의 실제 버스정류장·편의점 수를 직선거리 기준으로 "
    "확인할 수 있습니다. 창원시 5개 구 비교 추천과는 별개의 참고 분석입니다."
)

st.subheader("1. 검색 중심 위치 설정")
location_mode = st.radio(
    "위치 설정 방식", ["예시 위치 선택", "위도·경도 직접 입력"], horizontal=True
)

if location_mode == "예시 위치 선택":
    label = st.selectbox("예시 위치", list(EXAMPLE_LOCATIONS.keys()))
    lat_input, lon_input = EXAMPLE_LOCATIONS[label]
    st.info(
        f"ℹ️ '{label}' (위도 {lat_input}, 경도 {lon_input})를 사용합니다. 이 좌표는 "
        "근사치이며, 정확한 건물 출입구 위치나 특정 주거지 좌표가 아닙니다."
    )
else:
    col_lat, col_lon = st.columns(2)
    with col_lat:
        lat_input = st.number_input(
            "위도 (latitude)", min_value=-90.0, max_value=90.0, value=35.2280, step=0.0001, format="%.6f"
        )
    with col_lon:
        lon_input = st.number_input(
            "경도 (longitude)", min_value=-180.0, max_value=180.0, value=128.6811, step=0.0001, format="%.6f"
        )
    st.caption(
        "주소를 입력해 좌표를 자동으로 찾는 기능(지오코딩)과 지도에서 클릭해 좌표를 "
        "선택하는 기능은 아직 구현되지 않았습니다 - 위도·경도 숫자를 직접 입력해야 합니다."
    )
    if _is_outside_changwon(lat_input, lon_input):
        st.warning(
            "⚠️ 입력하신 좌표가 창원시 범위를 크게 벗어난 것으로 보입니다. 조회 자체는 "
            "진행되지만, 버스정류장·편의점 데이터는 창원시 5개 구만 포함합니다(다른 "
            "지역은 0건으로 나올 수 있습니다)."
        )

lat_valid = isinstance(lat_input, (int, float)) and math.isfinite(lat_input) and -90 <= lat_input <= 90
lon_valid = isinstance(lon_input, (int, float)) and math.isfinite(lon_input) and -180 <= lon_input <= 180

st.subheader("2. 반경 선택")
radius_choice = st.radio("주변 시설 목록에 사용할 반경", RADII_M, index=1, horizontal=True, format_func=lambda r: f"{r}m" if r != 1000 else "1km")
max_results = st.slider("시설별 최대 표시 개수", min_value=1, max_value=30, value=10)

if not (lat_valid and lon_valid):
    st.error("위도·경도가 올바르지 않습니다. 숫자이며 각각 -90~90, -180~180 범위여야 합니다.")
else:
    st.divider()
    st.subheader("3·4. 주변 버스정류장·편의점 수 (반경별 비교)")
    st.caption("아래 표는 300m / 500m / 1km 반경 안의 전체 건수입니다. 실제로 0건인 경우와 "
               "데이터를 조회할 수 없는 경우('미확보'/'조회 실패')는 구분해서 표시됩니다.")
    _render_comparison_table(lat_input, lon_input)

    st.divider()
    st.subheader(f"5·6. 주변 시설 목록 (반경 {radius_choice}m 이내)" if radius_choice != 1000 else "5·6. 주변 시설 목록 (반경 1km 이내)")
    _render_nearby_list(lat_input, lon_input, radius_choice, max_results)

    st.divider()
    st.subheader("7. 데이터 출처와 한계 안내")
    for c in DISTANCE_CAVEATS:
        st.caption(f"· {c}")
    with st.expander("데이터 품질 참고 (버스정류장)"):
        check = bus_stops.verify_official_counts()
        st.write(
            f"창원시 5개 구 경계 내부로 판정된 버스정류장은 총 {check['actual_total']}건입니다 "
            f"(경계 밖 제외 기준은 data/region_indicators.csv의 구별 집계와 동일)."
        )
        st.caption(
            f"· 단순화된 행정경계의 2m 오차를 원본 SHP 기준으로 보정한 정류장 "
            f"{check['boundary_correction_count']}건이 포함되어 있습니다."
        )
        st.caption(
            f"· 법정동명과 공간판정이 달라 위치정보 불일치 가능성이 있는 정류장 "
            f"{check['quality_exception_count']}건이 포함되어 있습니다(주변 목록에 "
            "'품질 주의' 칸으로 표시됩니다)."
        )
        if not check["ok"]:
            st.error(
                "⚠️ 현재 재계산된 집계가 기존 공식 집계와 다릅니다. 원본 데이터가 "
                "변경되었을 수 있으니 확인이 필요합니다."
            )
