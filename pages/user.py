# 개인용 화면
"""
📍 관심 위치 주변 생활시설 탐색 - 위치 기반 생활권 탐색 2단계(지도 추가).

app.py(창원시 5개 구 시설 수 기준 상대 비교 추천)와는 완전히 독립된 화면이다.
이 화면은 구별 추천 점수를 다시 계산하지 않는다 - "의창구의 버스정류장 수는
831개입니다"(구별 집계, analysis/scoring.py)와 "선택한 위치에서 직선거리 500m
이내에 버스정류장이 N개 있습니다"(이 화면, services/bus_stops.py·services/
convenience.py)는 서로 다른 분석이며, 이 둘을 섞어 새로운 추천 점수를 만들지
않는다. 지도에 보이는 시설 수도 구별 추천 점수에 자동으로 합산하지 않는다.

[지도 라이브러리]
    folium + streamlit-folium. 둘 다 import 실패 시(미설치 등) 지도 섹션만
    경고와 함께 건너뛰고, 반경별 비교표·주변 시설 목록은 그대로 동작한다 -
    지도(특히 타일 로딩)는 전적으로 선택적 기능이다.

[지도 클릭으로 검색 중심 변경 - 상태 관리]
    지도를 클릭하면 바로 검색 중심이 바뀌지 않는다. 클릭 좌표는 먼저
    session_state.map_click_candidate(미확정 후보)에만 저장되고, 사용자가
    "이 위치에서 주변 시설 검색" 버튼을 눌러야 session_state.search_center
    (확정된 검색 중심 - 비교표·목록·지도 마커가 전부 이 값을 쓴다)로 반영된다.
    streamlit-folium의 st_folium()은 마지막 클릭 좌표를 매 재실행마다 그대로
    돌려주므로, session_state.map_click_seen에 "이미 처리한 클릭 좌표"를
    저장해두고 같은 좌표가 반복 수신되면 무시한다 - 과거 클릭이 반복 적용되거나
    무한 재실행되는 일을 막는다.

[지도 마커와 아래 목록의 일치]
    find_nearby_bus_stops()/find_nearby_stores() 결과를 한 번만 계산해 지도
    마커와 "주변 시설 목록" 표에 동일하게 사용한다(따로 두 번 조회하지 않음) -
    지도에 보이는 시설과 표에 적힌 시설이 항상 같은 집합이다. 지도에 찍는 마커
    개수는 항상 displayed_count(= 목록 표시 개수)이며, 반경 안 전체 시설 수
    (total_count)와 다를 수 있다는 점을 캡션으로 명시한다.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from agent.location_agent import TOOL_LABELS as LOCATION_TOOL_LABELS
from agent.location_agent import run_location_agent
from services import bus_stops, convenience
from services.map_markers import (
    SEARCH_CENTER_MARKER_COLOR,
    SEARCH_CENTER_MARKER_ICON,
    build_bus_stop_markers,
    build_convenience_markers,
)

try:
    import folium
    from streamlit_folium import st_folium

    _MAP_AVAILABLE = True
except ImportError:
    _MAP_AVAILABLE = False

CHART_ACCENT_COLOR = "#2a78d6"

# docs/convenience_data.md에 기록된 창원시청 부근 근사 좌표. 정확한 건물 출입구나
# 특정 주거지 좌표가 아니다 - 예시 위치로만 쓴다.
EXAMPLE_LOCATIONS = {
    "창원시청 부근 예시 위치(근사 좌표)": (35.2280, 128.6811),
}
_DEFAULT_LOCATION_LABEL = next(iter(EXAMPLE_LOCATIONS))

RADII_M = (300, 500, 1000)
LOCATION_MODES = ["예시 위치 선택", "위도·경도 직접 입력", "지도 클릭으로 위치 선택"]

# 창원시 대략 범위(참고용 경고 표시 - services 모듈의 bbox와 동일한 값)
_CHANGWON_BBOX = {"lat": (34.9, 35.45), "lon": (128.35, 128.95)}

DISTANCE_CAVEATS = [
    "반경은 직선거리(하버사인 공식) 기준입니다. 실제 도보 경로 거리가 아닙니다.",
    "지도의 반경 원은 구면상 직선거리 계산의 시각적 참고용이며, 실제 도보 가능 범위를 "
    "뜻하지 않습니다.",
    "실제 도보 이동시간과 대중교통 소요시간은 계산하지 않습니다.",
    "버스 노선 수나 배차 간격(운행 빈도)은 분석하지 않습니다 - 정류장 '개수'만 셉니다.",
    "편의점 수는 상가정보에 등록된 업소 기준이며, 동일 주소 중복 등록 등으로 실제 "
    "영업 매장 수와 다를 수 있습니다.",
    "현재는 버스정류장과 편의점만 위치 기반 조회가 가능합니다. 의료기관은 구별 집계만 "
    "연결되어 있어 이 화면의 위치 기반 검색에는 포함되지 않습니다.",
    "이 화면은 창원시 5개 구 비교 추천(상대 점수)과는 별개의 참고 분석입니다 - "
    "구별 추천 점수를 다시 계산하지 않습니다.",
    "지도 타일은 외부 지도 서비스에서 불러옵니다. 타일이 느리게 뜨거나 표시되지 않아도 "
    "아래 반경별 비교표·시설 목록 수치에는 영향이 없습니다(둘 다 서버에서 별도로 계산).",
]


def _is_outside_changwon(lat: float, lon: float) -> bool:
    return not (
        _CHANGWON_BBOX["lat"][0] <= lat <= _CHANGWON_BBOX["lat"][1]
        and _CHANGWON_BBOX["lon"][0] <= lon <= _CHANGWON_BBOX["lon"][1]
    )


def _is_valid_coord(lat, lon) -> bool:
    return (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    )


def _build_results_map(
    center_lat: float,
    center_lon: float,
    radius_m: int,
    bus_markers: list[dict],
    store_markers: list[dict],
    show_bus: bool = True,
    show_store: bool = True,
):
    """folium.Map 객체를 만든다(이 함수만 folium에 의존 - 마커 데이터 준비는
    위의 순수 함수들이 이미 끝냈다)."""
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=16)

    folium.Marker(
        [center_lat, center_lon],
        tooltip="검색 중심 위치",
        popup="검색 중심 위치",
        icon=folium.Icon(color=SEARCH_CENTER_MARKER_COLOR, icon=SEARCH_CENTER_MARKER_ICON, prefix="fa"),
    ).add_to(fmap)

    folium.Circle(
        [center_lat, center_lon],
        radius=radius_m,
        color=CHART_ACCENT_COLOR,
        weight=2,
        fill=True,
        fill_opacity=0.08,
        tooltip=f"반경 {radius_m}m (직선거리, 시각적 참고용)",
    ).add_to(fmap)

    if show_bus and bus_markers:
        bus_group = folium.FeatureGroup(name=f"🚌 버스정류장 (지도 표시 {len(bus_markers)}개)")
        for m in bus_markers:
            folium.Marker(
                [m["lat"], m["lon"]],
                tooltip=m["label"],
                popup=folium.Popup("<br>".join(m["popup_lines"]), max_width=260),
                icon=folium.Icon(color=m["color"], icon=m["icon"], prefix="fa"),
            ).add_to(bus_group)
        bus_group.add_to(fmap)

    if show_store and store_markers:
        store_group = folium.FeatureGroup(name=f"🏪 편의점 (지도 표시 {len(store_markers)}개)")
        for m in store_markers:
            folium.Marker(
                [m["lat"], m["lon"]],
                tooltip=m["label"],
                popup=folium.Popup("<br>".join(m["popup_lines"]), max_width=260),
                icon=folium.Icon(color=m["color"], icon=m["icon"], prefix="fa"),
            ).add_to(store_group)
        store_group.add_to(fmap)

    # 레이어 컨트롤 체크박스로 버스정류장/편의점 마커를 각각 표시·숨김 가능.
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap


def _render_comparison_table(lat: float, lon: float) -> None:
    """300m/500m/1km 반경별 버스정류장·편의점 수 비교표. 실제 0건과 조회 실패를
    구분해서 표시한다(실패는 상태 문구, 0건은 숫자 0 그대로)."""
    bus_result = bus_stops.count_nearby_by_radius(lat, lon, radii=RADII_M)
    store_result = convenience.count_nearby_by_radius(lat, lon, radii=RADII_M)

    def _cell(result: dict, radius: int) -> str:
        if result["status"] != "ok":
            return "미확보" if result["status"] == "no_data" else "조회 실패"
        value = result["counts"].get(radius)
        return "미확보" if value is None else f"{value}건"

    rows = [
        {"시설": "🚌 버스정류장", **{f"{r}m" if r != 1000 else "1km": _cell(bus_result, r) for r in RADII_M}},
        {"시설": "🏪 편의점", **{f"{r}m" if r != 1000 else "1km": _cell(store_result, r) for r in RADII_M}},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if bus_result["status"] not in ("ok",):
        st.info(f"ℹ️ 버스정류장 데이터: {bus_result['message']}")
    if store_result["status"] not in ("ok",):
        st.info(f"ℹ️ 편의점 데이터: {store_result['message']}")

    for w in bus_result.get("warnings", []) + store_result.get("warnings", []):
        st.caption(f"⚠️ {w}")


def _render_nearby_list(bus_result: dict, store_result: dict) -> None:
    """이미 계산된 결과(지도 마커와 동일한 조회 결과)를 표로 보여준다 - 여기서
    다시 조회하지 않는다(지도와 목록이 서로 다른 집합을 보여주는 일을 막기 위함)."""
    col_bus, col_store = st.columns(2)

    with col_bus:
        st.markdown("**🚌 버스정류장 (직선거리 가까운 순)**")
        st.caption(bus_result["message"])
        if bus_result["status"] == "ok" and bus_result["stops"]:
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
                    for s in bus_result["stops"]
                ]
            )
            st.dataframe(df, hide_index=True, width="stretch")
            st.caption(f"출처: {bus_result['source']} (기준일 {bus_result['reference_date']})")
        elif bus_result["status"] == "invalid_input":
            st.error(bus_result["message"])
        elif bus_result["status"] == "no_data":
            st.warning(bus_result["message"])

    with col_store:
        st.markdown("**🏪 편의점 (직선거리 가까운 순)**")
        st.caption(store_result["message"])
        if store_result["status"] == "ok" and store_result["stores"]:
            df = pd.DataFrame(
                [
                    {
                        "순위": s["rank"],
                        "상호명": s["facility_name"],
                        "소속 구": s["district"],
                        "직선거리(m)": s["straight_distance_m"],
                        "도로명주소": s["road_address"],
                    }
                    for s in store_result["stores"]
                ]
            )
            st.dataframe(df, hide_index=True, width="stretch")
            if store_result["source"]:
                st.caption(f"출처: {store_result['source']} (기준년월 {store_result['reference_date']})")
        elif store_result["status"] == "invalid_input":
            st.error(store_result["message"])
        elif store_result["status"] == "no_data":
            st.warning(store_result["message"])


def _render_agent_bus_result(radius_m: int, result: dict) -> None:
    st.markdown(f"**🚌 버스정류장 (반경 {radius_m}m)**")
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


def _render_agent_convenience_result(radius_m: int, result: dict) -> None:
    st.markdown(f"**🏪 편의점 (반경 {radius_m}m)**")
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


def _render_agent_compare_result(result: dict) -> None:
    st.markdown("**📊 반경별(300m/500m/1km) 비교**")
    bus_cmp = result["bus_stops"]
    store_cmp = result["convenience_stores"]

    def _cell(cmp_result: dict, radius: int) -> str:
        if cmp_result["status"] != "ok":
            return "미확보" if cmp_result["status"] == "no_data" else "조회 실패"
        value = cmp_result["counts"].get(radius)
        return "미확보" if value is None else f"{value}건"

    rows = [
        {"시설": "🚌 버스정류장", **{f"{r}m" if r != 1000 else "1km": _cell(bus_cmp, r) for r in RADII_M}},
        {"시설": "🏪 편의점", **{f"{r}m" if r != 1000 else "1km": _cell(store_cmp, r) for r in RADII_M}},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_agent_executed_entry(entry: dict) -> None:
    icon = "✅" if entry["executed"] else "❌"
    st.caption(f"{icon} {LOCATION_TOOL_LABELS.get(entry['tool'], entry['tool'])}" + (f" — {entry['reason']}" if entry.get("reason") else ""))
    if not entry["executed"]:
        st.error(f"⚠️ 실행 중 오류: {entry['error']}")
        return
    if entry["tool"] == "find_nearby_bus_stops":
        _render_agent_bus_result(entry["radius_m"], entry["result"])
    elif entry["tool"] == "find_nearby_convenience_stores":
        _render_agent_convenience_result(entry["radius_m"], entry["result"])
    elif entry["tool"] == "compare_nearby_facilities":
        _render_agent_compare_result(entry["result"])


def render_location_agent_execution_log(result: dict) -> None:
    """'🔍 AI 위치 분석 실행 과정 보기' 접기 영역 - run_location_agent()의 반환값을
    그대로 보여줄 뿐 여기서 새로 판단하거나 숫자를 만들지 않는다. "AI가 제안한
    계획"과 "Python이 실제 실행한 작업"을 항상 구분해서 표시한다."""
    corrections = result.get("corrections", [])
    has_corrections = bool(corrections)

    if result["mode"] == "ai_planned":
        if has_corrections:
            st.success("✅ AI가 제안한 계획을 Python이 검증·보완한 후 실행했습니다.")
        else:
            st.success("✅ AI가 제안한 계획을 Python이 검증한 후 실행했습니다.")
    elif result["planner_error"]:
        st.warning(f"⚠️ AI 분석 계획을 생성하지 못해 기본 조회 절차를 사용했습니다: {result['planner_error']}")
    else:
        st.warning("⚠️ AI가 제안한 계획이 검증을 통과하지 못해 기본 조회 절차를 사용했습니다.")

    st.markdown("**1. AI가 제안한 분석 목표**")
    if result["goals"]:
        for g in result["goals"]:
            st.caption(f"· {g}")
    else:
        st.caption("(없음)")

    st.markdown("**2. AI가 제안한 도구 계획 (검증 전 원본)**")
    if result["planned_tool_calls"]:
        for call in result["planned_tool_calls"]:
            tool = call.get("tool") if isinstance(call, dict) else call
            reason = call.get("reason", "") if isinstance(call, dict) else ""
            st.caption(f"· {LOCATION_TOOL_LABELS.get(tool, tool)}" + (f" — {reason}" if reason else ""))
    else:
        st.caption("AI 계획 호출 자체가 없었습니다(claude CLI·Ollama 연결 실패 등).")

    st.markdown("**3. Python 검증 결과**")
    if has_corrections:
        for corr in corrections:
            orig_label = LOCATION_TOOL_LABELS.get(corr["original_tool"], corr["original_tool"])
            to_labels = " + ".join(LOCATION_TOOL_LABELS.get(t, t) for t in corr["corrected_to"])
            st.caption(f"· 보정: {orig_label} → {to_labels}")
            st.caption(f"  이유: {corr['note']}")
    if result["notes"]:
        for n in result["notes"]:
            st.caption(f"· {n}")
    if not has_corrections and not result["notes"]:
        st.caption("AI 계획이 사용자 요청 범위에 적합하여 그대로 승인했습니다.")

    st.markdown("**4. 실제 실행한 도구**")
    if result["executed_tool_calls"]:
        for i, entry in enumerate(result["executed_tool_calls"], start=1):
            icon = "✅" if entry["executed"] else "❌"
            err = f" — 오류: {entry['error']}" if entry.get("error") else ""
            st.caption(f"{icon} {i}. {LOCATION_TOOL_LABELS.get(entry['tool'], entry['tool'])}{err}")
    else:
        st.caption("실행된 도구가 없습니다(지원하지 않는 요청이었거나 실행할 내용이 없었습니다).")

    st.markdown("**5. 기본 절차 전환 여부**")
    if result["mode"] == "fallback_default":
        st.caption("예 — AI 계획을 사용할 수 없어 기본 조회 절차(반경별 전체 비교)로 전환했습니다.")
    else:
        st.caption("아니오 — AI 계획을 사용했습니다.")

    with st.expander("사용 가능한 도구 목록"):
        for tool_name, label in LOCATION_TOOL_LABELS.items():
            st.caption(f"· {label} ({tool_name})")


