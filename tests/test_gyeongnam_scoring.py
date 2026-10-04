"""
경남 확장 B단계: 같은 유형끼리 비교(구 지역 / 시 지역 / 군 지역)와 인구 1만 명당 점수 변환.
실제 data/region_indicators.csv를 읽는다(API·LLM 호출 없음).
"""

import unittest

from analysis import feedback, scoring, simulation
from analysis.candidates import strength_max_rank, weakness_min_rank
from analysis.explanation_facts import judge
from services.region_data import REGION_SCOPE_DISPLAY, REGION_SCOPES, get_all_regions, region_type_for, scope_display
from tests import changwon_fixture

EQUAL = {"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1}


class RegionScopeTest(unittest.TestCase):
    def test_three_scopes_split_22_regions_by_type(self):
        sizes = {label: len(get_all_regions(region_type=t)) for label, t in REGION_SCOPES.items()}
        self.assertEqual(sizes, {"창원시 5개 구": 5, "경남 시 지역 (7곳)": 7, "경남 군 지역 (10곳)": 10})
        self.assertEqual(len(get_all_regions()), 22)

    def test_scope_text_to_region_type_with_backward_compatible_inputs(self):
        self.assertEqual(region_type_for("경남 군 지역 (10곳)"), "군")
        self.assertEqual(region_type_for("창원시 의창구"), "구")  # 예전 희망지역 입력
        self.assertEqual(region_type_for("창원시 전체"), "구")
        self.assertEqual(region_type_for(""), "구")

    def test_scope_display_names_keep_internal_values(self):
        """내부 값은 그대로, 화면 표시 이름만 '경남 구 지역 (5곳)'. 표시 이름으로도 같은 유형을 찾는다."""
        self.assertEqual(list(REGION_SCOPES), ["창원시 5개 구", "경남 시 지역 (7곳)", "경남 군 지역 (10곳)"])
        self.assertEqual([scope_display(k) for k in REGION_SCOPES],
                         ["경남 구 지역 (5곳)", "경남 시 지역 (7곳)", "경남 군 지역 (10곳)"])
        self.assertEqual(set(REGION_SCOPE_DISPLAY), set(REGION_SCOPES))
        self.assertEqual(scope_display(""), "경남 구 지역 (5곳)")
        self.assertEqual(scope_display("창원시 의창구"), "창원시 의창구")  # 모르는 값은 그대로
        for label, region_type in REGION_SCOPES.items():
            self.assertEqual(region_type_for(scope_display(label)), region_type)

    def test_default_scoring_never_mixes_types(self):
        result = scoring.compute_region_scores_from_weights(EQUAL, 3)
        self.assertEqual({r["region_id"][:3] for r in result["region_scores"]}, {"CW-"})
        self.assertEqual(len(result["region_scores"]), 5)


class PerCapitaTest(unittest.TestCase):
    def test_per_10k_is_count_divided_by_population(self):
        regions = get_all_regions(region_type="군")
        result = scoring.compute_region_scores_from_weights(EQUAL, 3, regions=regions)
        self.assertEqual(result["basis"], scoring.BASIS_PER_CAPITA)
        for row in result["region_scores"]:
            for comp in row["component_scores"].values():
                self.assertAlmostEqual(comp["per_10k"], comp["raw_value"] / row["population"] * 10_000)
        normalized = [r["component_scores"]["hospital_count"]["normalized_score"] for r in result["region_scores"]]
        self.assertEqual((min(normalized), max(normalized)), (0.0, 100.0))  # min-max는 그대로

    def test_without_population_falls_back_to_counts(self):
        result = scoring.compute_region_scores_from_weights(EQUAL, 3, regions=changwon_fixture.changwon_count_regions())
        self.assertEqual(result["basis"], scoring.BASIS_COUNT)
        self.assertIsNone(result["region_scores"][0]["component_scores"]["bus_stop_count"]["per_10k"])

    def test_feedback_and_simulation_stay_in_the_same_scope(self):
        regions = get_all_regions(region_type="시")
        out = feedback.reevaluate(EQUAL, 3, [], regions=regions)
        self.assertEqual(len(out["score_result"]["region_scores"]), 7)
        sim = simulation.simulate_facility_change("GN-GIMHAE", "hospital_count", 10, EQUAL)
        self.assertEqual(sim["status"], "ok")
        self.assertEqual(len(sim["score_change"]), 7)


class RankThresholdTest(unittest.TestCase):
    def test_five_regions_keep_previous_thresholds(self):
        self.assertEqual((strength_max_rank(5), weakness_min_rank(5)), (2, 4))
        self.assertEqual([judge(k) for k in range(1, 6)], ["강점", "강점", "중립", "약점", "약점"])

    def test_thresholds_scale_with_region_count(self):
        self.assertEqual((strength_max_rank(7), weakness_min_rank(7)), (3, 5))
        self.assertEqual((strength_max_rank(10), weakness_min_rank(10)), (4, 7))
        self.assertEqual(judge(4, 10), "강점")
        self.assertEqual(judge(5, 10), "중립")
        self.assertEqual(judge(7, 10), "약점")


if __name__ == "__main__":
    unittest.main()
