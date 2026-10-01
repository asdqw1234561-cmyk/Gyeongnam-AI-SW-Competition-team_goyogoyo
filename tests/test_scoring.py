"""
analysis/scoring.py 단위 테스트 (실제 region_indicators.csv를 읽어 검증, API 호출 없음).

    python -m unittest tests.test_scoring -v
"""

import unittest
from unittest import mock

from analysis import scoring


class ScoringAgainstRealDataTest(unittest.TestCase):
    """현재 커밋된 data/region_indicators.csv(확보 15행) 기준 동작 검증."""

    def test_no_selection_uses_all_three_confirmed_indicators_equally(self):
        result = scoring.compute_region_scores(user_conditions=[], candidate_count=3)
        self.assertEqual(result["status"], "ok")
        used_codes = {uc["indicator_code"] for uc in result["used_conditions"]}
        self.assertEqual(used_codes, {"bus_stop_count", "hospital_count", "convenience_store_count"})
        for uc in result["used_conditions"]:
            self.assertAlmostEqual(uc["weight"], 1 / 3)
        self.assertEqual(result["excluded_conditions"], [])

    def test_medical_only_scores_with_hospital_indicator_only(self):
        result = scoring.compute_region_scores(user_conditions=["의료"], candidate_count=5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["used_conditions"]), 1)
        self.assertEqual(result["used_conditions"][0]["indicator_code"], "hospital_count")
        self.assertEqual(result["used_conditions"][0]["weight"], 1.0)
        for row in result["region_scores"]:
            self.assertEqual(set(row["component_scores"]), {"hospital_count"})

    def test_transport_and_medical_get_equal_weight(self):
        result = scoring.compute_region_scores(user_conditions=["교통", "의료"], candidate_count=5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["used_conditions"]), 2)
        for uc in result["used_conditions"]:
            self.assertAlmostEqual(uc["weight"], 0.5)

    def test_education_only_returns_no_usable_conditions_without_guessing(self):
        result = scoring.compute_region_scores(user_conditions=["교육"], candidate_count=3)
        self.assertEqual(result["status"], "no_usable_conditions")
        self.assertEqual(result["message"], scoring.NO_DATA_MESSAGE)
        self.assertEqual(result["used_conditions"], [])
        self.assertEqual(len(result["excluded_conditions"]), 1)
        self.assertEqual(result["excluded_conditions"][0]["condition"], "교육")

    def test_transport_plus_education_excludes_education_explicitly(self):
        result = scoring.compute_region_scores(user_conditions=["교통", "교육"], candidate_count=5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["used_conditions"]), 1)
        self.assertEqual(result["used_conditions"][0]["indicator_code"], "bus_stop_count")
        excluded = {e["condition"] for e in result["excluded_conditions"]}
        self.assertIn("교육", excluded)

    def test_unconfirmed_condition_never_silently_scored_as_zero(self):
        """안전(미확보)만 고르면 0점짜리 '안전' 항목이 섞여 들어가는 게 아니라
        아예 계산에서 빠져야 한다 - component_scores에 키 자체가 없어야 함."""
        result = scoring.compute_region_scores(user_conditions=["교통", "안전"], candidate_count=5)
        self.assertEqual(result["status"], "ok")
        for row in result["region_scores"]:
            self.assertNotIn("안전", row["component_scores"])
            self.assertEqual(set(row["component_scores"]), {"bus_stop_count"})

    def test_all_five_regions_always_computed_before_slicing(self):
        result = scoring.compute_region_scores(user_conditions=["교통"], candidate_count=1)
        self.assertEqual(len(result["region_scores"]), 5)
        self.assertEqual(len(result["top_candidates"]), 1)
        self.assertEqual(result["top_candidates"][0], result["region_scores"][0])

    def test_rank_and_total_score_consistency(self):
        result = scoring.compute_region_scores(user_conditions=[], candidate_count=5)
        totals = [row["total_score"] for row in result["region_scores"]]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertEqual([row["rank"] for row in result["region_scores"]], [1, 2, 3, 4, 5])


class MinMaxNormalizationTest(unittest.TestCase):
    def test_normal_case_min_max_0_to_100(self):
        scores, formula = scoring._normalize_min_max({"a": 10, "b": 20, "c": 30})
        self.assertEqual(scores, {"a": 0.0, "b": 50.0, "c": 100.0})
        self.assertIn("10", formula)
        self.assertIn("30", formula)

    def test_all_equal_values_gives_50_without_division_by_zero(self):
        scores, formula = scoring._normalize_min_max({"a": 7, "b": 7, "c": 7, "d": 7, "e": 7})
        self.assertEqual(scores, {"a": 50.0, "b": 50.0, "c": 50.0, "d": 50.0, "e": 50.0})
        self.assertIn("동일", formula)


