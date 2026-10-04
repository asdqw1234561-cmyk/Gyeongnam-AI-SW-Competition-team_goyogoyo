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


class ThinkingModeDisabledTest(unittest.TestCase):
    """qwen3.5의 think 모드가 켜져 있으면 생각 과정만으로 토큰을 다 써서 content가
    빈 문자열로 올 수 있다 - 두 Ollama 호출 모두 think=False를 전달해야 한다."""

    def test_generate_followup_questions(self):
        with mock.patch("agent.ollama_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_response('{"questions": []}')
            ollama_agent.generate_followup_questions({"희망지역": "창원시"})
        self.assertIs(mock_chat.call_args.kwargs.get("think"), False)

    def test_interpret_weight_feedback(self):
        with mock.patch("agent.ollama_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_response('{"type": "ask_clarification", "weights": null, "message": "?"}')
            ollama_agent.interpret_weight_feedback("의료가 더 중요해")
        self.assertIs(mock_chat.call_args.kwargs.get("think"), False)


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

    def test_explicit_ratio_in_extra_request_no_longer_gates_this_function(self):
        """요구사항 변경: "이미 비율이 있는지" 판단은 이제 plan_weight_question()이
        아니라 plan_followup_questions()가 interpret_weight_feedback()으로 실제
        검증한 뒤 내린다. 이 함수를 단독으로 호출하면 추가 요청사항 내용과 무관하게
        계산 가능한 조건 개수만 본다."""
        plan = ollama_agent.plan_weight_question(
            {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 70%, 의료 30%로 해주세요"}
        )
        self.assertIsNotNone(plan)

    def test_single_percent_mention_still_asks(self):
        plan = ollama_agent.plan_weight_question(
            {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 70% 정도면 좋겠어요"}
        )
        self.assertIsNotNone(plan)


class LooksLikeExplicitWeightRequestTest(unittest.TestCase):
    """_looks_like_explicit_weight_request()는 "검증을 시도할 가치가 있는가"만
    보는 사전 필터다 - 최종 유효성 판단이 아니다(요구사항: 퍼센트 기호 개수만으로
    정상 비율이라고 판단하지 않음)."""

    def test_two_percent_mentions_triggers_prefilter(self):
        self.assertTrue(
            ollama_agent._looks_like_explicit_weight_request("교통 70%, 의료 30%로 해주세요")
        )

    def test_single_percent_mention_does_not_trigger(self):
        self.assertFalse(ollama_agent._looks_like_explicit_weight_request("교통 70% 정도면 좋겠어요"))

    def test_empty_text_does_not_trigger(self):
        self.assertFalse(ollama_agent._looks_like_explicit_weight_request(""))


class PlanFollowupQuestionsTest(unittest.TestCase):
    """plan_followup_questions()는 plan_weight_question()(Ollama 미호출),
    generate_followup_questions()(Ollama 호출, 모킹), interpret_weight_feedback()
    (Ollama 호출, 모킹)를 합쳐서 최초 입력 제출 시점의 계획을 만든다."""

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_weight_question_takes_first_slot_and_leaves_one_for_ai(self, mock_chat):
        mock_chat.return_value = _fake_response(
            '{"questions": ["창원시 내에서 선호하는 주거 형태가 있으신가요?"]}'
        )
        plan = ollama_agent.plan_followup_questions({"중요 생활조건": ["교통", "의료"]})
        self.assertIsNotNone(plan["weight_question"])
        self.assertIsNone(plan["initial_weight_interpretation"])
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
    def test_ai_question_generation_failure_degrades_gracefully(self, mock_chat):
        """요구사항 8: AI 추가질문 생성이 실패해도 전체 흐름을 막지 않는다 - 더 이상
        예외를 던지지 않고 questions를 비운 채 ai_questions_error를 채워 돌려준다."""
        mock_chat.side_effect = ConnectionError("서버 없음")
        plan = ollama_agent.plan_followup_questions({"중요 생활조건": ["교통"]})
        self.assertEqual(plan["questions"], [])
        self.assertIsNotNone(plan["ai_questions_error"])
        self.assertIn("서버 없음", plan["ai_questions_error"])

    def test_valid_explicit_ratio_in_extra_request_is_interpreted_and_skips_weight_question(self):
        """요구사항 1,2,6: 추가 요청사항에 명확한 비율이 있으면 interpret_weight_feedback()
        으로 실제 해석하고, 가중치 확인 질문은 다시 만들지 않는다(단, 가중치 외
        AI 추가질문 생성은 별개 Ollama 호출로 그대로 남은 슬롯만큼 시도된다)."""
        with mock.patch("agent.ollama_agent.ollama.chat") as mock_chat:
            mock_chat.side_effect = [
                _fake_response(
                    '{"type": "set_weights", "weights": {"bus_stop_count": 70, "hospital_count": 30}, '
                    '"message": null}'
                ),
                _fake_response('{"questions": []}'),
            ]
            plan = ollama_agent.plan_followup_questions(
                {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 70%, 의료 30%로 해주세요"}
            )
        self.assertIsNone(plan["weight_question"])
        self.assertIsNotNone(plan["initial_weight_interpretation"])
        self.assertEqual(plan["initial_weight_interpretation"]["status"], "ok")
        self.assertEqual(plan["initial_weight_interpretation"]["type"], "set_weights")
        self.assertEqual(
            plan["initial_weight_interpretation"]["weights"],
            {"bus_stop_count": 70.0, "hospital_count": 30.0},
        )
        self.assertEqual(mock_chat.call_count, 2)

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_invalid_ratio_in_extra_request_is_not_silently_ignored(self, mock_chat):
        """요구사항 4,5: 퍼센트 기호가 있다고 바로 정상 비율로 취급하지 않는다 -
        합계가 100%가 아니면 ask_clarification으로 분류되고, 그래도 같은 가중치
        질문을 다시 만들지는 않는다(weight_confirm 단계에서 재확인/동일 가중치
        선택지로 이어진다)."""
        mock_chat.return_value = _fake_response(
            '{"type": "ask_clarification", "weights": null, "message": "합이 120%입니다."}'
        )
        plan = ollama_agent.plan_followup_questions(
            {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 60%, 의료 60%"}
        )
        self.assertIsNone(plan["weight_question"])
        self.assertEqual(plan["initial_weight_interpretation"]["type"], "ask_clarification")

    def test_single_percent_mention_in_extra_request_does_not_trigger_interpretation(self):
        """퍼센트 기호가 하나뿐이면 비율로 보기 부족하므로 해석을 시도하지 않고
        기존처럼 가중치 확인 질문을 만든다(Ollama를 호출하지 않아도 되는 경로)."""
        with mock.patch("agent.ollama_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_response('{"questions": []}')
            plan = ollama_agent.plan_followup_questions(
                {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 위주로 봐주세요 70%"}
            )
        self.assertIsNone(plan["initial_weight_interpretation"])
        self.assertIsNotNone(plan["weight_question"])

    @mock.patch("agent.ollama_agent.ollama.chat")
    def test_unsupported_condition_in_extra_request_ratio_is_reported_not_ignored(self, mock_chat):
        """요구사항 4: 미지원 조건이 섞인 비율도 임의로 무시하지 않고 그대로
        초기 해석 결과에 담아 반환한다."""
        mock_chat.return_value = _fake_response(
            '{"type": "unsupported", "weights": null, "message": '
            '"월세 데이터는 아직 확보되지 않아 반영할 수 없습니다."}'
        )
        plan = ollama_agent.plan_followup_questions(
            {"중요 생활조건": ["교통", "의료"], "추가 요청사항": "교통 50%, 월세 50%"}
        )
        self.assertIsNone(plan["weight_question"])
        self.assertEqual(plan["initial_weight_interpretation"]["type"], "unsupported")


class DirectionFeedbackParseTest(unittest.TestCase):
    """G3: 숫자 없는 방향성 요청은 방향만 담긴 adjust_direction으로 검증된다(숫자 없음)."""

    def _interpret(self, content: str) -> dict:
        with mock.patch("agent.ollama_agent.ollama.chat", return_value=_fake_response(content)):
            return ollama_agent.interpret_weight_feedback("병원을 더 중요하게 봐줘")

    def test_valid_direction(self):
        result = self._interpret(
            '{"type": "adjust_direction", "weights": null, '
            '"directions": {"hospital_count": "increase"}, "strength": "strong", "message": null}'
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["type"], "adjust_direction")
        self.assertIsNone(result["weights"])
        self.assertEqual(result["directions"], {"hospital_count": "increase"})
        self.assertEqual(result["strength"], "strong")

    def test_direction_value_is_normalized_and_strength_defaults(self):
        result = self._interpret(
            '{"type": "adjust_direction", "directions": {"bus_stop_count": " Decrease "}}'
        )
        self.assertEqual(result["directions"], {"bus_stop_count": "decrease"})
        self.assertEqual(result["strength"], "normal")

    def test_unsupported_indicator_rejects_whole_request(self):
        result = self._interpret(
            '{"type": "adjust_direction", '
            '"directions": {"hospital_count": "increase", "rent": "decrease"}}'
        )
        self.assertEqual(result["type"], "unsupported")
        self.assertIsNone(result["weights"])
        self.assertNotIn("directions", result)

    def test_bad_direction_value_is_invalid(self):
        for value in ('"up"', "30", "null", "true"):
            result = self._interpret(
                '{"type": "adjust_direction", "directions": {"hospital_count": %s}}' % value
            )
            self.assertEqual(result["status"], "invalid_response", value)

    def test_missing_directions_is_invalid(self):
        for body in ('{"type": "adjust_direction"}', '{"type": "adjust_direction", "directions": {}}',
                     '{"type": "adjust_direction", "directions": ["hospital_count"]}'):
            self.assertEqual(self._interpret(body)["status"], "invalid_response", body)

    def test_prompt_tells_ai_not_to_make_numbers_for_directions(self):
        prompt = ollama_agent.WEIGHT_FEEDBACK_SYSTEM_PROMPT
        self.assertIn('"adjust_direction"', prompt)
        self.assertIn("숫자는\n   절대 만들지 마세요", prompt)


if __name__ == "__main__":
    unittest.main()
