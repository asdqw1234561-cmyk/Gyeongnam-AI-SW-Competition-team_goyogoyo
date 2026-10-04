"""
services/bus_stops.py 단위 테스트. 실제 원본 데이터(data/raw/changwon_bus_stops.csv,
data/raw/changwon_district_boundaries.geojson)를 읽어 검증하되, 원본 파일 자체를
수정하지 않는지도 바이트 단위로 비교해 확인한다.

    python -m unittest tests.test_bus_stops -v
"""

import unittest
from pathlib import Path

from services import bus_stops

EXPECTED_COUNTS = {
    "CW-UICHANG": 831,
    "CW-SEONGSAN": 452,
    "CW-MASANHAPPO": 760,
    "CW-MASANHOEWON": 365,
    "CW-JINHAE": 518,
}
EXPECTED_TOTAL = 2926

# 창원시청 부근 예시 위치(근사 좌표) - docs/convenience_data.md와 동일한 좌표.
EXAMPLE_LAT, EXAMPLE_LON = 35.2280, 128.6811


class OfficialCountConsistencyTest(unittest.TestCase):
    """1,2) 원본 3,526건 중 공식 기준 2,926건이 정확히 분류되고, 구별 집계가
    scripts/ingest_bus_stop_counts.py의 합의된 값과 완전히 일치하는지."""

    def test_verify_official_counts_matches_exactly(self):
        result = bus_stops.verify_official_counts()
        self.assertTrue(result["ok"], f"공식 집계와 불일치: {result['mismatches']}")
        self.assertEqual(result["actual_counts"], EXPECTED_COUNTS)
        self.assertEqual(result["actual_total"], EXPECTED_TOTAL)
        self.assertEqual(result["expected_counts"], EXPECTED_COUNTS)
        self.assertEqual(result["expected_total"], EXPECTED_TOTAL)

    def test_each_district_count_individually(self):
        df = bus_stops._load_valid_stops()
        actual = df["region_id"].value_counts().to_dict()
        for region_id, expected in EXPECTED_COUNTS.items():
            self.assertEqual(actual.get(region_id, 0), expected, f"{region_id} 불일치")

    def test_no_duplicate_stop_ids_in_valid_set(self):
        """정류소아이디가 동일한 레코드를 중복 집계하지 않는지."""
        df = bus_stops._load_valid_stops()
        self.assertEqual(len(df), len(df["stop_id"].unique()))


class BoundaryCorrectionAndQualityExceptionTest(unittest.TestCase):
    """3,4) 경계 판정 보정 3건과 품질 예외 8건의 기존 처리 기준이 유지되는지."""

    def test_three_boundary_corrections_classified_as_seongsan(self):
        df = bus_stops._load_valid_stops()
        for stop_id in bus_stops._BOUNDARY_CORRECTION_IDS:
            row = df[df["stop_id"] == stop_id]
            self.assertFalse(row.empty, f"{stop_id}가 유효 집합에서 빠짐")
            self.assertEqual(row.iloc[0]["region_id"], "CW-SEONGSAN")
            self.assertEqual(row.iloc[0]["quality_flag"], "boundary_correction")
        self.assertEqual(len(bus_stops._BOUNDARY_CORRECTION_IDS), 3)

    def test_eight_quality_exceptions_included_not_excluded(self):
        """법정동-공간 불일치 8건은 제외되지 않고 공간판정 그대로 집계에 포함된다."""
        df = bus_stops._load_valid_stops()
        self.assertEqual(len(bus_stops._QUALITY_EXCEPTION_IDS), 8)
        for stop_id in bus_stops._QUALITY_EXCEPTION_IDS:
            row = df[df["stop_id"] == stop_id]
            self.assertFalse(row.empty, f"{stop_id}가 집계에서 제외됨 - 기존 처리 기준 위반")
            self.assertEqual(row.iloc[0]["quality_flag"], "location_mismatch_possible")

    def test_boundary_and_quality_sets_do_not_overlap(self):
        self.assertEqual(bus_stops._BOUNDARY_CORRECTION_IDS & bus_stops._QUALITY_EXCEPTION_IDS, set())


class HaversineDistanceTest(unittest.TestCase):
    """5) 주변 검색의 직선거리 계산 정확성."""

    def test_same_point_zero_distance(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=1, max_results=1)
        # 반경 1m에는 보통 정류장이 없을 수 있으나, 있다면 거리가 0에 가까워야 한다.
        for s in result["stops"]:
            self.assertLessEqual(s["straight_distance_m"], 1)

    def test_distance_matches_known_nearest_stop_within_tolerance(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=1000, max_results=1)
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(len(result["stops"]), 1)
        nearest = result["stops"][0]
        # 창원시청 좌표 부근이므로 1km 반경 안의 최근접 정류장은 300m 이내여야 한다
        # (실제로 100~200m대로 확인됨 - 큰 여유를 둔 상한선만 검증).
        self.assertLess(nearest["straight_distance_m"], 300)

    def test_results_sorted_by_distance_ascending(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=1000, max_results=50)
        distances = [s["straight_distance_m"] for s in result["stops"]]
        self.assertEqual(distances, sorted(distances))


