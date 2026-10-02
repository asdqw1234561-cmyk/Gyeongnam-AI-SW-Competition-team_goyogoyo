"""
services/map_markers.py 단위 테스트. folium을 import하지 않는 순수 함수라서
지도 라이브러리 설치 여부와 무관하게 가볍게 검증할 수 있다.

    python -m unittest tests.test_map_markers -v
"""

import unittest

from services import map_markers


def _fake_bus_result(stops):
    return {
        "status": "ok",
        "total_count": len(stops) + 5,  # displayed_count보다 total_count가 더 큰 상황을 흉내
        "displayed_count": len(stops),
        "stops": stops,
    }


def _fake_store_result(stores):
    return {
        "status": "ok",
        "total_count": len(stores) + 3,
        "displayed_count": len(stores),
        "stores": stores,
    }


class BuildBusStopMarkersTest(unittest.TestCase):
    """1,2,3,4,6) 마커 데이터 준비, 위도·경도 보존, 반경 필터는 입력 결과를
    그대로 따름, 시설 종류 구분, 표시 개수와 전체 개수 구분."""

    def test_non_ok_status_returns_empty_list(self):
        self.assertEqual(map_markers.build_bus_stop_markers({"status": "no_data"}), [])
        self.assertEqual(map_markers.build_bus_stop_markers({"status": "invalid_input"}), [])

    def test_lat_lon_not_swapped(self):
        stops = [
            {
                "rank": 1, "stop_id": "S1", "stop_name": "테스트정류장", "district": "성산구",
                "region_id": "CW-SEONGSAN", "lat": 35.111, "lon": 128.222,
                "straight_distance_m": 42, "quality_flag": None,
            }
        ]
        markers = map_markers.build_bus_stop_markers(_fake_bus_result(stops))
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["lat"], 35.111)
        self.assertEqual(markers[0]["lon"], 128.222)
        self.assertNotEqual(markers[0]["lat"], 128.222)  # 뒤바뀌지 않았는지 명시적으로 확인

    def test_marker_count_matches_displayed_not_total(self):
        """요구사항 6: 표시 마커 개수 제한과 전체 시설 수를 구분한다 - 마커 개수는
        stops 리스트 길이(displayed_count)와 같아야 하고, total_count와는 다를 수 있다."""
        stops = [
            {
                "rank": i, "stop_id": f"S{i}", "stop_name": f"정류장{i}", "district": "의창구",
                "region_id": "CW-UICHANG", "lat": 35.0 + i * 0.001, "lon": 128.0,
                "straight_distance_m": i * 10, "quality_flag": None,
            }
            for i in range(1, 4)
        ]
        result = _fake_bus_result(stops)
        markers = map_markers.build_bus_stop_markers(result)
        self.assertEqual(len(markers), result["displayed_count"])
        self.assertNotEqual(len(markers), result["total_count"])

    def test_quality_flag_included_in_popup_when_present(self):
        stops = [
            {
                "rank": 1, "stop_id": "S1", "stop_name": "품질예외정류장", "district": "마산합포구",
                "region_id": "CW-MASANHAPPO", "lat": 35.2, "lon": 128.5,
                "straight_distance_m": 10, "quality_flag": "location_mismatch_possible",
            }
        ]
        markers = map_markers.build_bus_stop_markers(_fake_bus_result(stops))
        popup_text = " ".join(markers[0]["popup_lines"])
        self.assertIn("위치정보 불일치", popup_text)

    def test_no_quality_flag_means_no_warning_line(self):
        stops = [
            {
                "rank": 1, "stop_id": "S1", "stop_name": "정상정류장", "district": "진해구",
                "region_id": "CW-JINHAE", "lat": 35.1, "lon": 128.7,
                "straight_distance_m": 5, "quality_flag": None,
            }
        ]
        markers = map_markers.build_bus_stop_markers(_fake_bus_result(stops))
        self.assertEqual(len(markers[0]["popup_lines"]), 3)

    def test_facility_type_and_style_tagged(self):
        stops = [
            {
                "rank": 1, "stop_id": "S1", "stop_name": "정류장", "district": "성산구",
                "region_id": "CW-SEONGSAN", "lat": 35.2, "lon": 128.6,
                "straight_distance_m": 1, "quality_flag": None,
            }
        ]
        markers = map_markers.build_bus_stop_markers(_fake_bus_result(stops))
        self.assertEqual(markers[0]["facility_type"], "bus_stop")
        self.assertEqual(markers[0]["color"], map_markers.BUS_STOP_MARKER_COLOR)
        self.assertEqual(markers[0]["icon"], map_markers.BUS_STOP_MARKER_ICON)


