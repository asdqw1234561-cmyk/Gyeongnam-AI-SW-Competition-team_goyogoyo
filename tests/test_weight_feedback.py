"""
analysis/weight_feedback.py - 방향성 가중치 피드백 규칙과 피드백 이력(G3) 테스트.

    python -m unittest tests.test_weight_feedback -v
"""

import unittest

from analysis import scoring
from analysis.weight_feedback import (
    append_history,
    apply_direction,
    describe_directions,
    format_weights,
    history_rows,
    resolve_direction_proposal,
    weights_from_result,
)

BUS, HOSP, CONV = "bus_stop_count", "hospital_count", "convenience_store_count"


class ApplyDirectionTest(unittest.TestCase):
    def assertSum100(self, weights):
        self.assertAlmostEqual(sum(weights.values()), 100.0, places=6)

    def test_increase_from_two_condition_equal(self):
        out = apply_direction({BUS: 50, HOSP: 50}, {HOSP: "increase"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["after"], {BUS: 30.0, HOSP: 70.0, CONV: 0.0})
        self.assertSum100(out["after"])

    def test_increase_takes_from_others_proportionally(self):
        out = apply_direction({BUS: 50, HOSP: 25, CONV: 25}, {HOSP: "increase"})
        # 20%p를 교통:생활편의 = 50:25 비율로 뺀다 -> 13.3 / 6.7
        self.assertEqual(out["after"][HOSP], 45.0)
        self.assertAlmostEqual(out["after"][BUS], 36.7, places=1)
        self.assertAlmostEqual(out["after"][CONV], 18.3, places=1)
        self.assertSum100(out["after"])

    def test_three_equal_thirds_sum_exactly_100(self):
        out = apply_direction({BUS: 33.3, HOSP: 33.3, CONV: 33.3}, {BUS: "increase"})
        self.assertEqual(out["status"], "ok")
        self.assertSum100(out["after"])
        self.assertGreater(out["after"][BUS], out["before"][BUS])
        self.assertAlmostEqual(out["after"][HOSP], out["after"][CONV], places=1)

    def test_strong_uses_bigger_step(self):
        normal = apply_direction({BUS: 50, HOSP: 50}, {HOSP: "increase"}, "normal")
        strong = apply_direction({BUS: 50, HOSP: 50}, {HOSP: "increase"}, "strong")
        self.assertEqual(normal["step"], 20.0)
        self.assertEqual(strong["step"], 30.0)
        self.assertEqual(strong["after"][HOSP], 80.0)

    def test_unknown_strength_falls_back_to_normal(self):
        out = apply_direction({BUS: 50, HOSP: 50}, {HOSP: "increase"}, "huge")
        self.assertEqual(out["step"], 20.0)

    def test_decrease_gives_to_others_proportionally(self):
        out = apply_direction({BUS: 40, HOSP: 40, CONV: 20}, {CONV: "decrease"})
        self.assertEqual(out["after"][CONV], 0.0)
        self.assertEqual(out["after"][BUS], 50.0)
        self.assertEqual(out["after"][HOSP], 50.0)

    def test_decrease_into_zero_others_splits_equally(self):
        out = apply_direction({BUS: 100}, {BUS: "decrease"})
        self.assertEqual(out["after"], {BUS: 80.0, HOSP: 10.0, CONV: 10.0})

    def test_mixed_moves_from_decrease_to_increase_only(self):
        out = apply_direction({BUS: 40, HOSP: 30, CONV: 30}, {HOSP: "increase", CONV: "decrease"})
        self.assertEqual(out["after"], {BUS: 40.0, HOSP: 50.0, CONV: 10.0})

    def test_increase_limited_by_available_weight(self):
        out = apply_direction({BUS: 10, HOSP: 90}, {HOSP: "increase"})
        self.assertEqual(out["after"], {BUS: 0.0, HOSP: 100.0, CONV: 0.0})

    def test_already_at_max_is_no_change(self):
        out = apply_direction({HOSP: 100}, {HOSP: "increase"})
        self.assertEqual(out["status"], "no_change")
        self.assertIsNone(out["after"])
        self.assertIn("의료", out["message"])

    def test_already_at_zero_is_no_change(self):
        out = apply_direction({BUS: 50, HOSP: 50}, {CONV: "decrease"})
        self.assertEqual(out["status"], "no_change")

    def test_all_three_same_direction_is_invalid(self):
        for d in ("increase", "decrease"):
            out = apply_direction({BUS: 30, HOSP: 30, CONV: 40}, {BUS: d, HOSP: d, CONV: d})
            self.assertEqual(out["status"], "invalid")
            self.assertIsNone(out["after"])

    def test_unsupported_code_or_value_is_invalid(self):
        self.assertEqual(apply_direction({BUS: 50, HOSP: 50}, {"rent": "increase"})["status"], "invalid")
        self.assertEqual(apply_direction({BUS: 50, HOSP: 50}, {HOSP: "up"})["status"], "invalid")
        self.assertEqual(apply_direction({BUS: 50, HOSP: 50}, {})["status"], "invalid")

    def test_unnormalized_input_is_normalized_first(self):
        out = apply_direction({BUS: 1, HOSP: 1}, {HOSP: "increase"})
        self.assertEqual(out["before"], {BUS: 50.0, HOSP: 50.0, CONV: 0.0})
        self.assertEqual(out["after"][HOSP], 70.0)

    def test_deterministic(self):
        a = apply_direction({BUS: 37, HOSP: 21, CONV: 42}, {CONV: "increase"}, "strong")
        b = apply_direction({BUS: 37, HOSP: 21, CONV: 42}, {CONV: "increase"}, "strong")
        self.assertEqual(a, b)


class ResolveProposalTest(unittest.TestCase):
    def test_direction_becomes_set_weights_with_preview(self):
        proposal = {
            "status": "ok", "type": "adjust_direction", "weights": None,
            "directions": {HOSP: "increase"}, "strength": "normal", "message": None,
        }
        out = resolve_direction_proposal(proposal, {BUS: 50, HOSP: 50})
        self.assertEqual(out["type"], "set_weights")
        self.assertEqual(out["weights"][HOSP], 70.0)
        self.assertEqual(out["direction"]["before"][HOSP], 50.0)
        self.assertTrue(out["direction"]["rule"])

    def test_no_change_becomes_clarification(self):
        proposal = {
            "status": "ok", "type": "adjust_direction", "weights": None,
            "directions": {HOSP: "increase"}, "strength": "normal", "message": None,
        }
        out = resolve_direction_proposal(proposal, {HOSP: 100})
        self.assertEqual(out["type"], "ask_clarification")
        self.assertIsNone(out["weights"])

    def test_other_types_pass_through(self):
        for proposal in (
            {"status": "ok", "type": "set_weights", "weights": {HOSP: 100}, "message": None},
            {"status": "ok", "type": "unsupported", "weights": None, "message": "x"},
            {"status": "ollama_error", "type": None, "weights": None, "message": "x"},
        ):
            self.assertIs(resolve_direction_proposal(proposal, {BUS: 50, HOSP: 50}), proposal)


class HistoryTest(unittest.TestCase):
    def test_history_records_before_after_and_top_region_from_real_scoring(self):
        initial = scoring.compute_region_scores_from_weights({BUS: 50, HOSP: 50}, 3)
        after = scoring.compute_region_scores_from_weights({BUS: 30, HOSP: 70}, 3)
        history = append_history([], "자연어(방향)", "병원을 더 중요하게", initial, after, "rule")
        self.assertEqual(len(history), 1)
        entry = history[0]
        self.assertEqual(entry["round"], 1)
        self.assertEqual(entry["before"], {BUS: 50.0, HOSP: 50.0, CONV: 0.0})
        self.assertEqual(entry["after"], {BUS: 30.0, HOSP: 70.0, CONV: 0.0})
        top_after = next(r["region_name"] for r in after["region_scores"] if r["rank"] == 1)
        self.assertEqual(entry["top_after"], top_after)

        history2 = append_history(history, "슬라이더", None, after, initial)
        self.assertEqual([h["round"] for h in history2], [1, 2])
        self.assertEqual(len(history), 1, "원래 목록은 바뀌지 않아야 한다")

        rows = history_rows(history2)
        self.assertEqual(rows[0]["요청"], "병원을 더 중요하게")
        self.assertEqual(rows[0]["변경 후"], "교통 30% · 의료 70%")
        self.assertEqual(rows[1]["요청"], "-")

    def test_weights_from_result_and_format(self):
        self.assertEqual(weights_from_result(None), {BUS: 0.0, HOSP: 0.0, CONV: 0.0})
        self.assertEqual(format_weights({BUS: 0.0, HOSP: 0.0}), "-")
        self.assertEqual(describe_directions({CONV: "decrease", HOSP: "increase"}), "의료 ↑, 생활편의 ↓")


if __name__ == "__main__":
    unittest.main()
