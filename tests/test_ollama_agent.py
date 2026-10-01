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
    def test_mixed_supported_and_unsupported_indicator_rejects_whole_response(self, mock_chat):
        """회귀 테스트: '의료 60%, 주거비 40%'가 의료 60%(->정규화 시 의료 100%)로
        조용히 축소 적용되면 안 된다. 미지원 지표가 섞여 있으면 weights는 아예
        None이어야 하고(승인 버튼 자체가 뜨지 않음), type은 'unsupported'로
        재입력을 유도해야 한다."""
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 60, "monthly_rent_avg": 40}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 60%, 주거비 40%")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "unsupported")
        self.assertIsNone(result["weights"])
        self.assertIn("monthly_rent_avg", result["message"])
        self.assertIn("다시 비율을 지정", result["message"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_all_indicators_unsupported_also_rejects_whole_response(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"monthly_rent_avg": 100}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("주거비 100%")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "unsupported")
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


class WeightSumValidationTest(unittest.TestCase):
    """요구사항 2: 비율 합계가 100%가 아니면 임의로 정규화하지 않고 되묻는다."""

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_single_indicator_80_percent_asks_where_remainder_goes(self, mock_chat):
        """'의료 80%' 단독 -> 나머지 20%를 묻는다(조용히 의료 100%로 바뀌면 안 됨)."""
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 80}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 80%")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "ask_clarification")
        self.assertIsNone(result["weights"])
        self.assertIn("20", result["message"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_two_indicators_summing_to_100_is_accepted(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 80, "bus_stop_count": 20}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 80%, 교통 20%로 비교해줘")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "set_weights")
        self.assertEqual(result["weights"], {"hospital_count": 80.0, "bus_stop_count": 20.0})

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_convenience_and_transport_summing_to_100_is_accepted(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"convenience_store_count": 70, "bus_stop_count": 30}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("편의점 70%, 교통 30%로 비교해줘")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "set_weights")
        self.assertEqual(result["weights"], {"convenience_store_count": 70.0, "bus_stop_count": 30.0})

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_sum_over_100_asks_for_reconfirmation(self, mock_chat):
        """'의료 60%, 교통 60%' -> 합계 120%를 안내하고 재확인을 요청한다."""
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 60, "bus_stop_count": 60}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 60%, 교통 60%로 비교해줘")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "ask_clarification")
        self.assertIsNone(result["weights"])
        self.assertIn("120", result["message"])
        self.assertIn("100%", result["message"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_three_indicators_summing_to_100_is_accepted(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": '
            '{"hospital_count": 50, "bus_stop_count": 30, "convenience_store_count": 20}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 50%, 교통 30%, 생활편의 20%")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "set_weights")


class WeightNumericValidationTest(unittest.TestCase):
    """요구사항 3: NaN/Infinity/음수/100 초과/bool 등 숫자 검증 강화."""

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_nan_string_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": "NaN"}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 이상한 값")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_infinity_string_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": "Infinity"}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 무한대")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_negative_infinity_number_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": -Infinity}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 음의 무한대")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_over_100_single_value_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 150}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 150%")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_all_zero_weights_rejected(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": 0, "bus_stop_count": 0}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료 0%, 교통 0%")
        self.assertEqual(result["status"], "invalid_response")

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_boolean_value_not_treated_as_0_or_1(self, mock_chat):
        """JSON true/false가 파이썬 bool로 들어오면 float(True)==1.0으로 조용히
        통과해버릴 수 있으므로 명시적으로 거부해야 한다."""
        mock_chat.return_value = _fake_response(
            '{"type": "set_weights", "weights": {"hospital_count": true, "bus_stop_count": 20}, "message": null}'
        )
        result = ollama_agent.interpret_weight_feedback("의료는 예, 교통 20%")
        self.assertEqual(result["status"], "invalid_response")