def render_location_agent_result(result: dict) -> None:
    if result["status"] == "rejected_input":
        st.error(f"⚠️ {result['message']}")
        return

    if result["search_center"] != st.session_state.search_center:
        prev_lat, prev_lon = result["search_center"]
        st.warning(
            f"⚠️ 이 결과는 이전 위치(위도 {prev_lat:.6f}, 경도 {prev_lon:.6f})에서 실행한 "
            "결과입니다. 현재 확정된 검색 중심과 다릅니다 - 최신 결과를 보려면 다시 실행해 "
            "주세요."
        )

    st.markdown("**A. 사용자 요청**")
    st.write(f"> {result['user_text']}")

    st.markdown("**B. 실제 검색 기준**")
    lat, lon = result["search_center"]
    radius_label = f"{result['resolved_radius_m']}m" if result["resolved_radius_m"] != 1000 else "1km"
    source_label = "요청 문장에서 직접 명시" if result["radius_source"] == "explicit_text" else "화면에서 선택된 기본값"
    st.write(f"검색 중심: 위도 {lat:.6f}, 경도 {lon:.6f} · 검색 반경: {radius_label} ({source_label})")

    st.markdown("**C. AI 분석 결과**")
    if result["unsupported_requests"]:
        for item in result["unsupported_requests"]:
            st.info(f"ℹ️ 지원하지 않는 요청입니다: {item['request']} - {item['reason']}")

    if result["executed_tool_calls"]:
        for entry in result["executed_tool_calls"]:
            _render_agent_executed_entry(entry)
    elif not result["unsupported_requests"]:
        st.caption("실행된 조회가 없습니다.")

    st.caption(
        "실제 도보 이동시간·대중교통 소요시간·버스 노선 및 배차 간격은 계산하지 않습니다 "
        "- 위 수치는 정류소아이디·등록 업소 기준 조회 결과일 뿐입니다."
    )

    with st.expander("🔍 AI 위치 분석 실행 과정 보기"):
        render_location_agent_execution_log(result)