class AllValuesEqualEndToEndTest(unittest.TestCase):
    """5개 구 지표 값이 전부 동일해도 compute_region_scores()가 에러 없이 동작하는지."""

    FAKE_REGIONS = [
        {
            "region_id": rid,
            "region_name": name,
            "categories": {
                "교통": [
                    {"indicator_code": "bus_stop_count", "indicator_name": "버스정류장 수",
                     "value": "100", "data_status": "확보"},
                ],
                "의료": [
                    {"indicator_code": "hospital_count", "indicator_name": "병원 수",
                     "value": "50", "data_status": "확보"},
                ],
                "생활편의": [
                    {"indicator_code": "convenience_store_count", "indicator_name": "편의점 수",
                     "value": "30", "data_status": "확보"},
                ],
            },
        }
        for rid, name in [
            ("CW-UICHANG", "의창구"), ("CW-SEONGSAN", "성산구"), ("CW-MASANHAPPO", "마산합포구"),
            ("CW-MASANHOEWON", "마산회원구"), ("CW-JINHAE", "진해구"),
        ]
    ]

    @mock.patch("analysis.scoring.get_all_changwon_regions")
    def test_tied_scores_marked_and_no_crash(self, mock_get_regions):
        mock_get_regions.return_value = self.FAKE_REGIONS
        result = scoring.compute_region_scores(user_conditions=[], candidate_count=5)
        self.assertEqual(result["status"], "ok")
        for row in result["region_scores"]:
            self.assertAlmostEqual(row["total_score"], 50.0)
            self.assertTrue(row["tied"])


class FeedbackWeightAdjustmentTest(unittest.TestCase):
    """analysis/scoring.py의 피드백(가중치 직접 조정) 경로 전용 테스트."""

    def test_initial_feedback_weights_mirrors_first_recommendation(self):
        first = scoring.compute_region_scores(user_conditions=["의료", "교통"], candidate_count=5)
        weights = scoring.initial_feedback_weights(first)
        self.assertAlmostEqual(weights["hospital_count"], 50.0)
        self.assertAlmostEqual(weights["bus_stop_count"], 50.0)
        self.assertAlmostEqual(weights["convenience_store_count"], 0.0)

    def test_initial_feedback_weights_for_single_condition(self):
        first = scoring.compute_region_scores(user_conditions=["의료"], candidate_count=5)
        weights = scoring.initial_feedback_weights(first)
        self.assertAlmostEqual(weights["hospital_count"], 100.0)
        self.assertAlmostEqual(weights["bus_stop_count"], 0.0)
        self.assertAlmostEqual(weights["convenience_store_count"], 0.0)

    def test_80_20_recomputes_and_differs_from_50_50(self):
        fifty_fifty = scoring.compute_region_scores_from_weights(
            {"hospital_count": 50, "bus_stop_count": 50}, candidate_count=5
        )
        eighty_twenty = scoring.compute_region_scores_from_weights(
            {"hospital_count": 80, "bus_stop_count": 20}, candidate_count=5
        )
        self.assertEqual(fifty_fifty["status"], "ok")
        self.assertEqual(eighty_twenty["status"], "ok")

        fifty_fifty_scores = {r["region_id"]: r["total_score"] for r in fifty_fifty["region_scores"]}
        eighty_twenty_scores = {r["region_id"]: r["total_score"] for r in eighty_twenty["region_scores"]}
        self.assertNotEqual(fifty_fifty_scores, eighty_twenty_scores)

        hospital_weight = next(
            uc["weight"] for uc in eighty_twenty["used_conditions"] if uc["indicator_code"] == "hospital_count"
        )
        self.assertAlmostEqual(hospital_weight, 0.8)

    def test_raw_facility_values_unchanged_by_weight_adjustment(self):
        """가중치를 바꿔도 의료·교통·편의점 원본 수치(reference_indicators)는 그대로여야 한다."""
        a = scoring.compute_region_scores_from_weights({"hospital_count": 100}, candidate_count=5)
        b = scoring.compute_region_scores_from_weights({"bus_stop_count": 100}, candidate_count=5)
        a_raw = {r["region_id"]: r["reference_indicators"]["hospital_count"]["raw_value"] for r in a["region_scores"]}
        b_raw = {r["region_id"]: r["reference_indicators"]["hospital_count"]["raw_value"] for r in b["region_scores"]}
        self.assertEqual(a_raw, b_raw)

    def test_all_weights_zero_does_not_compute(self):
        result = scoring.compute_region_scores_from_weights(
            {"hospital_count": 0, "bus_stop_count": 0, "convenience_store_count": 0}, candidate_count=3
        )
        self.assertEqual(result["status"], "no_usable_conditions")
        self.assertEqual(result["message"], scoring.ALL_WEIGHTS_ZERO_MESSAGE)

    def test_empty_weights_dict_does_not_compute(self):
        result = scoring.compute_region_scores_from_weights({}, candidate_count=3)
        self.assertEqual(result["status"], "no_usable_conditions")

    def test_unnormalized_weight_sum_is_normalized_to_100_percent(self):
        """60/60 합계 120 -> 내부적으로 50%/50%로 정규화되어야 한다."""
        skewed = scoring.compute_region_scores_from_weights(
            {"hospital_count": 60, "bus_stop_count": 60}, candidate_count=5
        )
        for uc in skewed["used_conditions"]:
            self.assertAlmostEqual(uc["weight"], 0.5)

    def test_weight_based_uses_same_normalization_as_condition_based(self):
        """같은 유효 가중치라면 compute_region_scores와 compute_region_scores_from_weights의
        정규화 결과(raw_values/normalized)가 완전히 같아야 한다(같은 내부 함수 재사용 확인)."""
        condition_based = scoring.compute_region_scores(user_conditions=["의료"], candidate_count=5)
        weight_based = scoring.compute_region_scores_from_weights({"hospital_count": 100}, candidate_count=5)
        self.assertEqual(
            condition_based["normalization"]["hospital_count"],
            weight_based["normalization"]["hospital_count"],
        )


if __name__ == "__main__":
    unittest.main()
