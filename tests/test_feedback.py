"""
G3 방향성 피드백 → 결정적 가중치 조정 → 단일 재평가 경로 → feedback_history 테스트.
LLM(ollama.chat)은 모킹하고, 실제 data/region_indicators.csv와 같은 값의 픽스처로 재평가한다.

    python -m unittest tests.test_feedback -v
"""

import copy
import json
import unittest
from unittest import mock

from agent import ollama_agent
from analysis import feedback
from tests.test_candidates import ALL_EQUAL, REAL_LIKE

EQUAL = {"bus_stop_count": 100 / 3, "hospital_count": 100 / 3, "convenience_store_count": 100 / 3}


def _llm(payload: dict):
    return mock.patch("ollama.chat", return_value={"message": {"content": json.dumps(payload, ensure_ascii=False)}})


def _direction(axis, direction, strength="normal", explicit=False):
    return {"type": "adjust_direction", "message": None, "adjustments": [
        {"axis": axis, "direction": direction, "strength": strength, "strength_explicit": explicit}]}


def _interpret(text, payload):
    with _llm(payload):
        return ollama_agent.interpret_weight_feedback(text)


def _reeval(weights):
    return feedback.reevaluate(weights, 3, ["주거비 예산"], regions=copy.deepcopy(REAL_LIKE))


def _roles(review):
    return {r["role"]: r.get("region_name") for r in review["roles"]}


class DirectionExtractionTest(unittest.TestCase):
    """LLM은 축·방향·강도 표현만 추출하고, 축 판정은 Python이 다시 한다."""

    def test_medical_more_important(self):
        result = _interpret("의료를 더 중요하게", _direction("의료", "increase"))
        self.assertEqual(result["type"], "adjust_direction")
        self.assertIsNone(result["weights"])  # 숫자는 LLM이 만들지 않는다
        self.assertEqual(result["adjustments"], [{"indicator_code": "hospital_count", "axis": "의료",
                                                  "direction": "increase", "strength": "normal",
                                                  "strength_explicit": False}])

    def test_hospital_alias_maps_to_medical(self):
        result = _interpret("병원을 더 중요하게", _direction("병원", "increase"))
        self.assertEqual(result["adjustments"][0]["indicator_code"], "hospital_count")

    def test_slight_decrease_keeps_explicit_strength(self):
        result = _interpret("교통은 조금 덜 중요하게", _direction("교통", "decrease", "slight", True))
        self.assertEqual(result["adjustments"][0]["strength"], "slight")
        self.assertTrue(result["adjustments"][0]["strength_explicit"])

    def test_strength_ignored_when_not_explicit(self):
        result = _interpret("교통을 덜 중요하게", _direction("교통", "decrease", "strong", False))
        self.assertEqual(result["adjustments"][0]["strength"], "normal")

    def test_nonexistent_axis_is_unsupported(self):
        result = _interpret("날씨를 더 중요하게", _direction("날씨", "increase"))
        self.assertEqual(result["type"], "unsupported")
        self.assertIn("없는 평가축", result["message"])
        self.assertNotIn("adjustments", result)

    def test_missing_axis_housing_is_unsupported(self):
        result = _interpret("주거비를 더 중요하게", _direction("주거비", "increase"))
        self.assertEqual(result["type"], "unsupported")
        self.assertIn("주거비", result["message"])
        self.assertIn("확보하지 못한", result["message"])

    def test_mixed_supported_and_missing_axis_is_rejected_whole(self):
        payload = {"type": "adjust_direction", "adjustments": [
            {"axis": "의료", "direction": "increase", "strength_explicit": False},
            {"axis": "집값", "direction": "increase", "strength_explicit": False}]}
        self.assertEqual(_interpret("의료랑 집값을 더 중요하게", payload)["type"], "unsupported")

    def test_axis_not_in_user_text_asks_clarification(self):
        result = _interpret("교통을 더 중요하게", _direction("의료", "increase"))  # LLM이 축을 잘못 읽음
        self.assertEqual(result["type"], "ask_clarification")

    def test_invalid_direction_is_invalid_response(self):
        self.assertEqual(_interpret("의료를 더", _direction("의료", "up"))["status"], "invalid_response")

    def test_reset_type(self):
        result = _interpret("원래대로 돌려줘", {"type": "reset", "message": None})
        self.assertEqual((result["status"], result["type"]), ("ok", "reset"))