# ---------------------------------------------------------------------------
# 화면 시작
# ---------------------------------------------------------------------------
st.set_page_config(page_title="관심 위치 주변 생활시설 탐색", page_icon="📍")

st.title("📍 관심 위치 주변 생활시설 탐색")
st.caption(
    "관심 있는 위치를 지정하면 그 주변의 실제 버스정류장·편의점을 지도와 직선거리 기준 "
    "수치로 확인할 수 있습니다. 창원시 5개 구 비교 추천과는 별개의 참고 분석입니다."
)

if "search_center" not in st.session_state:
    st.session_state.search_center = EXAMPLE_LOCATIONS[_DEFAULT_LOCATION_LABEL]
if "map_click_candidate" not in st.session_state:
    st.session_state.map_click_candidate = None  # 지도에서 클릭했지만 아직 확정 안 한 좌표
if "map_click_seen" not in st.session_state:
    st.session_state.map_click_seen = None  # 마지막으로 "처리"한 클릭 좌표(중복 처리 방지)
if "location_agent_result" not in st.session_state:
    # run_location_agent()의 반환값을 통째로 저장한다. "🤖 AI 분석 실행" 버튼을 눌렀을
    # 때만 채워지며(지도 이동·레이어 토글·반경 변경 등 다른 재실행에서는 절대 갱신하지
    # 않음), 실행 당시의 search_center/반경도 같이 저장돼 있어 이후 검색 중심이
    # 바뀌어도 "이전 위치의 결과"임을 구분해서 보여줄 수 있다.
    st.session_state.location_agent_result = None