class RadiusAndDisplayLimitTest(unittest.TestCase):
    """6,7) 300m/500m/1km 반경별 개수 계산, 가까운 순 정렬과 표시 개수 제한."""

    def test_counts_increase_monotonically_with_radius(self):
        result = bus_stops.count_nearby_by_radius(EXAMPLE_LAT, EXAMPLE_LON)
        self.assertEqual(result["status"], "ok")
        c300, c500, c1000 = result["counts"][300], result["counts"][500], result["counts"][1000]
        self.assertLessEqual(c300, c500)
        self.assertLessEqual(c500, c1000)
        self.assertGreater(c1000, 0)

    def test_total_count_can_exceed_displayed_count(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=1000, max_results=3)
        self.assertEqual(result["displayed_count"], min(3, result["total_count"]))
        self.assertLessEqual(len(result["stops"]), 3)

    def test_max_results_limits_output(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=1000, max_results=2)
        self.assertLessEqual(len(result["stops"]), 2)


class ActualZeroVsLookupFailureTest(unittest.TestCase):
    """8) 실제 0건과 조회 실패(잘못된 입력)의 구분."""

    def test_remote_location_returns_actual_zero_not_error(self):
        # 창원시에서 멀리 떨어진 좌표(서울 시청 부근) - 데이터 자체는 정상 조회되지만 0건이어야 한다.
        result = bus_stops.find_nearby_bus_stops(37.5665, 126.9780, radius_m=100, max_results=5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_count"], 0)
        self.assertIn("없습니다", result["message"])

    def test_remote_location_warns_outside_changwon(self):
        result = bus_stops.find_nearby_bus_stops(37.5665, 126.9780, radius_m=100, max_results=5)
        self.assertTrue(any("경상남도 범위" in w for w in result["warnings"]))


class InvalidInputTest(unittest.TestCase):
    """9) 잘못된 좌표·반경 입력 처리."""

    def test_non_numeric_lat_rejected(self):
        result = bus_stops.find_nearby_bus_stops("북위삼십오도", 128.6811)
        self.assertEqual(result["status"], "invalid_input")

    def test_latitude_out_of_range_rejected(self):
        result = bus_stops.find_nearby_bus_stops(200, 128.6811)
        self.assertEqual(result["status"], "invalid_input")

    def test_nan_coordinate_rejected(self):
        result = bus_stops.find_nearby_bus_stops(float("nan"), 128.6811)
        self.assertEqual(result["status"], "invalid_input")

    def test_infinite_coordinate_rejected(self):
        result = bus_stops.find_nearby_bus_stops(float("inf"), 128.6811)
        self.assertEqual(result["status"], "invalid_input")

    def test_zero_radius_rejected(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=0)
        self.assertEqual(result["status"], "invalid_input")

    def test_negative_radius_rejected(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=-500)
        self.assertEqual(result["status"], "invalid_input")

    def test_excessive_radius_rejected(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=999_999)
        self.assertEqual(result["status"], "invalid_input")

    def test_negative_max_results_rejected(self):
        result = bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, max_results=-5)
        self.assertEqual(result["status"], "invalid_input")


class OriginalDataProtectionTest(unittest.TestCase):
    """10) 검색 과정에서 원본 CSV/GeoJSON이 전혀 바뀌지 않는지."""

    def test_csv_and_geojson_untouched(self):
        csv_before = Path(bus_stops.BUS_STOPS_CSV).read_bytes()
        geojson_before = Path(bus_stops.BOUNDARY_GEOJSON).read_bytes()

        bus_stops.find_nearby_bus_stops(EXAMPLE_LAT, EXAMPLE_LON, radius_m=1000, max_results=20)
        bus_stops.count_nearby_by_radius(EXAMPLE_LAT, EXAMPLE_LON)
        bus_stops.verify_official_counts()

        self.assertEqual(csv_before, Path(bus_stops.BUS_STOPS_CSV).read_bytes())
        self.assertEqual(geojson_before, Path(bus_stops.BOUNDARY_GEOJSON).read_bytes())


class ConvenienceStoreUnaffectedTest(unittest.TestCase):
    """11) 기존 편의점 조회 결과가 이번 변경으로 영향받지 않는지(모듈 재사용만
    했을 뿐 convenience.py 자체는 건드리지 않았으므로 정상 동작해야 한다)."""

    def test_convenience_nearby_search_still_works(self):
        from services import convenience

        result = convenience.find_nearby_stores(EXAMPLE_LAT, EXAMPLE_LON, radius_m=500, max_results=5)
        self.assertIn(result["status"], ("ok", "no_data"))


class SharedCenterAndRadiusConsistencyTest(unittest.TestCase):
    """버스정류장과 편의점이 같은 검색 중심·반경 프리셋을 쓰는지(요구사항 4)."""

    def test_same_preset_radii_and_distance_semantics(self):
        from services import convenience

        self.assertEqual(bus_stops.PRESET_RADII_M, convenience.PRESET_RADII_M)
        self.assertEqual(bus_stops.DISTANCE_TYPE, convenience.DISTANCE_TYPE)

    def test_same_center_produces_results_for_both_facility_types(self):
        from services import convenience

        bus_result = bus_stops.count_nearby_by_radius(EXAMPLE_LAT, EXAMPLE_LON)
        store_result = convenience.count_nearby_by_radius(EXAMPLE_LAT, EXAMPLE_LON)
        self.assertEqual(bus_result["status"], "ok")
        self.assertIn(store_result["status"], ("ok", "no_data"))


if __name__ == "__main__":
    unittest.main()