class AdjustRuleTest(unittest.TestCase):
    """숫자 없는 방향성 피드백의 결정적 조정 규칙."""

    def test_increase_medical_from_equal(self):
        plan = feedback.adjust_weights(EQUAL, [{"indicator_code": "hospital_count", "direction": "increase"}])
        self.assertEqual(plan["status"], "ok")
        self.assertAlmostEqual(plan["after"]["hospital_count"], 300 / 7)          # 42.857
        self.assertAlmostEqual(plan["after"]["bus_stop_count"], 200 / 7)          # 28.571
        self.assertAlmostEqual(sum(plan["after"].values()), 100)

    def test_decrease_transport_from_equal(self):
        plan = feedback.adjust_weights(EQUAL, [{"indicator_code": "bus_stop_count", "direction": "decrease"}])
        self.assertAlmostEqual(plan["after"]["bus_stop_count"], 25.0)
        self.assertAlmostEqual(plan["after"]["hospital_count"], 37.5)

    def test_increase_then_decrease_returns_to_original(self):
        up = feedback.adjust_weights({"bus_stop_count": 50, "hospital_count": 30, "convenience_store_count": 20},
                                     [{"indicator_code": "hospital_count", "direction": "increase", "strength": "strong"}])
        down = feedback.adjust_weights(up["after"], [{"indicator_code": "hospital_count", "direction": "decrease",
                                                      "strength": "strong"}])
        for code, value in {"bus_stop_count": 50, "hospital_count": 30, "convenience_store_count": 20}.items():
            self.assertAlmostEqual(down["after"][code], value)

    def test_zero_axis_increase_gets_fixed_share(self):
        plan = feedback.adjust_weights({"bus_stop_count": 50, "hospital_count": 50, "convenience_store_count": 0},
                                       [{"indicator_code": "convenience_store_count", "direction": "increase"}])
        self.assertAlmostEqual(plan["after"]["convenience_store_count"], 20.0)
        self.assertAlmostEqual(plan["after"]["bus_stop_count"], 40.0)

    def test_no_change_cases_are_reported_not_invented(self):
        only = {"bus_stop_count": 100, "hospital_count": 0, "convenience_store_count": 0}
        for adj in ({"indicator_code": "bus_stop_count", "direction": "increase"},
                    {"indicator_code": "bus_stop_count", "direction": "decrease"},
                    {"indicator_code": "hospital_count", "direction": "decrease"}):
            plan = feedback.adjust_weights(only, [adj])
            self.assertEqual(plan["status"], "no_change")
            self.assertTrue(plan["message"])
            self.assertEqual(plan["after"], plan["before"])

    def test_deterministic(self):
        adj = [{"indicator_code": "hospital_count", "direction": "increase"}]
        self.assertEqual(feedback.adjust_weights(EQUAL, adj), feedback.adjust_weights(EQUAL, adj))


