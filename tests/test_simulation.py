"""
analysis/simulation.py의 simulate_facility_change() 단위 테스트.
실제 data/region_indicators.csv를 읽어 검증하되, CSV 자체를 수정하지 않는지도
파일 내용을 직접 비교해서 확인한다.

    python -m unittest tests.test_simulation -v
"""

import copy
import unittest
from pathlib import Path
from unittest import mock

from analysis import simulation
from services.region_data import INDICATORS_CSV, get_all_changwon_regions

ALL_REGION_IDS = ["CW-UICHANG", "CW-SEONGSAN", "CW-MASANHAPPO", "CW-MASANHOEWON", "CW-JINHAE"]


class BasicExecutionTest(unittest.TestCase):
    """1,2,3) 5개 구 전부, 지표 3종 전부, +10/-10/0 변화가 정상 실행되는지."""

    def test_runs_for_all_five_districts(self):
        for region_id in ALL_REGION_IDS:
            result = simulation.simulate_facility_change(region_id, "hospital_count", 10)
            self.assertEqual(result["status"], "ok", f"{region_id} 실행 실패: {result}")
            self.assertEqual(result["region_id"], region_id)

    def test_runs_for_all_three_confirmed_indicators(self):
        for code in ["bus_stop_count", "hospital_count", "convenience_store_count"]:
            result = simulation.simulate_facility_change("CW-SEONGSAN", code, 5)
            self.assertEqual(result["status"], "ok", f"{code} 실행 실패: {result}")
            self.assertEqual(result["indicator_code"], code)

    def test_positive_negative_and_zero_delta(self):
        for delta in [10, -10, 0]:
            result = simulation.simulate_facility_change("CW-UICHANG", "hospital_count", delta)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["delta"], delta)
            self.assertAlmostEqual(result["simulated_value"], result["actual_value"] + delta)


