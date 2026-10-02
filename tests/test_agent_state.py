"""
agent/agent_state.py (위치 분석 대화 기억) 테스트. 실제 LLM을 호출하지 않는다.

    python -m unittest tests.test_agent_state -v
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import location_agent  # noqa: E402
from agent.agent_state import ConversationMemory, history_to_prompt  # noqa: E402

A = (35.2280, 128.6811)
B = (35.1900, 128.5700)


def _turn(text, center=A, answer="답변", tools=("find_nearby_convenience_stores",)):
    return {"user_text": text, "search_center": center, "radius_m": 500, "answer": answer, "tools": list(tools)}


class MemoryTest(unittest.TestCase):
    def test_add_and_recent(self):
        store = {}
        mem = ConversationMemory(store)
        for i in range(7):
            mem.add_turn(user_text=f"q{i}", search_center=A, radius_m=500, answer=f"a{i}", tools=[])
        self.assertEqual(len(mem.turns), 5)                      # 최대 5개 저장
        self.assertEqual([t["user_text"] for t in mem.recent()], ["q4", "q5", "q6"])  # 최근 3개
        self.assertIs(store["location_agent_memory"], store["location_agent_memory"])
        mem.clear()
        self.assertEqual(mem.turns, [])

    def test_filters_by_center(self):
        mem = ConversationMemory({})
        mem.add_turn(user_text="A 위치 질문", search_center=A, radius_m=500, answer="x", tools=[])
        mem.add_turn(user_text="B 위치 질문", search_center=B, radius_m=500, answer="y", tools=[])
        self.assertEqual([t["user_text"] for t in mem.recent(search_center=A)], ["A 위치 질문"])
        self.assertEqual([t["user_text"] for t in mem.recent(search_center=(1, 1))], [])

    def test_truncates_long_text(self):
        mem = ConversationMemory({})
        mem.add_turn(user_text="가" * 1000, search_center=A, radius_m=500, answer="나" * 1000, tools=[])
        self.assertEqual(len(mem.turns[0]["user_text"]), 300)
        self.assertEqual(len(mem.turns[0]["answer"]), 300)

    def test_add_from_result(self):
        mem = ConversationMemory({})
        mem.add_from_result({"status": "rejected_input"})        # 잘못된 입력은 기억 안 함
        mem.add_from_result({
            "status": "ok", "user_text": "걸어서 몇 분?", "search_center": A, "resolved_radius_m": 500,
            "final_answer": None, "executed_tool_calls": [],
            "unsupported_requests": [{"request": "도보 이동시간", "reason": "r"}]})
        mem.add_from_result({
            "status": "ok", "user_text": "편의점?", "search_center": A, "resolved_radius_m": 500,
            "final_answer": {"text": "편의점 21개"}, "unsupported_requests": [],
            "executed_tool_calls": [{"tool": "find_nearby_convenience_stores", "executed": True},
                                    {"tool": "find_nearby_bus_stops", "executed": False}]})
        turns = mem.turns
        self.assertEqual(len(turns), 2)
        self.assertIn("도보 이동시간", turns[0]["answer"])
        self.assertEqual(turns[1]["tools"], ["find_nearby_convenience_stores"])

    def test_corrupted_store_reset(self):
        store = {"location_agent_memory": "깨진 값"}
        self.assertEqual(ConversationMemory(store).turns, [])

    def test_prompt_block(self):
        self.assertEqual(history_to_prompt(None), "")
        text = history_to_prompt([_turn("가장 가까운 편의점은?", answer="GS25 직선거리 154m")])
        self.assertIn("가장 가까운 편의점은?", text)
        self.assertIn("숫자를 다시 쓰지 말고", text)


class HistoryInAgentTest(unittest.TestCase):
    """이전 대화가 AI 프롬프트에 들어가되, 답변 검증은 이번 조회 결과로만 하는지."""

    def test_history_reaches_both_prompts(self):
        prompts = []
        plan = {"goals": ["버스"], "unsupported_requests": [],
                "tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "후속 질문"}]}

        def fake(**kwargs):
            prompts.append(kwargs["messages"][-1]["content"])
            if len(prompts) == 1:
                return {"message": {"content": json.dumps(plan, ensure_ascii=False)}}
            return {"message": {"content": json.dumps({"action": "answer", "answer": "버스정류장 정보는 위와 같습니다."},
                                                      ensure_ascii=False)}}

        history = [_turn("가장 가까운 편의점 알려줘", answer="GS25창원정우점, 직선거리 154m")]
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"}), mock.patch("ollama.chat", side_effect=fake):
            result = location_agent.run_location_agent("그럼 버스는?", A, 500, 10, history=history)
        self.assertEqual(len(prompts), 2)
        for p in prompts:
            self.assertIn("가장 가까운 편의점 알려줘", p)
        self.assertEqual(result["executed_tool_calls"][0]["tool"], "find_nearby_bus_stops")

    def test_numbers_from_history_not_allowed_in_answer(self):
        plan = {"goals": ["버스"], "unsupported_requests": [],
                "tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "r"}]}
        bad = {"action": "answer", "answer": "아까 편의점은 직선거리 154m였고 버스도 가깝습니다."}
        seq = [plan, bad, bad, bad]
        history = [_turn("편의점?", answer="GS25 직선거리 154m")]
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"}), \
             mock.patch("ollama.chat", side_effect=[{"message": {"content": json.dumps(x, ensure_ascii=False)}} for x in seq]):
            result = location_agent.run_location_agent("그럼 버스는?", A, 500, 10, history=history)
        self.assertEqual(result["final_answer"]["source"], "python_summary")
        self.assertIn("154", result["final_answer"]["rejected_reason"])

    def test_no_history_prompt_unchanged(self):
        self.assertEqual(location_agent._build_user_prompt("q", 500),
                         location_agent._build_user_prompt("q", 500, history=[]))
        self.assertNotIn("이전 대화", location_agent._build_user_prompt("q", 500))


if __name__ == "__main__":
    unittest.main()
