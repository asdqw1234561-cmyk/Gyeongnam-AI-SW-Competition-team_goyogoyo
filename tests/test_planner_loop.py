"""
agent/planner_loop.py (최초 추천 결과 관찰 -> 판단 -> 행동) 테스트. 실제 LLM을 호출하지 않는다.

    python -m unittest tests.test_planner_loop -v

실제 data/region_indicators.csv 로 run_agent_plan() 을 끝까지 실행한다.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import planner, planner_loop  # noqa: E402

WEIGHTS = {"bus_stop_count": 60, "convenience_store_count": 40}
CONDITIONS = ["교통", "생활편의(마트/편의점)"]
PLAN = {"goals": ["교통·생활편의 분석"], "unsupported_requests": [], "tool_calls": [
    {"tool": "get_available_indicators", "reason": "r"},
    {"tool": "get_region_indicators", "indicator_codes": ["bus_stop_count", "convenience_store_count"], "reason": "r"},
    {"tool": "calculate_region_scores", "weights": WEIGHTS, "reason": "r"}]}


def _resp(obj):
    return {"message": {"content": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)}}


def _run(responses):
    with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"}), \
         mock.patch("ollama.chat", side_effect=[r if isinstance(r, Exception) else _resp(r) for r in responses]) as m:
        result = planner.run_agent_plan(CONDITIONS, WEIGHTS, "창원시", 3)
    return result, m


def _baseline():
    """루프 없이 계산한 실제 점수(검증 기준값)."""
    result, _ = _run([PLAN, ConnectionError("no review")])
    return result


class PlannerLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = _baseline()
        rows = planner_loop._ranking_view(cls.base["score_result"])
        cls.top = rows[0]
        cls.second = rows[1]

    def _good_answer(self):
        bus = int(self.top["components"]["bus_stop_count"]["raw_value"])
        return (f"{self.top['region_name']}가 {self.top['total_score']}점으로 1위입니다. 버스정류장이 {bus}개로 가장 많고, "
                f"교통 가중치가 60%로 높게 반영되었습니다. 이 점수는 시설 수 기반 상대 비교일 뿐 실제 거주 적합도를 확정하지 않습니다.")

    def test_verified_explanation(self):
        result, m = _run([PLAN, {"action": "answer", "answer": self._good_answer()}])
        self.assertEqual(m.call_count, 2)
        self.assertEqual(result["final_answer"]["source"], "ai_verified")
        self.assertEqual([s["action"] for s in result["agent_steps"]], ["answer"])

    def test_score_result_never_changes(self):
        result, _ = _run([
            PLAN,
            {"action": "call_tools", "reason": "의료 중시 시 변화 확인",
             "tool_calls": [{"tool": "simulate_weights", "weights": {"hospital_count": 80, "bus_stop_count": 20}}]},
            {"action": "answer", "answer": self._good_answer()},
        ])
        self.assertEqual(result["score_result"], self.base["score_result"])   # 실제 추천 불변
        self.assertEqual(result["approved_weights"], self.base["approved_weights"])
        self.assertEqual(len(result["what_if_results"]), 1)
        sim = result["what_if_results"][0]
        self.assertEqual(sim["weights_percent"], {"hospital_count": 80.0, "bus_stop_count": 20.0})
        self.assertEqual(len(sim["ranking"]), 5)

    def test_extra_indicator_lookup_and_answer_using_it(self):
        def fake(**kwargs):
            content = kwargs["messages"][-1]["content"]
            n = fake.calls = getattr(fake, "calls", 0) + 1
            if n == 1:
                return _resp(PLAN)
            if n == 2:
                return _resp({"action": "call_tools", "reason": "의료 참고",
                              "tool_calls": [{"tool": "get_region_indicators", "indicator_codes": ["hospital_count"]}]})
            obs = json.loads(content.split("[관찰 내용]\n", 1)[1].split("\n\n", 1)[0])
            name, value = next(iter(obs["extra_indicators"]["hospital_count"]["values"].items()))
            return _resp({"action": "answer", "answer": f"참고로 {name}의 병원은 {int(value)}개입니다. "
                                                      "점수는 시설 수 기반 상대 비교일 뿐입니다."})
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"}), mock.patch("ollama.chat", side_effect=fake):
            result = planner.run_agent_plan(CONDITIONS, WEIGHTS, "창원시", 3)
        self.assertIn("hospital_count", result["extra_indicators"])
        self.assertEqual(result["review_tool_calls"][0]["step"], 2)
        self.assertEqual(result["final_answer"]["source"], "ai_verified")

    def test_unavailable_or_disallowed_tools_ignored(self):
        result, _ = _run([
            PLAN,
            {"action": "call_tools", "reason": "x", "tool_calls": [
                {"tool": "get_region_indicators", "indicator_codes": ["rent_price"]},
                {"tool": "calculate_region_scores", "weights": {"bus_stop_count": 100}},
                {"tool": "simulate_weights", "weights": {"rent_price": 100}}]},
            {"action": "answer", "answer": self._good_answer()},
        ])
        self.assertEqual(result["review_tool_calls"], [])
        note = result["agent_steps"][0]["note"]
        self.assertIn("rent_price", note)
        self.assertIn("calculate_region_scores", note)
        self.assertEqual(result["score_result"], self.base["score_result"])

    def test_what_if_limit(self):
        sims = [{"tool": "simulate_weights", "weights": {"hospital_count": 50 + i, "bus_stop_count": 50 - i}} for i in range(3)]
        result, _ = _run([
            PLAN,
            {"action": "call_tools", "reason": "a", "tool_calls": sims},
            {"action": "answer", "answer": self._good_answer()},
        ])
        self.assertEqual(len(result["what_if_results"]), planner_loop.MAX_WHAT_IF)

    def test_fabricated_score_rejected_then_corrected(self):
        result, m = _run([
            PLAN,
            {"action": "answer", "answer": f"{self.top['region_name']}가 99.9점으로 1위입니다."},
            {"action": "answer", "answer": self._good_answer()},
        ])
        self.assertEqual(m.call_count, 3)
        self.assertEqual(result["final_answer"]["source"], "ai_verified")
        self.assertIn("검증 실패", result["agent_steps"][0]["note"])

    def test_persistent_bad_answer_uses_python_summary(self):
        bad = {"action": "answer", "answer": "1위는 2위보다 2배 좋습니다."}
        result, _ = _run([PLAN, bad, bad, bad])
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertIn(self.top["region_name"], result["final_answer"]["text"])
        self.assertIn("상대 비교", result["final_answer"]["text"])

    def test_reviewer_failure(self):
        self.assertIsNotNone(self.base["review_error"])
        self.assertEqual(self.base["final_answer"]["source"], "python_summary")
        self.assertEqual(self.base["mode"], "ai_planned")

    def test_fallback_mode_skips_review(self):
        result, m = _run([ConnectionError("down")])
        self.assertEqual(m.call_count, 1)
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIsNone(result["final_answer"])
        self.assertEqual(result["agent_steps"], [])


class PlannerVerifyTest(unittest.TestCase):
    OBS = {
        "approved_weights_percent": {"bus_stop_count": 60.0, "convenience_store_count": 40.0},
        "ranking": [{"rank": 1, "region_name": "의창구", "total_score": 80.4,
                     "components": {"bus_stop_count": {"raw_value": 831.0, "normalized_score": 100.0}},
                     "reference_indicators": {"hospital_count": 262.0}}],
        "extra_indicators": {}, "what_if_simulations": [],
    }

    def ok(self, text):
        return planner_loop.verify_answer(text, self.OBS)[0]

    def test_units(self):
        self.assertTrue(self.ok("의창구가 80.4점으로 1위이며 버스정류장 831개, 병원 262개입니다. 교통 60%가 반영됐습니다. 창원시 5개 구 비교입니다."))
        self.assertTrue(self.ok("버스정류장 정규화 점수는 100점입니다."))
        self.assertFalse(self.ok("의창구가 80점으로 1위입니다."))          # 반올림한 점수
        self.assertFalse(self.ok("버스정류장이 830개입니다."))              # 틀린 개수
        self.assertFalse(self.ok("교통 비중 70%가 반영됐습니다."))          # 틀린 가중치
        self.assertFalse(self.ok("2위보다 1.5배 높습니다."))                # 배수
        self.assertFalse(self.ok("월세는 50만원입니다."))                   # 관찰에 없는 금액
        self.assertFalse(self.ok("시청까지 20분 걸립니다."))                # 시간
        self.assertFalse(self.ok("7위입니다."))                            # 순위 범위 밖


if __name__ == "__main__":
    unittest.main()
