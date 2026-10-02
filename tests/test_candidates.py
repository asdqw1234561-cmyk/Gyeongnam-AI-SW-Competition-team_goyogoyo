"""
analysis/candidates.py(정착 후보군 역할 + Critic 점검) 단위 테스트. LLM 호출 없음.

    python -m unittest tests.test_candidates -v
"""

import copy
import unittest

from analysis import scoring
from analysis.candidates import (
    AXIS_STATUS_MISSING,
    AXIS_STATUS_UNUSED,
    AXIS_STATUS_USED,
    build_candidate_set,
)


def _region(rid, name, bus, hosp, conv):
    def ind(code, value):
        return {"indicator_code": code, "indicator_name": code, "value": str(value), "unit": "개",
                "source": "s", "reference_date": "2026-01-01", "data_status": "확보"}
    return {"region_id": rid, "region_name": name, "categories": {
        "교통": [ind("bus_stop_count", bus)],
        "의료": [ind("hospital_count", hosp)],
        "생활편의": [ind("convenience_store_count", conv)],
    }}


# 현재 data/region_indicators.csv와 같은 값(버스정류장·의료기관·편의점)
REAL_LIKE = [
    _region("CW-UICHANG", "의창구", 831, 262, 199),
    _region("CW-SEONGSAN", "성산구", 452, 398, 245),
    _region("CW-MASANHAPPO", "마산합포구", 760, 242, 159),
    _region("CW-MASANHOEWON", "마산회원구", 365, 256, 151),
    _region("CW-JINHAE", "진해구", 518, 200, 196),
]
ALL_EQUAL = {"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1}


def _score(weights, regions=REAL_LIKE, count=3):
    return scoring.compute_region_scores_from_weights(weights, count, regions=copy.deepcopy(regions))


def _roles(cs):
    return {r["role"]: r for r in cs["roles"]}


def _codes(cs, level=None):
    return [c["code"] for c in cs["critic"]["checks"] if level is None or c["level"] == level]


class CandidateRolesTest(unittest.TestCase):

    def test_best_balanced_alternative_are_different_regions_on_real_like_data(self):
        cs = build_candidate_set(_score(ALL_EQUAL))
        roles = _roles(cs)
        self.assertEqual(cs["status"], "ok")
        self.assertEqual(roles["best"]["region_name"], "성산구")       # 종합 1위
        self.assertEqual(roles["balanced"]["region_name"], "의창구")   # 가장 약한 축 31.3점이 최대
        self.assertEqual(roles["best"]["weaknesses"], ["교통"])
        self.assertIn("의료", roles["best"]["strengths"])

    def test_best_role_never_changes_scoring_rank(self):
        result = _score(ALL_EQUAL)
        before = [(r["region_id"], r["total_score"], r["rank"]) for r in result["region_scores"]]
        cs = build_candidate_set(result)
        after = [(r["region_id"], r["total_score"], r["rank"]) for r in result["region_scores"]]
        self.assertEqual(before, after)
        self.assertEqual(_roles(cs)["best"]["region_id"], result["region_scores"][0]["region_id"])

    def test_value_role_is_unavailable_without_housing_data(self):
        role = _roles(build_candidate_set(_score(ALL_EQUAL)))["value"]
        self.assertEqual(role["status"], "unavailable")
        self.assertIn("주거비", role["reason"])
        self.assertNotIn("region_id", role)

    def test_dominated_alternative_is_revised_by_critic(self):
        # 성산구의 가장 약한 축 = 교통. 교통에서 앞서는 1차 대안 마산합포구는 의창구에 모든 축에서 뒤지므로
        # Critic이 지배되지 않는 의창구로 바꾸고 기록해야 한다.
        cs = build_candidate_set(_score(ALL_EQUAL))
        alt = _roles(cs)["alternative"]
        self.assertEqual(alt["region_name"], "의창구")
        self.assertEqual(alt["revised_from"]["region_name"], "마산합포구")
        self.assertEqual(alt["revised_from"]["dominated_by"], ["의창구"])
        self.assertIn("균형", alt["reason"])  # 같은 구라는 사실을 숨기지 않는다
        self.assertIn("revised", _codes(cs))

    def test_single_axis_disables_balanced_and_alternative(self):
        cs = build_candidate_set(_score({"bus_stop_count": 100}))
        roles = _roles(cs)
        self.assertEqual(roles["best"]["region_name"], "의창구")
        self.assertEqual(roles["balanced"]["status"], "unavailable")
        self.assertEqual(roles["alternative"]["status"], "unavailable")
        self.assertEqual(cs["pareto"], [])

    def test_pareto_on_real_like_data(self):
        self.assertEqual(build_candidate_set(_score(ALL_EQUAL))["pareto"], ["성산구", "의창구"])

    def test_no_usable_result_returns_unavailable(self):
        cs = build_candidate_set({"status": "no_usable_conditions", "message": "없음", "used_conditions": []})
        self.assertEqual(cs["status"], "unavailable")
        self.assertEqual(cs["roles"], [])

    def test_deterministic(self):
        self.assertEqual(build_candidate_set(_score(ALL_EQUAL)), build_candidate_set(_score(ALL_EQUAL)))


class CriticTest(unittest.TestCase):

    def test_close_gap_warning(self):
        regions = [
            _region("A", "가구", 100, 100, 100),
            _region("B", "나구", 99, 100, 100),
            _region("C", "다구", 0, 0, 0),
        ]
        cs = build_candidate_set(_score(ALL_EQUAL, regions))
        self.assertIn("close_gap", _codes(cs, "warning"))
        self.assertNotIn("close_gap", _codes(build_candidate_set(_score(ALL_EQUAL))))  # 실데이터 1·2위 차 12.1점

    def test_single_axis_dependency_warning(self):
        cs = build_candidate_set(_score({"hospital_count": 80, "bus_stop_count": 20}))
        msg = next(c["message"] for c in cs["critic"]["checks"] if c["code"] == "single_axis")
        self.assertIn("성산구", msg)
        self.assertIn("의료", msg)

    def test_dominated_top_candidate_warning(self):
        cs = build_candidate_set(_score(ALL_EQUAL, count=3))  # top3에 마산합포구 포함
        dominated = [c["message"] for c in cs["critic"]["checks"] if c["code"] == "dominated"]
        self.assertTrue(any(m.startswith("마산합포구") and "의창구" in m for m in dominated))

    def test_concentration_warning_when_all_roles_same_region(self):
        regions = [
            _region("A", "가구", 100, 100, 100),
            _region("B", "나구", 50, 50, 50),
            _region("C", "다구", 0, 0, 0),
        ]
        cs = build_candidate_set(_score(ALL_EQUAL, regions))
        self.assertIn("concentration", _codes(cs, "warning"))
        self.assertEqual(_roles(cs)["alternative"]["status"], "unavailable")

    def test_coverage_reports_missing_axes_and_unscored_inputs_without_filling(self):
        cs = build_candidate_set(_score({"bus_stop_count": 50, "hospital_count": 50}), ["주거비 예산"])
        axes = {a["axis"]: a["status"] for a in cs["axes"]}
        self.assertEqual(axes["주거비"], AXIS_STATUS_MISSING)
        self.assertEqual(axes["교육"], AXIS_STATUS_MISSING)
        self.assertEqual(axes["직장 접근성"], AXIS_STATUS_MISSING)
        self.assertEqual(axes["교통"], AXIS_STATUS_USED)
        self.assertEqual(axes["생활편의"], AXIS_STATUS_UNUSED)
        coverage = next(c for c in cs["critic"]["checks"] if c["code"] == "coverage")
        self.assertEqual(coverage["level"], "warning")
        self.assertIn("6개 평가축 중 2개", coverage["message"])
        self.assertIn("주거비 예산", coverage["message"])
        for role in cs["roles"]:  # 미확보 축은 후보 프로필에 숫자로 들어가지 않는다
            for p in role.get("axis_profile", []):
                self.assertIn(p["axis"], ("교통", "의료"))

    def test_granularity_limit_always_reported(self):
        self.assertIn("granularity", _codes(build_candidate_set(_score(ALL_EQUAL)), "info"))

    def test_feedback_reweighting_changes_candidates(self):
        """피드백으로 가중치가 바뀌면 같은 데이터로 후보를 다시 평가한다(교통 위주 -> 최적이 의창구)."""
        before = _roles(build_candidate_set(_score(ALL_EQUAL)))["best"]["region_name"]
        after = _roles(build_candidate_set(_score({"bus_stop_count": 80, "hospital_count": 10,
                                                   "convenience_store_count": 10})))["best"]["region_name"]
        self.assertEqual((before, after), ("성산구", "의창구"))


if __name__ == "__main__":
    unittest.main()