class ValidationTest(unittest.TestCase):
    """4,5) 음수 결과·잘못된 ID/코드 거부."""

    def test_negative_resulting_value_rejected(self):
        regions = get_all_changwon_regions()
        jinhae = next(r for r in regions if r["region_id"] == "CW-JINHAE")
        current = next(
            i for i in jinhae["categories"]["의료"] if i["indicator_code"] == "hospital_count"
        )
        current_value = float(current["value"])

        result = simulation.simulate_facility_change(
            "CW-JINHAE", "hospital_count", -(int(current_value) + 1)
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("음수", result["message"])

    def test_invalid_region_id_rejected(self):
        result = simulation.simulate_facility_change("CW-NOPE", "hospital_count", 10)
        self.assertEqual(result["status"], "error")
        self.assertIn("지역 ID", result["message"])

    def test_invalid_indicator_code_rejected(self):
        result = simulation.simulate_facility_change("CW-JINHAE", "monthly_rent_avg", 10)
        self.assertEqual(result["status"], "error")
        self.assertIn("시뮬레이션 대상 지표", result["message"])

    def test_non_integer_delta_rejected(self):
        result = simulation.simulate_facility_change("CW-JINHAE", "hospital_count", "많이")
        self.assertEqual(result["status"], "error")


class OriginalDataProtectionTest(unittest.TestCase):
    """6,7) 실제 CSV가 전혀 바뀌지 않고, 연속 실행해도 가상 변화가 누적되지 않는지."""

    def test_csv_file_untouched(self):
        original_bytes = Path(INDICATORS_CSV).read_bytes()
        simulation.simulate_facility_change("CW-JINHAE", "hospital_count", 999)
        simulation.simulate_facility_change("CW-UICHANG", "bus_stop_count", -50)
        after_bytes = Path(INDICATORS_CSV).read_bytes()
        self.assertEqual(original_bytes, after_bytes, "simulate_facility_change()가 CSV 파일을 수정함")

    def test_repeated_calls_do_not_accumulate(self):
        values = []
        for _ in range(3):
            result = simulation.simulate_facility_change("CW-JINHAE", "hospital_count", 20)
            values.append((result["actual_value"], result["simulated_value"]))
        # 매 호출마다 actual_value가 동일해야 한다(이전 호출의 가상값이 남아있지 않음).
        self.assertTrue(all(v == values[0] for v in values), f"가상 변화가 누적됨: {values}")

    def test_fresh_query_after_simulation_matches_original(self):
        """8) 시뮬레이션 후 다시 원본 데이터로 계산하면 최초 결과와 동일한지."""
        from analysis.scoring import compute_region_scores_from_weights

        weights = {"hospital_count": 50, "bus_stop_count": 50}
        before = compute_region_scores_from_weights(weights, candidate_count=5)

        simulation.simulate_facility_change("CW-JINHAE", "hospital_count", 77)

        after = compute_region_scores_from_weights(weights, candidate_count=5)
        before_scores = {r["region_id"]: r["total_score"] for r in before["region_scores"]}
        after_scores = {r["region_id"]: r["total_score"] for r in after["region_scores"]}
        self.assertEqual(before_scores, after_scores)


class RenormalizationTest(unittest.TestCase):
    """9) 가상 변경으로 최댓값/최솟값이 바뀌면 다른 구의 점수도 재계산되는지."""

    def test_other_districts_scores_change_when_min_shifts(self):
        result = simulation.simulate_facility_change("CW-JINHAE", "hospital_count", 20)
        self.assertEqual(result["status"], "ok")

        # 진해구 외 다른 구 중 적어도 하나는 점수가 변해야 한다(선택한 구만 바뀌면 버그).
        other_deltas = [
            sc["score_delta"] for rid, sc in result["score_change"].items() if rid != "CW-JINHAE"
        ]
        self.assertTrue(
            any(abs(d) > 1e-9 for d in other_deltas),
            "진해구 외 다른 구 점수가 전혀 바뀌지 않음 - 전체 재정규화가 안 된 것으로 보임",
        )


class TieHandlingTest(unittest.TestCase):
    """10) 가상 변경 결과 5개 구 수치가 모두 같아지면 기존 50점 동점 처리가 유지되는지."""

    FAKE_REGIONS = [
        {
            "region_id": rid,
            "region_name": name,
            "categories": {
                "교통": [
                    {"indicator_code": "bus_stop_count", "indicator_name": "버스정류장 수",
                     "value": "100", "unit": "개", "source": "s", "reference_date": "2026-01-01",
                     "data_status": "확보"},
                ],
                "의료": [
                    {"indicator_code": "hospital_count", "indicator_name": "병원 수",
                     "value": value, "unit": "개", "source": "s", "reference_date": "2026-01-01",
                     "data_status": "확보"},
                ],
                "생활편의": [
                    {"indicator_code": "convenience_store_count", "indicator_name": "편의점 수",
                     "value": "30", "unit": "개", "source": "s", "reference_date": "2026-01-01",
                     "data_status": "확보"},
                ],
            },
        }
        for rid, name, value in [
            ("CW-UICHANG", "의창구", "50"), ("CW-SEONGSAN", "성산구", "50"),
            ("CW-MASANHAPPO", "마산합포구", "50"), ("CW-MASANHOEWON", "마산회원구", "50"),
            ("CW-JINHAE", "진해구", "40"),  # 진해구만 10 적어서, +10 시뮬레이션하면 전부 50으로 동일해짐
        ]
    ]

    @mock.patch("analysis.simulation.get_all_changwon_regions")
    def test_all_equal_after_simulation_gives_tied_50_points(self, mock_get_regions):
        mock_get_regions.return_value = copy.deepcopy(self.FAKE_REGIONS)
        result = simulation.simulate_facility_change(
            "CW-JINHAE", "hospital_count", 10, comparison_weights={"hospital_count": 100}
        )
        self.assertEqual(result["status"], "ok")
        for rid, sc in result["score_change"].items():
            self.assertAlmostEqual(sc["score_after"], 50.0, msg=f"{rid} 변경 후 점수가 50점이 아님")
            after_row = next(r for r in result["simulated_scores"]["region_scores"] if r["region_id"] == rid)
            self.assertTrue(after_row["tied"], f"{rid}가 동점(tied)으로 표시되지 않음")


class MissingIndicatorTest(unittest.TestCase):
    """미확보 지표는 시뮬레이션 대상에서 제외되는지(요구사항 2)."""

    FAKE_REGIONS_PARTIAL = [
        {
            "region_id": rid,
            "region_name": name,
            "categories": {
                "의료": [
                    {"indicator_code": "hospital_count", "indicator_name": "병원 수",
                     "value": None if rid == "CW-JINHAE" else "100", "unit": "개", "source": None,
                     "reference_date": None, "data_status": "미확보" if rid == "CW-JINHAE" else "확보"},
                ],
            },
        }
        for rid, name in [
            ("CW-UICHANG", "의창구"), ("CW-SEONGSAN", "성산구"), ("CW-MASANHAPPO", "마산합포구"),
            ("CW-MASANHOEWON", "마산회원구"), ("CW-JINHAE", "진해구"),
        ]
    ]

    @mock.patch("analysis.simulation.get_all_changwon_regions")
    def test_indicator_not_confirmed_everywhere_is_rejected(self, mock_get_regions):
        mock_get_regions.return_value = copy.deepcopy(self.FAKE_REGIONS_PARTIAL)
        result = simulation.simulate_facility_change("CW-UICHANG", "hospital_count", 10)
        self.assertEqual(result["status"], "error")
        self.assertIn("확보", result["message"])


if __name__ == "__main__":
    unittest.main()
