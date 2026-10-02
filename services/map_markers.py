# 지도 마커 데이터 준비 (지도 라이브러리에 의존하지 않는 순수 함수)
"""
services/bus_stops.py의 find_nearby_bus_stops()와 services/convenience.py의
find_nearby_stores() 반환값을, 특정 지도 라이브러리(folium 등)와 무관한 평범한
dict 리스트(마커 데이터)로 변환한다.

pages/user.py가 이 데이터로 folium.Marker를 그릴 뿐, 여기서 좌표나 시설 정보를
새로 만들지 않는다 - 원본 서비스 함수가 이미 필터링·검증한 결과를 그대로 옮길
뿐이다(status != "ok"면 빈 리스트). folium을 import하지 않으므로 지도 라이브러리
설치 여부와 무관하게 이 모듈만 단독으로 가볍게 단위 테스트할 수 있다.

[시설 종류 구분]
    버스정류장과 편의점은 color/icon이 서로 다른 상수를 쓴다 - 지도에서 혼동되지
    않게 구분하기 위함이며, pages/user.py는 이 값을 그대로 folium.Icon에 넘긴다.

[표시 개수와 전체 개수]
    반환하는 마커 리스트 길이는 항상 입력받은 조회 결과의 "표시 목록"
    (stops/stores, 즉 displayed_count)과 같다 - 반경 안 "전체" 시설 수
    (total_count)와는 다를 수 있다. 호출 측(pages/user.py)이 이 둘을 섞어
    보여주지 않도록 주의해야 한다(화면에 total_count와 함께 캡션으로 안내).
"""

from __future__ import annotations

from services import bus_stops

SEARCH_CENTER_MARKER_COLOR = "red"
SEARCH_CENTER_MARKER_ICON = "star"
BUS_STOP_MARKER_COLOR = "blue"
BUS_STOP_MARKER_ICON = "bus"
CONVENIENCE_MARKER_COLOR = "green"
CONVENIENCE_MARKER_ICON = "shopping-cart"


def build_bus_stop_markers(bus_result: dict) -> list[dict]:
    """
    find_nearby_bus_stops() 반환값에서 지도에 찍을 마커 데이터만 뽑는다.

    Returns: [{"facility_type": "bus_stop", "id", "lat", "lon", "label",
               "popup_lines": [...], "color", "icon"}, ...]
    status != "ok"면 빈 리스트.
    """
    if bus_result.get("status") != "ok":
        return []
    markers = []
    for s in bus_result["stops"]:
        quality_label = bus_stops.QUALITY_FLAG_LABELS.get(s["quality_flag"]) if s.get("quality_flag") else None
        popup_lines = [s["stop_name"], f"소속: {s['district']}", f"직선거리: {s['straight_distance_m']}m"]
        if quality_label:
            popup_lines.append(f"⚠️ {quality_label}")
        markers.append(
            {
                "facility_type": "bus_stop",
                "id": s["stop_id"],
                "lat": s["lat"],
                "lon": s["lon"],
                "label": s["stop_name"],
                "popup_lines": popup_lines,
                "color": BUS_STOP_MARKER_COLOR,
                "icon": BUS_STOP_MARKER_ICON,
            }
        )
    return markers


def build_convenience_markers(store_result: dict) -> list[dict]:
    """
    find_nearby_stores() 반환값에서 지도에 찍을 마커 데이터만 뽑는다.

    Returns: [{"facility_type": "convenience_store", "id", "lat", "lon", "label",
               "popup_lines": [...], "color", "icon"}, ...]
    status != "ok"면 빈 리스트.
    """
    if store_result.get("status") != "ok":
        return []
    markers = []
    for s in store_result["stores"]:
        address = s.get("road_address") or s.get("jibun_address") or "주소 정보 없음"
        popup_lines = [s["facility_name"], address, f"직선거리: {s['straight_distance_m']}m"]
        markers.append(
            {
                "facility_type": "convenience_store",
                "id": f"{s['facility_name']}#{s['rank']}",
                "lat": s["lat"],
                "lon": s["lon"],
                "label": s["facility_name"],
                "popup_lines": popup_lines,
                "color": CONVENIENCE_MARKER_COLOR,
                "icon": CONVENIENCE_MARKER_ICON,
            }
        )
    return markers
