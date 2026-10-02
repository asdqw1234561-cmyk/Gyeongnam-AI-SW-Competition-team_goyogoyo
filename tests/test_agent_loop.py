"""
agent/agent_loop.py (관찰 -> 판단 -> 행동 반복) 테스트. 실제 LLM을 호출하지 않는다.

    python -m unittest tests.test_agent_loop -v

ollama.chat 을 순서대로 다른 응답을 돌려주는 가짜로 바꾸고, 실제 데이터(편의점·버스정류장
CSV)로 run_location_agent() 를 끝까지 실행해 본다.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import agent_loop, location_agent  # noqa: E402
from services import bus_stops, convenience  # noqa: E402

CENTER = (35.2280, 128.6811)  # 창원시청 부근(근사)


def _resp(obj) -> dict:
    content = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return {"message": {"content": content}}


PLAN_CONV = {"goals": ["편의점 조회"], "unsupported_requests": [],
             "tool_calls": [{"tool": "find_nearby_convenience_stores", "reason": "편의점 요청"}]}
PLAN_BOTH = {"goals": ["편의점·버스 조회"], "unsupported_requests": [],
             "tool_calls": [{"tool": "find_nearby_convenience_stores", "reason": "r"}]}


def _real_counts(radius=500):
    conv = convenience.find_nearby_stores(*CENTER, radius_m=radius, max_results=10)
    bus = bus_stops.find_nearby_bus_stops(*CENTER, radius_m=radius, max_results=10)
    return conv, bus


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _run(self, responses, text="500m 안 편의점 알려줘"):
        with mock.patch("ollama.chat", side_effect=[_resp(r) if not isinstance(r, Exception) else r
                                                    for r in responses]) as m:
            result = location_agent.run_location_agent(text, CENTER, 500, 10)
        return result, m

    def test_verified_answer_first_review(self):
        conv, _ = _real_counts()
        first = conv["stores"][0]
        answer = (f"반경 500m 안에 편의점이 {conv['total_count']}개 있습니다. 가장 가까운 곳은 "
                  f"{first['facility_name']}로 직선거리 {first['straight_distance_m']}m입니다.")
        result, m = self._run([PLAN_CONV, {"action": "answer", "answer": answer}])
        self.assertEqual(m.call_count, 2)
        self.assertEqual(result["mode"], "ai_planned")
        self.assertEqual(result["final_answer"]["source"], "ai_verified")
        self.assertEqual(result["final_answer"]["text"], answer)
        self.assertEqual([s["action"] for s in result["agent_steps"]], ["answer"])
        self.assertEqual(result["executed_tool_calls"][0]["step"], 1)

    def test_call_tools_then_answer(self):
        conv, bus = _real_counts()
        answer = (f"편의점은 {conv['total_count']}개, 버스정류장은 {bus['total_count']}개입니다. "
                  "모두 직선거리 기준입니다.")
        result, m = self._run([
            PLAN_BOTH,
            {"action": "call_tools", "reason": "버스정류장 정보도 필요",
             "tool_calls": [{"tool": "find_nearby_bus_stops"}]},
            {"action": "answer", "answer": answer},
        ], text="500m 안에 편의점이랑 버스정류장 알려줘")
        self.assertEqual(m.call_count, 3)
        tools = [(e["tool"], e["step"]) for e in result["executed_tool_calls"]]
        self.assertEqual(tools, [("find_nearby_convenience_stores", 1), ("find_nearby_bus_stops", 2)])
        self.assertEqual(result["final_answer"]["source"], "ai_verified")
        self.assertEqual([s["action"] for s in result["agent_steps"]], ["call_tools", "answer"])
        self.assertEqual(result["agent_steps"][0]["executed_tools"], ["find_nearby_bus_stops"])
        # 추가 조회도 승인된 좌표·고정 반경으로만 실행
        self.assertEqual(result["executed_tool_calls"][1]["radius_m"], 500)
        self.assertEqual(result["executed_tool_calls"][1]["result"]["query"]["lat"], CENTER[0])

    def test_added_tool_respects_request_scope(self):
        # 사용자는 편의점만 물었는데 AI가 버스정류장을 추가로 원하면 -> 범위 보정으로 편의점이 되고,
        # 이미 실행한 것과 같으므로 다시 실행하지 않는다.
        result, m = self._run([
            PLAN_CONV,
            {"action": "call_tools", "reason": "버스도 보자", "tool_calls": [{"tool": "find_nearby_bus_stops"}]},
            {"action": "answer", "answer": "편의점 정보는 위와 같습니다."},
        ])
        self.assertEqual([e["tool"] for e in result["executed_tool_calls"]], ["find_nearby_convenience_stores"])
        self.assertIn("이미 실행", result["agent_steps"][0]["note"])
        self.assertEqual(m.call_count, 3)

    def test_compare_tool_allowed_in_review_stage(self):
        # 요청 문장에 '비교' 단어가 없어도, 결과를 본 AI가 반경별 비교를 요청하면 실행한다
        conv, _ = _real_counts()
        result, _ = self._run([
            PLAN_CONV,
            {"action": "call_tools", "reason": "반경을 넓혔을 때 변화 확인 필요",
             "tool_calls": [{"tool": "compare_nearby_facilities"}]},
            {"action": "answer", "answer": f"반경 500m 안 편의점은 {conv['total_count']}개입니다."},
        ], text="편의점 알려주고 반경 넓혀도 충분한지 판단해줘")
        self.assertEqual([e["tool"] for e in result["executed_tool_calls"]],
                         ["find_nearby_convenience_stores", "compare_nearby_facilities"])

    def test_disallowed_tool_rejected(self):
        result, _ = self._run([
            PLAN_CONV,
            {"action": "call_tools", "reason": "병원도", "tool_calls": [{"tool": "find_hospitals"}]},
            {"action": "answer", "answer": "편의점 결과만 확인할 수 있습니다."},
        ])
        self.assertIn("허용되지 않은 도구", result["agent_steps"][0]["note"])
        self.assertEqual(len(result["executed_tool_calls"]), 1)
        self.assertEqual(result["final_answer"]["source"], "ai_verified")

    def test_round_limit(self):
        # AI가 계속 새 조회만 요청해도 최대 3회 판단 후 종료 (총 호출 4회 이하)
        result, m = self._run([
            PLAN_CONV,
            {"action": "call_tools", "reason": "a", "tool_calls": [{"tool": "find_nearby_convenience_stores", "max_results": 3}]},
            {"action": "call_tools", "reason": "b", "tool_calls": [{"tool": "find_nearby_convenience_stores", "max_results": 4}]},
            {"action": "call_tools", "reason": "c", "tool_calls": [{"tool": "find_nearby_convenience_stores", "max_results": 5}]},
        ])
        self.assertEqual(m.call_count, 1 + agent_loop.MAX_REVIEW_ROUNDS)
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertLessEqual(len(result["executed_tool_calls"]), 1 + agent_loop.MAX_REVIEW_ROUNDS - 1)

    def test_fabricated_number_rejected(self):
        bad = {"action": "answer", "answer": "편의점이 9999개 있습니다."}
        result, m = self._run([PLAN_CONV, bad, bad, bad])        # 고쳐 쓰기 기회를 줘도 계속 틀림
        self.assertEqual(m.call_count, 1 + agent_loop.MAX_REVIEW_ROUNDS)
        self.assertIsNone(result["review_error"])
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertIn("9999", result["final_answer"]["rejected_reason"])
        self.assertIn("직선거리", result["final_answer"]["text"])

    def test_walking_time_rejected(self):
        bad = {"action": "answer", "answer": "가장 가까운 편의점은 걸어서 3분입니다."}
        result, _ = self._run([PLAN_CONV, bad, bad, bad])
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertIn("이동시간", result["final_answer"]["rejected_reason"])

    def test_distance_without_straight_line_rejected(self):
        conv, _ = _real_counts()
        d = conv["stores"][0]["straight_distance_m"]
        bad = {"action": "answer", "answer": f"가장 가까운 편의점은 {d}m 거리입니다."}
        result, _ = self._run([PLAN_CONV, bad, bad, bad])
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertIn("직선거리", result["final_answer"]["rejected_reason"])

    def test_self_correction_after_rejection(self):
        conv, _ = _real_counts()
        good = f"반경 500m 안 편의점은 {conv['total_count']}개로, 300m보다 크게 늘어납니다."
        captured = []

        def fake(**kwargs):
            captured.append(kwargs["messages"][-1]["content"])
            seq = [PLAN_CONV, {"action": "answer", "answer": "300m보다 약 2배 많습니다."},
                   {"action": "answer", "answer": good}]
            return _resp(seq[len(captured) - 1])

        with mock.patch("ollama.chat", side_effect=fake):
            result = location_agent.run_location_agent("500m 안 편의점 알려줘", CENTER, 500, 10)
        self.assertEqual(result["final_answer"]["source"], "ai_verified")
        self.assertEqual(result["final_answer"]["text"], good)
        self.assertIsNone(result["final_answer"]["rejected_reason"])
        self.assertEqual([s["action"] for s in result["agent_steps"]], ["answer", "answer"])
        self.assertIn("검증 실패", result["agent_steps"][0]["note"])
        self.assertIn("거부된 이유", captured[2])           # 실패 이유를 AI에게 전달
        self.assertIn("2", captured[2])

    def test_reviewer_failure_keeps_first_results(self):
        result, _ = self._run([PLAN_CONV, ConnectionError("down")])
        self.assertEqual(result["mode"], "ai_planned")
        self.assertIsNotNone(result["review_error"])
        self.assertEqual(len(result["executed_tool_calls"]), 1)
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertEqual(result["agent_steps"][0]["action"], "error")

    def test_unparseable_review(self):
        result, _ = self._run([PLAN_CONV, "그냥 텍스트"])
        self.assertIsNotNone(result["review_error"])
        self.assertEqual(result["final_answer"]["source"], "python_summary")

    def test_fallback_mode_does_not_call_reviewer(self):
        result, m = self._run([ConnectionError("down")])
        self.assertEqual(m.call_count, 1)
        self.assertEqual(result["mode"], "fallback_default")
        self.assertEqual(result["agent_steps"], [])
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertIn("300m", result["final_answer"]["text"])

    def test_limit_error_mid_loop(self):
        from agent import llm
        calls = {"n": 0}
        real = llm._check_and_count

        def limited(messages):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise llm.LLMLimitError("이번 접속에서 사용할 수 있는 AI 호출 횟수(1회)를 모두 사용했습니다.")
            return real(messages)

        with mock.patch.object(llm, "_check_and_count", side_effect=limited):
            result, _ = self._run([PLAN_CONV])
        self.assertIn("1회", result["review_error"])
        self.assertEqual(result["final_answer"]["source"], "python_summary")


class RadiusParticleTest(unittest.TestCase):
    """반경 단위 뒤에 한글 조사가 붙어도 인식해야 한다(기존 \\b 정규식 버그 수정)."""

    def test_korean_particles(self):
        f = location_agent.extract_explicit_radius_m
        self.assertEqual(f("1km로 넓히면")["radius_m"], 1000)
        self.assertEqual(f("500m에서 편의점")["radius_m"], 500)
        self.assertEqual(f("300미터로")["radius_m"], 300)
        unsupported = f("200m로 해줘")
        self.assertTrue(unsupported["found"])
        self.assertIsNone(unsupported["radius_m"])          # 지원 안 하는 반경 -> 안내 대상
        self.assertFalse(f("5mm")["found"])
        self.assertFalse(f("15m2 원룸")["found"])


class VerifyTest(unittest.TestCase):
    OBS = [{"tool": "find_nearby_convenience_stores", "radius_m": 500, "total_count": 21,
            "nearest": [{"rank": 1, "name": "GS25창원정우점", "straight_distance_m": 154,
                         "road_address": "경상남도 창원시 성산구 원이대로 587"}]}]

    def test_ok(self):
        ok, _ = agent_loop.verify_answer("반경 500m 안 편의점 21개, 가장 가까운 GS25창원정우점은 직선거리 154m입니다.",
                                         self.OBS, "질문", 500)
        self.assertTrue(ok)

    def test_numbers_from_names_and_user_text_allowed(self):
        ok, _ = agent_loop.verify_answer("원이대로 587의 GS25가 있습니다. 요청하신 2곳 기준으로 정리했습니다.",
                                         self.OBS, "2곳 알려줘", 500)
        self.assertTrue(ok)

    def test_radius_mention_does_not_need_straight_line_word(self):
        ok, why = agent_loop.verify_answer("500m 안에 편의점이 21개 있고, 1km 이내로 넓히면 더 많습니다.",
                                           self.OBS, "q", 500)
        self.assertTrue(ok, why)

    def test_facility_distance_needs_straight_line_word(self):
        ok, why = agent_loop.verify_answer("GS25창원정우점은 154m 떨어져 있습니다.", self.OBS, "q", 500)
        self.assertFalse(ok)

    def test_rank_number_cannot_be_used_as_count_or_ratio(self):
        obs = self.OBS[:1] + [{"tool": "find_nearby_bus_stops", "radius_m": 500, "total_count": 14,
                               "nearest": [{"rank": 1, "straight_distance_m": 128}, {"rank": 2, "straight_distance_m": 130}]}]
        self.assertFalse(agent_loop.verify_answer("300m보다 약 2배 많습니다.", obs, "q", 500)[0])
        self.assertFalse(agent_loop.verify_answer("버스정류장은 2개입니다.", obs, "q", 500)[0])  # 실제 14개
        self.assertFalse(agent_loop.verify_answer("가까운 2곳은 직선거리 128m, 130m입니다.", obs, "q", 500)[0])
        self.assertTrue(agent_loop.verify_answer("버스정류장은 14개이고, 가장 가까운 두 곳은 직선거리 128m, 130m입니다.",
                                                 obs, "q", 500)[0])
        self.assertFalse(agent_loop.verify_answer("가장 가까운 곳은 직선거리 129m입니다.", obs, "q", 500)[0])

    def test_rounded_number_rejected(self):
        ok, why = agent_loop.verify_answer("약 150m(직선거리) 거리에 있습니다.", self.OBS, "q", 500)
        self.assertFalse(ok)
        self.assertIn("150", why)

    def test_empty_and_long(self):
        self.assertFalse(agent_loop.verify_answer("", self.OBS, "q", 500)[0])
        self.assertFalse(agent_loop.verify_answer("가" * 900, self.OBS, "q", 500)[0])

    def test_python_summary_formats(self):
        text = agent_loop.python_summary(self.OBS + [{
            "tool": "compare_nearby_facilities",
            "bus_stop_counts_by_radius_m": {"300": 9, "1000": 39, "500": 14},
            "convenience_store_counts_by_radius_m": {"300": 12, "500": 21, "1000": 91}}])
        self.assertIn("편의점: 21개", text)
        self.assertLess(text.index("300m"), text.index("1000m"))   # 반경 순서 정렬
        self.assertIn("직선거리", text)


if __name__ == "__main__":
    unittest.main()