class PlanWeightQuestionTest(unittest.TestCase):
    """plan_weight_question()은 Ollama를 호출하지 않는 결정적 로직이므로 ollama.chat을
    모킹할 필요 없이 바로 검증한다."""

    def test_two_computable_conditions_generates_weight_question(self):
        plan = ollama_agent.plan_weight_question({"중요 생활조건": ["교통", "의료"]})
        self.assertIsNotNone(plan)
        self.assertEqual(plan["conditions"], ["교통", "의료"])
        self.assertEqual(plan["indicator_codes"], ["bus_stop_count", "hospital_count"])
        self.assertIn("교통", plan["text"])
        self.assertIn("의료", plan["text"])
        self.assertIn("100%", plan["text"])

    def test_three_computable_conditions_example_sums_to_100(self):
        plan = ollama_agent.plan_weight_question(
            {"중요 생활조건": ["교통", "의료", "생활편의(마트/편의점)"]}
        )
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan["conditions"]), 3)
        self.assertEqual(len(plan["indicator_codes"]), 3)

    def test_single_computable_condition_returns_none(self):
        plan = ollama_agent.plan_weight_question({"중요 생활조건": ["교통"]})
        self.assertIsNone(plan)

    def test_zero_computable_conditions_returns_none(self):
        plan = ollama_agent.plan_weight_question({"중요 생활조건": ["교육", "안전"]})
        self.assertIsNone(plan)

    def test_no_selected_conditions_returns_none(self):
        plan = ollama_agent.plan_weight_question({"중요 생활조건": []})
        self.assertIsNone(plan)

    def test_explicit_ratio_already_in_extra_request_skips_question(self):
        plan = ollama_agent.plan_weight_question(
            {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 70%, 의료 30%로 해주세요"}
        )
        self.assertIsNone(plan)

    def test_single_percent_mention_still_asks(self):
        """퍼센트 표현이 하나뿐이면(두 조건 사이 비율로 보기 부족) 그대로 질문한다."""
        plan = ollama_agent.plan_weight_question(
            {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 70% 정도면 좋겠어요"}
        )
        self.assertIsNotNone(plan)


class PlanFollowupQuestionsTest(unittest.TestCase):
    """plan_followup_questions()는 plan_weight_question()(Ollama 미호출) +
    generate_followup_questions()(Ollama 호출, 모킹)를 합친다."""

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_weight_question_takes_first_slot_and_leaves_one_for_ai(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"questions": ["창원시 내에서 선호하는 주거 형태가 있으신가요?"]}'
        )
        plan = ollama_agent.plan_followup_questions({"중요 생활조건": ["교통", "의료"]})
        self.assertIsNotNone(plan["weight_question"])
        self.assertEqual(plan["questions"][0], plan["weight_question"]["text"])
        self.assertEqual(len(plan["questions"]), 2)
        mock_chat.assert_called_once()

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_groundless_ai_question_is_omitted_not_replaced(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"questions": ["가족 구성이 어떻게 되시나요?", "최대 통근시간은 어느 정도인가요?"]}'
        )
        plan = ollama_agent.plan_followup_questions({"중요 생활조건": ["교통", "의료"]})
        # 가중치 질문 1개만 남고, 근거 없는 AI 질문 2개는 생략된다(대체 문구로 바꿔치기하지 않음).
        self.assertEqual(plan["questions"], [plan["weight_question"]["text"]])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_single_condition_has_no_weight_question_but_can_still_ask_ai_questions(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"questions": ["창원시 내 어느 구를 가장 선호하시나요?"]}'
        )
        plan = ollama_agent.plan_followup_questions({"중요 생활조건": ["교통"]})
        self.assertIsNone(plan["weight_question"])
        self.assertEqual(plan["questions"], ["창원시 내 어느 구를 가장 선호하시나요?"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_ollama_failure_propagates_as_runtime_error(self, mock_chat):
        mock_chat.side_effect = ConnectionError("서버 없음")
        with self.assertRaises(RuntimeError):
            ollama_agent.plan_followup_questions({"중요 생활조건": ["교통"]})


if __name__ == "__main__":
    unittest.main()