class BuildConvenienceMarkersTest(unittest.TestCase):
    def test_non_ok_status_returns_empty_list(self):
        self.assertEqual(map_markers.build_convenience_markers({"status": "no_data"}), [])

    def test_lat_lon_not_swapped(self):
        stores = [
            {
                "rank": 1, "facility_name": "테스트편의점", "branch_name": "", "district": "성산구",
                "road_address": "도로명주소", "jibun_address": "지번주소",
                "lat": 35.333, "lon": 128.444, "straight_distance_m": 7,
            }
        ]
        markers = map_markers.build_convenience_markers(_fake_store_result(stores))
        self.assertEqual(markers[0]["lat"], 35.333)
        self.assertEqual(markers[0]["lon"], 128.444)

    def test_marker_count_matches_displayed_not_total(self):
        stores = [
            {
                "rank": i, "facility_name": f"편의점{i}", "branch_name": "", "district": "의창구",
                "road_address": "", "jibun_address": "", "lat": 35.0, "lon": 128.0,
                "straight_distance_m": i,
            }
            for i in range(1, 3)
        ]
        result = _fake_store_result(stores)
        markers = map_markers.build_convenience_markers(result)
        self.assertEqual(len(markers), result["displayed_count"])
        self.assertNotEqual(len(markers), result["total_count"])

    def test_facility_type_and_style_tagged(self):
        stores = [
            {
                "rank": 1, "facility_name": "편의점", "branch_name": "", "district": "성산구",
                "road_address": "", "jibun_address": "", "lat": 35.2, "lon": 128.6,
                "straight_distance_m": 1,
            }
        ]
        markers = map_markers.build_convenience_markers(_fake_store_result(stores))
        self.assertEqual(markers[0]["facility_type"], "convenience_store")
        self.assertEqual(markers[0]["color"], map_markers.CONVENIENCE_MARKER_COLOR)
        self.assertEqual(markers[0]["icon"], map_markers.CONVENIENCE_MARKER_ICON)


class BusAndConvenienceMarkersDistinguishableTest(unittest.TestCase):
    """4) 시설 종류별 마커가 (색상/아이콘으로) 구분되는지."""

    def test_bus_and_convenience_use_different_color_and_icon(self):
        self.assertNotEqual(map_markers.BUS_STOP_MARKER_COLOR, map_markers.CONVENIENCE_MARKER_COLOR)
        self.assertNotEqual(map_markers.BUS_STOP_MARKER_ICON, map_markers.CONVENIENCE_MARKER_ICON)
        self.assertNotEqual(map_markers.BUS_STOP_MARKER_COLOR, map_markers.SEARCH_CENTER_MARKER_COLOR)


class MapMarkersMatchRealServiceDataTest(unittest.TestCase):
    """5) 지도에 표시된 시설과 목록의 시설이 일치하는지 - 실제 서비스 함수 결과를
    그대로 넣었을 때 마커 id가 원본 stop_id/상호명과 정확히 일치하는지."""

    def test_markers_ids_match_source_stop_ids(self):
        from services import bus_stops

        result = bus_stops.find_nearby_bus_stops(35.2280, 128.6811, radius_m=500, max_results=5)
        markers = map_markers.build_bus_stop_markers(result)
        self.assertEqual([m["id"] for m in markers], [s["stop_id"] for s in result["stops"]])
        self.assertEqual(len(markers), result["displayed_count"])

    def test_markers_match_real_convenience_data(self):
        from services import convenience

        result = convenience.find_nearby_stores(35.2280, 128.6811, radius_m=500, max_results=5)
        markers = map_markers.build_convenience_markers(result)
        self.assertEqual(len(markers), result["displayed_count"])
        for marker, store in zip(markers, result["stores"]):
            self.assertEqual(marker["lat"], store["lat"])
            self.assertEqual(marker["lon"], store["lon"])


if __name__ == "__main__":
    unittest.main()
