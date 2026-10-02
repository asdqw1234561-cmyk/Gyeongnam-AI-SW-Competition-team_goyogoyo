"""
agent/llm_json.py 단위 테스트 + 예전 탐욕적 정규식(\\{.*\\})으로는 실패하던 응답이
각 Agent에서 정상 처리되는지 확인한다(실제 AI 호출 없음).

    python -m unittest tests.test_llm_json -v
"""

import unittest
from unittest import mock

from agent import location_agent, ollama_agent, planner
from agent.llm_json import extract_json_object, sanitize_goals, sanitize_unsupported_requests


class ExtractJsonObjectTest(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_surrounded_by_explanation(self):
        text = '계획입니다:\n```json\n{"tool_calls": []}\n```\n이상입니다.'
        self.assertEqual(extract_json_object(text), {"tool_calls": []})

    def test_two_objects_returns_first(self):
        """예전 정규식은 첫 '{'~마지막 '}'를 통째로 파싱해 실패했다."""
        text = '{"a": 1}\n추가 설명 {"b": 2}'
        self.assertEqual(extract_json_object(text), {"a": 1})

    def test_braces_in_prose_before_json(self):
        text = '예시 {이건 JSON 아님} 실제: {"goals": ["x"]}'
        self.assertEqual(extract_json_object(text), {"goals": ["x"]})

    def test_nested_object(self):
        text = '{"weights": {"bus_stop_count": 70, "hospital_count": 30}, "type": "set_weights"}'
        self.assertEqual(extract_json_object(text)["weights"]["bus_stop_count"], 70)

    def test_no_json(self):
        self.assertIsNone(extract_json_object("JSON이 아닙니다"))
        self.assertIsNone(extract_json_object(""))
        self.assertIsNone(extract_json_object(None))

    def test_broken_json(self):
        self.assertIsNone(extract_json_object('{"a": 1,'))

    def test_array_is_not_returned(self):
        self.assertIsNone(extract_json_object('[1, 2, 3]'))


class SanitizeTest(unittest.TestCase):
    def test_goals_limits(self):
        goals = sanitize_goals([" a ", "", "b" * 150] + ["g"] * 20)
        self.assertEqual(goals[0], "a")
        self.assertEqual(len(goals[1]), 100)
        self.assertEqual(len(goals), 9)  # 앞 10개만 보고, 그중 빈 문자열 1개 제외
        self.assertEqual(sanitize_goals("not a list"), [])

    def test_unsupported_default_and_custom_length(self):
        raw = [{"request": "r" * 200, "reason": "x" * 300}, "bad", {"request": "  "}]
        default = sanitize_unsupported_requests(raw)
        self.assertEqual(len(default), 1)
        self.assertEqual(len(default[0]["request"]), 100)
        self.assertEqual(len(default[0]["reason"]), 200)
        self.assertEqual(len(sanitize_unsupported_requests(raw, request_max_len=150)[0]["request"]), 150)

    def test_existing_per_module_limits_preserved(self):
        """기존 동작 유지: planner는 100자, location_agent는 150자."""
        self.assertEqual(location_agent._UNSUPPORTED_REQUEST_MAX_LEN, 150)


class AgentsAcceptPreviouslyFailingResponsesTest(unittest.TestCase):
    def test_planner_call_with_two_json_objects(self):
        raw = '{"tool_calls": [{"tool": "get_available_indicators"}]} 참고: {"note": 1}'
        with mock.patch("agent.planner.ollama.chat", return_value={"message": {"content": raw}}):
            plan = planner.call_planner(["교통"], {"bus_stop_count": 100.0}, "창원시", {"bus_stop_count": True})
        self.assertEqual(plan["tool_calls"][0]["tool"], "get_available_indicators")

    def test_location_planner_with_explanation_braces(self):
        raw = '분석 {요약} 결과: {"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "a"}]}'
        with mock.patch("agent.location_agent.ollama.chat", return_value={"message": {"content": raw}}):
            plan = location_agent.call_location_planner("버스정류장", 500)
        self.assertEqual(plan["tool_calls"][0]["tool"], "find_nearby_bus_stops")

    def test_weight_feedback_messages_preserved(self):
        with mock.patch("agent.ollama_agent.ollama.chat", return_value={"message": {"content": "숫자 없음"}}):
            self.assertIn("JSON을 찾지 못했습니다", ollama_agent.interpret_weight_feedback("의료 80%")["message"])
        with mock.patch("agent.ollama_agent.ollama.chat", return_value={"message": {"content": "{깨짐"}}):
            self.assertIn("올바른 JSON 형식이 아닙니다", ollama_agent.interpret_weight_feedback("의료 80%")["message"])

    def test_weight_feedback_with_trailing_object(self):
        raw = '{"type": "set_weights", "weights": {"hospital_count": 80, "bus_stop_count": 20}, "message": null} {"x": 1}'
        with mock.patch("agent.ollama_agent.ollama.chat", return_value={"message": {"content": raw}}):
            result = ollama_agent.interpret_weight_feedback("의료 80%, 교통 20%")
        self.assertEqual(result["type"], "set_weights")
        self.assertEqual(result["weights"], {"hospital_count": 80.0, "bus_stop_count": 20.0})


if __name__ == "__main__":
    unittest.main()
