"""
services/geo.py 단위 테스트 - 버스정류장·편의점 주변 조회가 같은 공용 정의를 쓰는지 확인.

    python -m unittest tests.test_geo -v
"""

import math
import unittest

import numpy as np

from services import bus_stops, convenience, geo


class GeoHelpersTest(unittest.TestCase):
    def test_to_float(self):
        self.assertEqual(geo.to_float("35.5"), 35.5)
        for bad in (None, "abc", True, float("nan"), float("inf"), "Infinity"):
            self.assertIsNone(geo.to_float(bad), bad)

    def test_to_int(self):
        self.assertEqual(geo.to_int("10"), 10)
        self.assertEqual(geo.to_int(3.0), 3)
        self.assertIsNone(geo.to_int(1.5))
        self.assertIsNone(geo.to_int(False))

    def test_haversine_one_degree_latitude(self):
        expected = math.radians(1) * geo.EARTH_RADIUS_M  # 약 111,195m
        d = geo.haversine_m(35.0, 128.0, np.array([36.0]), np.array([128.0]))[0]
        self.assertAlmostEqual(d, expected, places=3)

    def test_haversine_zero_distance(self):
        self.assertEqual(geo.haversine_m(35.2, 128.6, np.array([35.2]), np.array([128.6]))[0], 0.0)

    def test_changwon_bbox(self):
        self.assertTrue(geo.is_within_changwon_bbox(35.2280, 128.6811))
        self.assertFalse(geo.is_within_changwon_bbox(37.5665, 126.9780))  # 서울


class SharedDefinitionsTest(unittest.TestCase):
    """중복 정의를 없앤 뒤에도 두 서비스가 같은 객체를 쓰는지(값이 갈라지지 않는지)."""

    def test_same_functions_and_constants(self):
        for mod in (bus_stops, convenience):
            self.assertIs(mod._haversine_m, geo.haversine_m)
            self.assertIs(mod._to_float, geo.to_float)
            self.assertIs(mod._to_int, geo.to_int)
            self.assertIs(mod.DISTRICTS, geo.DISTRICTS)
            self.assertIs(mod._CHANGWON_BBOX, geo.CHANGWON_BBOX)
            self.assertEqual(mod.EARTH_RADIUS_M, 6_371_008.8)


if __name__ == "__main__":
    unittest.main()