st.subheader("1. 검색 중심 위치 설정")
location_mode = st.radio("위치 설정 방식", LOCATION_MODES, horizontal=True)

if location_mode == "예시 위치 선택":
    label = st.selectbox("예시 위치", list(EXAMPLE_LOCATIONS.keys()))
    lat_input, lon_input = EXAMPLE_LOCATIONS[label]
    st.session_state.search_center = (lat_input, lon_input)
    st.info(
        f"ℹ️ '{label}' (위도 {lat_input}, 경도 {lon_input})를 사용합니다. 이 좌표는 "
        "근사치이며, 정확한 건물 출입구 위치나 특정 주거지 좌표가 아닙니다."
    )

elif location_mode == "위도·경도 직접 입력":
    prev_lat, prev_lon = st.session_state.search_center
    col_lat, col_lon = st.columns(2)
    with col_lat:
        lat_input = st.number_input(
            "위도 (latitude)", min_value=-90.0, max_value=90.0, value=float(prev_lat), step=0.0001, format="%.6f"
        )
    with col_lon:
        lon_input = st.number_input(
            "경도 (longitude)", min_value=-180.0, max_value=180.0, value=float(prev_lon), step=0.0001, format="%.6f"
        )
    st.caption("주소를 입력해 좌표를 자동으로 찾는 기능(지오코딩)은 아직 구현되지 않았습니다.")
    if _is_valid_coord(lat_input, lon_input):
        st.session_state.search_center = (lat_input, lon_input)
        if _is_outside_changwon(lat_input, lon_input):
            st.warning(
                "⚠️ 입력하신 좌표가 창원시 범위를 크게 벗어난 것으로 보입니다. 조회 자체는 "
                "진행되지만, 버스정류장·편의점 데이터는 창원시 5개 구만 포함합니다(다른 "
                "지역은 0건으로 나올 수 있습니다)."
            )

