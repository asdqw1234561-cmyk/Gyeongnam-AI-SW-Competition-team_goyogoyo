"""
agent/ollama_agent.py의 interpret_weight_feedback() 단위 테스트.
ollama.chat을 모킹해서 실제 Ollama 서버 없이 파싱·검증 로직만 검증한다.

    python -m unittest tests.test_ollama_agent -v
"""

import unittest
from unittest import mock

from agent import ollama_agent


def _fake_response(content: str) -> dict:
    return {"message": {"content": content}}


class InterpretWeightFeedbackTest(unittest.TestCase):
    def test_empty_text_does_not_call_ollama(self):
        with mock.patch("agent.ollama_agent.ollama.chat") as mock_chat:
            result = ollama_agent.interpret_weight_feedback("   ")
        mock_chat.assert_not_called()
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_set_weights_parsed_and_kept_exact(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 80, "bus_stop_count": 20}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 80%, 교통 20%로 바꿔줘")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "set_weights")
        self.assertEqual(result["weights"], {"hospital_count": 80.0, "bus_stop_count": 20.0})

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_ask_clarification_has_no_weights(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "ask_clarification", "weights": null, "message": "몇 %로 할까요?"}'
        )
        result = ollama_agent.interpret_weight_feedback("의료가 더 중요해")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "ask_clarification")
        self.assertIsNone(result["weights"])
        self.assertIn("%", result["message"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_unsupported_for_unavailable_data(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "unsupported", "weights": null, "message": "주거비 데이터는 아직 확보되지 않았습니다."}'
        )
        result = ollama_agent.interpret_weight_feedback("월세가 저렴한 곳으로")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "unsupported")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_unsupported_indicator_code_is_dropped_not_whole_response(self, mock_chat):
        """허용 안 된 코드(monthly_rent_avg)는 그 항목만 버리고, 유효한 hospital_count는 살린다."""
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 60, "monthly_rent_avg": 40}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 60%, 주거비 40%")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["weights"], {"hospital_count": 60.0})

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_all_weights_invalid_rejects_whole_response(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"monthly_rent_avg": 100}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("주거비 100%")
        self.assertEqual(result["status"], "invalid_response")
        self.assertIsNone(result["weights"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_negative_weight_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": -10}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 -10%")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_non_numeric_weight_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": "많이"}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 많이")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_malformed_json_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response("이건 JSON이 아닙니다")
        result = ollama_agent.interpret_weight_feedback("아무 말")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_unknown_type_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response('{"type": "do_something_else", "weights": null, "message": null}')
        result = ollama_agent.interpret_weight_feedback("아무 말")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat", side_effect=ConnectionError("서버 없음"))
    def test_ollama_connection_failure_returns_ollama_error(self, mock_chat):
        result = ollama_agent.interpret_weight_feedback("의료 80%, 교통 20%")
        self.assertEqual(result["status"], "ollama_error")
        self.assertIsNone(result["weights"])
        self.assertIn("실패", result["message"])


if __name__ == "__main__":
    unittest.main()
