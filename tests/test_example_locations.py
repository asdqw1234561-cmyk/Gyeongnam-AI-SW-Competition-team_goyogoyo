"""
위치 탐색 예시 위치(services/example_locations.py) - 경남 22개 지역 시·군·구청 부근 대표 근사 좌표.
범위별 개수·중복/누락·기존 좌표 재사용·좌표가 해당 지역 경계 안인지 확인한다(API·LLM 호출 없음).
"""

import os
import sys
import unittest

from services.example_locations import (
    DEFAULT_SCOPE, EXAMPLE_LOCATIONS, EXAMPLE_SCOPES, default_location, locations_in_scope,
)
from services.region_data import REGION_SCOPE_DISPLAY, get_all_regions

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


class ExampleLocationsTest(unittest.TestCase):
    def test_scope_counts_5_7_10(self):
        self.assertEqual({s: len(locations_in_scope(s)) for s in EXAMPLE_SCOPES},
                         {"경남 구 지역 (5곳)": 5, "경남 시 지역 (7곳)": 7, "경남 군 지역 (10곳)": 10})
        self.assertEqual(locations_in_scope("없는 범위"), [])

    def test_scope_names_match_comparison_scope_display(self):
        self.assertEqual(list(EXAMPLE_SCOPES), list(REGION_SCOPE_DISPLAY.values()))

    def test_22_regions_without_duplicates_or_missing(self):
        ids = [loc["region_id"] for loc in EXAMPLE_LOCATIONS]
        self.assertEqual(len(ids), 22)
        self.assertEqual(len(set(ids)), 22)
        regions = {r["region_id"]: r for r in get_all_regions()}
        self.assertEqual(set(ids), set(regions))
        for loc in EXAMPLE_LOCATIONS:
            self.assertEqual(loc["region_type"], regions[loc["region_id"]]["region_type"], loc["region_id"])
            self.assertTrue(loc["label"].endswith("청 부근 예시 위치 (근사 좌표)"), loc["label"])

    def test_existing_verified_coords_are_reused(self):
        coords = {loc["region_name"]: loc["coord"] for loc in EXAMPLE_LOCATIONS}
        self.assertEqual(coords["김해시"], (35.2285, 128.8894))
        self.assertEqual(coords["진주시"], (35.1800, 128.1076))

    def test_every_coord_is_inside_its_region_boundary(self):
        """SGIS 경계(data/raw/gyeongnam_boundaries.geojson)와 기존 판정 함수로 확인 - 판정 로직은 그대로 재사용."""
        from shapely.strtree import STRtree
        import ingest_gyeongnam_bus_stops as gn_stops

        polygons, ids = gn_stops.load_polygons()
        tree = STRtree(polygons)
        for loc in EXAMPLE_LOCATIONS:
            region_id, how = gn_stops.classify(*loc["coord"], polygons, ids, tree)
            self.assertEqual((region_id, how), (loc["region_id"], "contains"), loc["label"])

    def test_default_is_first_region_of_default_scope(self):
        self.assertEqual(DEFAULT_SCOPE, "경남 구 지역 (5곳)")
        self.assertEqual(default_location()["region_name"], "의창구")


if __name__ == "__main__":
    unittest.main()