else:  # "지도 클릭으로 위치 선택"
    # 클릭 위치 확인·확정(승인)·취소 UI는 아래 "3. 지도로 보기" 섹션에서 지도
    # 바로 밑에 렌더링한다(사용성 개선: 클릭 -> 스크롤 없이 바로 아래에서 확정).
    st.caption("아래 지도를 클릭해 위치를 선택한 뒤, 지도 바로 아래에서 확정하세요.")

cur_lat, cur_lon = st.session_state.search_center
st.caption(f"현재 확정된 검색 중심: 위도 {cur_lat:.6f}, 경도 {cur_lon:.6f}")

st.subheader("2. 반경 선택")
radius_choice = st.radio(
    "지도·주변 시설 목록에 사용할 반경",
    RADII_M,
    index=1,
    horizontal=True,
    format_func=lambda r: f"{r}m" if r != 1000 else "1km",
)
max_results = st.slider("시설 유형별 최대 표시 개수(지도·목록 공통)", min_value=1, max_value=30, value=10)

lat_input, lon_input = st.session_state.search_center

if not _is_valid_coord(lat_input, lon_input):
    st.error("위도·경도가 올바르지 않습니다. 숫자이며 각각 -90~90, -180~180 범위여야 합니다.")
else:
    # 지도 마커와 아래 "주변 시설 목록"이 반드시 같은 결과를 쓰도록, 여기서 딱 한 번만
    # 조회해서 재사용한다(총 건수 반경별 비교표는 별도 - 300/500/1000 전부 필요하므로
    # count_nearby_by_radius()를 그대로 쓴다).
    bus_result = bus_stops.find_nearby_bus_stops(lat_input, lon_input, radius_m=radius_choice, max_results=max_results)
    store_result = convenience.find_nearby_stores(lat_input, lon_input, radius_m=radius_choice, max_results=max_results)
    bus_markers = build_bus_stop_markers(bus_result)
    store_markers = build_convenience_markers(store_result)

    st.divider()
    st.subheader("3. 지도로 보기")
    if not _MAP_AVAILABLE:
        st.warning(
            "⚠️ 지도 라이브러리(folium/streamlit-folium)를 불러오지 못해 지도를 표시할 수 "
            "없습니다. 아래 반경별 비교표와 시설 목록은 정상적으로 이용할 수 있습니다."
        )
    else:
        col_show_bus, col_show_store = st.columns(2)
        with col_show_bus:
            show_bus = st.checkbox("🚌 버스정류장 마커 표시", value=True)
        with col_show_store:
            show_store = st.checkbox("🏪 편의점 마커 표시", value=True)

        st.caption(
            f"지도에는 반경 {radius_choice}m 안 가까운 순 최대 {max_results}개씩만 표시됩니다 "
            f"(버스정류장 {len(bus_markers)}개 / 편의점 {len(store_markers)}개 표시 중 - "
            "반경 안 전체 시설 수는 아래 비교표를 참고하세요). 지도 오른쪽 위 레이어 "
            "컨트롤로도 마커 종류를 켜고 끌 수 있습니다."
        )

        try:
            fmap = _build_results_map(
                lat_input, lon_input, radius_choice, bus_markers, store_markers,
                show_bus=show_bus, show_store=show_store,
            )
            map_data = st_folium(
                fmap,
                height=480,
                use_container_width=True,
                key="user_location_map",
                returned_objects=["last_clicked"],
            )
        except Exception as exc:  # 지도 컴포넌트 자체 오류 - 나머지 화면은 계속 동작해야 함
            st.warning(f"⚠️ 지도를 표시하는 중 문제가 발생했습니다: {exc}")
            map_data = None

        # 새로 들어온 클릭만 후보로 받아들인다 - st_folium()은 재실행될 때마다 마지막
        # 클릭 좌표를 그대로 돌려주므로, 이미 처리한 좌표(map_click_seen)와 같으면
        # 무시한다(과거 클릭 반복 적용 방지). 여기서는 일부러 st.rerun()을 호출하지
        # 않는다 - 클릭 자체가 이미 streamlit-folium 컴포넌트의 재실행을 한 번
        # 일으키므로, 바로 아래에서 같은 실행 흐름 안에 candidate를 읽어 승인 UI를
        # 그리면 추가 재실행 없이 즉시 보인다(불필요한 재실행·스크롤 최소화).
        if map_data and location_mode == "지도 클릭으로 위치 선택":
            clicked = map_data.get("last_clicked")
            if clicked and clicked.get("lat") is not None and clicked.get("lng") is not None:
                click_sig = (round(clicked["lat"], 7), round(clicked["lng"], 7))
                if click_sig != st.session_state.map_click_seen:
                    st.session_state.map_click_seen = click_sig
                    st.session_state.map_click_candidate = (clicked["lat"], clicked["lng"])

        # 클릭 확인·확정·취소 UI - 지도 바로 아래에 둬서 스크롤 없이 바로 이어서
        # 조작할 수 있게 한다(요구사항: 승인 UI를 지도 아래로 이동).
        if location_mode == "지도 클릭으로 위치 선택":
            candidate = st.session_state.map_click_candidate
            if candidate is not None:
                c_lat, c_lon = candidate
                st.info(f"🖱️ 새로 클릭한 위치(아직 미확정): 위도 {c_lat:.6f}, 경도 {c_lon:.6f}")
                if _is_outside_changwon(c_lat, c_lon):
                    st.warning(
                        "⚠️ 클릭한 위치가 창원시 범위를 벗어난 것으로 보입니다. 확정해도 "
                        "조회는 진행되지만 창원시 5개 구 데이터만 포함되어 0건으로 나올 수 "
                        "있습니다."
                    )
                col_confirm, col_cancel = st.columns(2)
                with col_confirm:
                    if st.button("📍 이 위치에서 주변 시설 검색", type="primary", width="stretch"):
                        st.session_state.search_center = (c_lat, c_lon)
                        st.session_state.map_click_candidate = None
                        # map_click_seen은 그대로 둔다 - 초기화하면 컴포넌트가 계속
                        # 돌려주는 "같은" 마지막 클릭 좌표가 다음 실행에서 다시
                        # 새 클릭처럼 재처리돼 버린다(아래 "선택 취소"도 동일).
                        st.rerun()
                with col_cancel:
                    if st.button("선택 취소", width="stretch"):
                        st.session_state.map_click_candidate = None
                        st.rerun()
            else:
                st.caption("아직 클릭한 위치가 없습니다. 위 지도를 클릭해 주세요.")

    st.divider()
    st.subheader("4. 주변 버스정류장·편의점 수 (반경별 비교)")
    st.caption(
        "아래 표는 300m / 500m / 1km 반경 안의 전체 건수입니다. 실제로 0건인 경우와 "
        "데이터를 조회할 수 없는 경우('미확보'/'조회 실패')는 구분해서 표시됩니다."
    )
    _render_comparison_table(lat_input, lon_input)

    st.divider()
    st.subheader("5. 🤖 AI에게 주변 생활시설 분석 요청하기")
    st.caption(
        "자연어로 요청하면 Claude(claude CLI, 실패 시 로컬 Ollama)가 어떤 조회 도구를 쓸지 계획하고, "
        "Python이 그 계획을 검증한 뒤 실제 데이터를 조회합니다. 검색 중심 좌표는 항상 "
        "위에서 확정한 좌표만 사용되며, AI가 임의로 바꿀 수 없습니다."
    )
    agent_request_text = st.text_input(
        "분석 요청",
        key="location_agent_input",
        placeholder="예: 이 위치에서 500m 안에 버스정류장과 편의점이 얼마나 있어?",
        label_visibility="collapsed",
    )
    # 버튼을 눌렀을 때만 run_location_agent()(Ollama 호출 포함)를 실행한다 - 지도
    # 이동·레이어 토글·반경 변경 등 다른 재실행에서는 절대 호출되지 않는다.
    if st.button("🤖 AI 분석 실행", type="primary"):
        if not agent_request_text.strip():
            st.warning("분석 요청 문장을 입력해 주세요.")
        else:
            with st.spinner("AI가 분석 계획을 세우고 실제 데이터를 조회하는 중입니다..."):
                st.session_state.location_agent_result = run_location_agent(
                    user_text=agent_request_text,
                    # 승인된 확정 좌표만 넘긴다 - map_click_candidate(미확정 후보)는
                    # 여기 절대 쓰지 않는다(가장 중요한 요구사항).
                    search_center=st.session_state.search_center,
                    ui_radius_m=radius_choice,
                    ui_max_results=max_results,
                )

    if st.session_state.location_agent_result is not None:
        render_location_agent_result(st.session_state.location_agent_result)

    st.divider()
    st.subheader(f"6. 주변 시설 목록 (반경 {radius_choice}m 이내)" if radius_choice != 1000 else "6. 주변 시설 목록 (반경 1km 이내)")
    _render_nearby_list(bus_result, store_result)

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
            f"{check['quality_exception_count']}건이 포함되어 있습니다(주변 목록·지도 "
            "팝업에 '품질 주의'로 표시됩니다)."
        )
        if not check["ok"]:
            st.error(
                "⚠️ 현재 재계산된 집계가 기존 공식 집계와 다릅니다. 원본 데이터가 "
                "변경되었을 수 있으니 확인이 필요합니다."
            )