class ReevaluationAndHistoryTest(unittest.TestCase):
    """단일 재평가 경로와 feedback_history - 후보 역할 변화까지 기록."""

    def _step(self, current_result, adjustments, text):
        before_w = feedback.weights_from_result(current_result["score_result"])
        plan = feedback.adjust_weights(before_w, adjustments)
        after = _reeval(plan["after"])
        entry = feedback.history_entry(
            source="nl_direction", text=text, adjustments=plan["steps"], before_weights=before_w,
            after_weights=feedback.weights_from_result(after["score_result"]), approved=True,
            before_result=current_result["score_result"], after_result=after["score_result"],
            before_review=current_result["candidate_review"], after_review=after["candidate_review"])
        return after, entry

    def test_reevaluate_matches_scoring_and_candidates(self):
        out = _reeval(ALL_EQUAL)
        self.assertEqual(out["score_result"]["region_scores"][0]["region_name"], "성산구")
        self.assertEqual(_roles(out["candidate_review"])["balanced"], "의창구")

    def test_history_entry_has_required_fields(self):
        start = _reeval(EQUAL)
        _after, entry = self._step(start, [{"indicator_code": "hospital_count", "direction": "increase",
                                            "axis": "의료"}], "의료를 더 중요하게")
        self.assertEqual(entry["targets"][0]["axis"], "의료")
        self.assertEqual(entry["targets"][0]["direction"], "increase")
        self.assertEqual(entry["before_weights"]["hospital_count"], 33.3)
        self.assertEqual(entry["after_weights"]["hospital_count"], 42.9)
        self.assertTrue(entry["approved"])
        self.assertEqual({c["role"] for c in entry["candidate_changes"]}, {"best", "balanced", "value", "alternative"})
        self.assertEqual((entry["top_before"], entry["top_after"]), ("성산구", "성산구"))

    def test_rejected_entry_has_no_candidate_change(self):
        entry = feedback.history_entry(source="nl_direction", text="의료를 더", before_weights=EQUAL,
                                       after_weights=EQUAL, approved=False)
        self.assertFalse(entry["approved"])
        self.assertEqual(entry["candidate_changes"], [])
        self.assertIsNone(entry["top_after"])

    def test_medical_up_then_back_restores_candidates(self):
        start = _reeval(EQUAL)
        up, _ = self._step(start, [{"indicator_code": "hospital_count", "direction": "increase"}], "의료 더")
        back, entry = self._step(up, [{"indicator_code": "hospital_count", "direction": "decrease"}], "의료 덜")
        for code in EQUAL:
            self.assertAlmostEqual(feedback.weights_from_result(back["score_result"])[code], EQUAL[code])
        self.assertEqual(_roles(back["candidate_review"]), _roles(start["candidate_review"]))

    def test_consecutive_feedback_changes_candidate_roles(self):
        """교통을 많이 더 중요하게 → 교통을 더 중요하게: 최적 후보가 성산구 → 의창구로 바뀌고 기록된다."""
        start = _reeval(EQUAL)
        first, e1 = self._step(start, [{"indicator_code": "bus_stop_count", "direction": "increase",
                                        "strength": "strong"}], "교통을 훨씬 더 중요하게")
        second, e2 = self._step(first, [{"indicator_code": "bus_stop_count", "direction": "increase"}],
                                "교통을 더 중요하게")
        best_change = next(c for c in e1["candidate_changes"] if c["role"] == "best")
        self.assertEqual((best_change["before"], best_change["after"], best_change["changed"]),
                         ("성산구", "의창구", True))
        self.assertEqual(e1["after_weights"]["bus_stop_count"], 50.0)
        self.assertEqual(e2["before_weights"], e1["after_weights"])  # 연속 피드백은 직전 적용 결과에서 출발
        self.assertEqual(e2["after_weights"]["bus_stop_count"], 60.0)
        self.assertEqual(_roles(second["candidate_review"])["best"], "의창구")
        self.assertEqual(_roles(second["candidate_review"])["alternative"], "성산구")  # 의창구 약점(의료) 보완

    def test_reevaluate_does_not_modify_scoring_formula_results(self):
        from analysis import scoring
        direct = scoring.compute_region_scores_from_weights(ALL_EQUAL, 3, regions=copy.deepcopy(REAL_LIKE))
        self.assertEqual([r["total_score"] for r in _reeval(ALL_EQUAL)["score_result"]["region_scores"]],
                         [r["total_score"] for r in direct["region_scores"]])


if __name__ == "__main__":
    unittest.main()
