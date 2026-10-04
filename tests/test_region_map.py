"""
services/region_map.py - 추천 결과 지도용 데이터 테스트 (표시 전용, 새 계산 없음).

    python -m unittest tests.test_region_map -v
"""

import unittest

from analysis import scoring
from analysis.candidates import build_candidate_set
from services import region_map
from services.region_data import get_all_regions

EQUAL = {"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1}


def _result_and_candidates(region_type: str):
    result = scoring.compute_region_scores_from_weights(EQUAL, 3, regions=get_all_regions(region_type=region_type))
    return result, build_candidate_set(result, [])


class CandidateMapRegionsTest(unittest.TestCase):
    def test_every_compared_region_has_a_boundary_in_all_scopes(self):
        for region_type, count in (("구", 5), ("시", 7), ("군", 10)):
            result, candidates = _result_and_candidates(region_type)
            data = region_map.build_candidate_map_regions(result, candidates)
            self.assertEqual(len(data["regions"]), count, region_type)
            self.assertEqual(data["missing"], [], region_type)
            self.assertIsNotNone(data["bounds"])

    def test_scores_and_roles_are_copied_not_recomputed(self):
        result, candidates = _result_and_candidates("구")
        data = region_map.build_candidate_map_regions(result, candidates)
        by_id = {r["region_id"]: r for r in data["regions"]}
        for row in result["region_scores"]:
            self.assertEqual(by_id[row["region_id"]]["rank"], row["rank"])
            self.assertEqual(by_id[row["region_id"]]["total_score"], row["total_score"])
        for role in candidates["roles"]:
            if role["status"] == "ok":
                self.assertIn(role["role_label"], by_id[role["region_id"]]["roles"])

    def test_role_colors_and_draw_order(self):
        result, candidates = _result_and_candidates("구")
        data = region_map.build_candidate_map_regions(result, candidates)
        best_id = next(r["region_id"] for r in candidates["roles"] if r["role"] == "best")
        best = next(r for r in data["regions"] if r["region_id"] == best_id)
        self.assertEqual(best["primary_role"], "best")
        self.assertEqual(best["color"], region_map.ROLE_COLORS["best"])
        for region in data["regions"]:
            if not region["roles"]:
                self.assertIsNone(region["primary_role"])
                self.assertEqual(region["color"], region_map.OTHER_COLOR)
        # 후보 지역이 뒤에(위에) 그려진다
        flags = [r["primary_role"] is not None for r in data["regions"]]
        self.assertEqual(flags, sorted(flags))

    def test_region_with_several_roles_uses_best_first(self):
        result, candidates = _result_and_candidates("시")
        data = region_map.build_candidate_map_regions(result, candidates)
        label_order = ["최적", "균형", "대안", "가성비"]
        for region in data["regions"]:
            if len(region["roles"]) > 1:
                self.assertEqual(region["roles"], sorted(region["roles"], key=label_order.index))
                self.assertEqual(region["color"], region_map.ROLE_COLORS[region["primary_role"]])

    def test_label_point_is_inside_its_region(self):
        from shapely.geometry import Point, shape

        for region_type in ("구", "시", "군"):
            result, candidates = _result_and_candidates(region_type)
            for region in region_map.build_candidate_map_regions(result, candidates)["regions"]:
                lat, lon = region["label_point"]
                # 라벨은 원본 경계 안쪽 점이다. 단순화된 표시용 경계와는 수십 m 차이가 날 수 있어 약간 넓혀 확인한다.
                self.assertTrue(shape(region["geometry"]).buffer(0.003).contains(Point(lon, lat)), region["region_name"])

    def test_unknown_region_is_reported_missing_not_invented(self):
        result, candidates = _result_and_candidates("구")
        fake = dict(result["region_scores"][0], region_id="XX-NOWHERE", region_name="가상구")
        result = dict(result, region_scores=[*result["region_scores"], fake])
        data = region_map.build_candidate_map_regions(result, candidates)
        self.assertIn("가상구", data["missing"])
        self.assertNotIn("XX-NOWHERE", [r["region_id"] for r in data["regions"]])

    def test_without_candidates_all_regions_are_neutral(self):
        result, _ = _result_and_candidates("군")
        data = region_map.build_candidate_map_regions(result, None)
        self.assertTrue(all(r["roles"] == [] for r in data["regions"]))

    def test_boundary_base_date(self):
        self.assertRegex(region_map.boundary_base_date() or "", r"^\d{8}$")


class MapRefreshKeyTest(unittest.TestCase):
    """피드백으로 후보가 바뀌거나 관심 지역을 바꾸면 지도 키가 달라져 이전 지도가 남지 않는다."""

    def _map(self, weights):
        from analysis import scoring
        from analysis.candidates import build_candidate_set
        from services.region_data import get_all_regions
        result = scoring.compute_region_scores_from_weights(weights, 3, regions=get_all_regions(region_type="구"))
        return region_map.build_candidate_map_regions(result, build_candidate_set(result, []))

    def test_key_changes_with_candidates_and_focus_but_is_stable_otherwise(self):
        equal = self._map({"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1})
        medical = self._map({"bus_stop_count": 28.57, "hospital_count": 42.86, "convenience_store_count": 28.57})
        best = lambda m: next(r["region_name"] for r in m["regions"] if r["primary_role"] == "best")  # noqa: E731
        self.assertEqual((best(equal), best(medical)), ("마산합포구", "성산구"))  # 피드백으로 최적이 바뀌는 경우
        key = region_map.map_component_key
        self.assertNotEqual(key(equal, None, "구"), key(medical, None, "구"))
        self.assertNotEqual(key(equal, None, "구"), key(equal, "진해구", "구"))
        self.assertNotEqual(key(equal, None, "구"), key(equal, None, "시"))
        self.assertEqual(key(equal, None, "구"), key(self._map({"bus_stop_count": 1, "hospital_count": 1,
                                                               "convenience_store_count": 1}), None, "구"))


if __name__ == "__main__":
    unittest.main()
