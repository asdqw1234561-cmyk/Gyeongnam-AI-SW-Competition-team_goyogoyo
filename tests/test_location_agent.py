"""
agent/location_agent.py의 위치 기반 주변 시설 분석 Agent 단위 테스트.
ollama.chat을 모킹해서 실제 Ollama 서버 없이 계획 검증·실행 로직을 검증한다.

    python -m unittest tests.test_location_agent -v
"""

import unittest
from unittest import mock

from agent import location_agent

SEARCH_CENTER = (35.2280, 128.6811)  # 창원시청 부근 예시 좌표


def _fake_chat_response(content: str) -> dict:
    return {"message": {"content": content}}


class ExtractExplicitRadiusTest(unittest.TestCase):
    """요구사항 5: 자연어 반경(300m/500m/1km) 처리, 미지원 반경 명확히 안내."""

    def test_no_radius_mentioned(self):
        result = location_agent.extract_explicit_radius_m("주변 버스정류장 알려줘")
        self.assertFalse(result["found"])
        self.assertIsNone(result["radius_m"])

    def test_300m_recognized(self):
        result = location_agent.extract_explicit_radius_m("300m 안의 편의점 알려줘.")
        self.assertTrue(result["found"])
        self.assertEqual(result["radius_m"], 300)

    def test_500m_recognized(self):
        result = location_agent.extract_explicit_radius_m("이 위치에서 500m 안에 버스정류장이 몇 개 있어?")
        self.assertEqual(result["radius_m"], 500)

    def test_1km_recognized(self):
        result = location_agent.extract_explicit_radius_m("1km 안에 버스정류장이 몇 개야?")
        self.assertEqual(result["radius_m"], 1000)

    def test_1000m_recognized_as_1km(self):
        result = location_agent.extract_explicit_radius_m("1000미터 안에 뭐있어?")
        self.assertEqual(result["radius_m"], 1000)

    def test_unsupported_radius_detected_but_not_mapped(self):
        result = location_agent.extract_explicit_radius_m("200m 안에 뭐 있어?")
        self.assertTrue(result["found"])
        self.assertIsNone(result["radius_m"])
        self.assertIn("200", result["raw_text"])


class ValidateAndNormalizePlanTest(unittest.TestCase):
    """요구사항 7,9,10,11: AI 계획을 그대로 신뢰하지 않고 Python이 검증한다."""

    def test_valid_single_tool_accepted(self):
        plan = {
            "goals": ["주변 버스정류장 조회"],
            "tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "사용자가 요청함"}],
        }
        result = location_agent.validate_and_normalize_plan(plan, resolved_radius_m=500, ui_max_results=10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["radius_m"], 500)
        self.assertEqual(result["tool_calls"][0]["max_results"], 10)

    def test_disallowed_tool_rejected(self):
        plan = {"tool_calls": [{"tool": "run_python_code", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertEqual(result["status"], "rejected")

    def test_ai_proposed_radius_is_overridden(self):
        """요구사항 7: AI가 다른 반경을 제안해도 실제 적용 반경은 Python이 정한 값."""
        plan = {"tool_calls": [{"tool": "find_nearby_bus_stops", "radius_m": 9999, "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(plan, resolved_radius_m=300, ui_max_results=10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool_calls"][0]["radius_m"], 300)
        self.assertTrue(any("무시" in n for n in result["notes"]))

    def test_ai_proposed_coordinates_are_ignored_and_noted(self):
        """요구사항 6: AI가 좌표를 제안해도 무시하고 기록만 남긴다."""
        plan = {
            "tool_calls": [
                {"tool": "find_nearby_bus_stops", "lat": 1.0, "lon": 2.0, "reason": "x"}
            ]
        }
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("lat", result["tool_calls"][0])
        self.assertNotIn("lon", result["tool_calls"][0])
        self.assertTrue(any("좌표" in n for n in result["notes"]))

    def test_max_results_clamped_to_limit(self):
        plan = {"tool_calls": [{"tool": "find_nearby_bus_stops", "max_results": 9999, "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertEqual(result["tool_calls"][0]["max_results"], location_agent.MAX_RESULTS_LIMIT)

    def test_max_results_one_for_nearest_only_request(self):
        plan = {"tool_calls": [{"tool": "find_nearby_bus_stops", "max_results": 1, "reason": "가장 가까운 정류장만"}]}
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertEqual(result["tool_calls"][0]["max_results"], 1)

    def test_too_many_tool_calls_rejected(self):
        plan = {
            "tool_calls": [
                {"tool": "find_nearby_bus_stops", "reason": "a"},
                {"tool": "find_nearby_convenience_stores", "reason": "b"},
                {"tool": "compare_nearby_facilities", "reason": "c"},
                {"tool": "find_nearby_bus_stops", "max_results": 2, "reason": "d"},
                {"tool": "find_nearby_bus_stops", "max_results": 3, "reason": "e"},
            ]
        }
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertEqual(result["status"], "rejected")

    def test_duplicate_tool_call_deduplicated(self):
        """요구사항: 동일한 도구 호출이 불필요하게 반복되지 않도록 한다."""
        plan = {
            "tool_calls": [
                {"tool": "find_nearby_bus_stops", "reason": "a"},
                {"tool": "find_nearby_bus_stops", "reason": "b"},
            ]
        }
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertTrue(any("중복" in n for n in result["notes"]))

    def test_empty_tool_calls_with_no_reason_is_still_structurally_valid(self):
        """tool_calls가 비어도 구조 자체는 유효하다(지원 불가 판단은 orchestrator가 함)."""
        result = location_agent.validate_and_normalize_plan({"tool_calls": []}, 500, 10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool_calls"], [])

    def test_non_dict_plan_rejected(self):
        result = location_agent.validate_and_normalize_plan("not a dict", 500, 10)
        self.assertEqual(result["status"], "rejected")

    def test_compare_tool_ignores_radius_and_max_results_fields(self):
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "radius_m": 500, "max_results": 5, "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        self.assertNotIn("radius_m", result["tool_calls"][0])
        self.assertNotIn("max_results", result["tool_calls"][0])


class CallLocationPlannerTest(unittest.TestCase):
    def test_thinking_mode_disabled_to_avoid_empty_content(self):
        """실제 qwen3.5:4b 연동 테스트에서 think 모드가 켜져 있으면 생각 과정만으로
        토큰 예산을 다 써서 content가 빈 문자열로 돌아오는 경우를 확인했다 -
        think=False를 명시적으로 전달해야 한다."""
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response('{"tool_calls": []}')
            location_agent.call_location_planner("버스정류장 알려줘", 500)
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs.get("think"), False)


class RunLocationAgentTest(unittest.TestCase):
    """run_location_agent() 통합 테스트."""

    def test_bus_stop_request_selects_bus_tool(self):
        """요구사항 1: 버스정류장 요청 -> 버스정류장 도구 선택."""
        plan_json = (
            '{"goals": ["주변 버스정류장 조회"], '
            '"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "사용자가 버스정류장을 물음"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "이 위치에서 500m 안에 버스정류장이 몇 개 있어?", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "ai_planned")
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(executed_tools, ["find_nearby_bus_stops"])
        self.assertEqual(result["executed_tool_calls"][0]["result"]["status"], "ok")

    def test_convenience_request_selects_convenience_tool(self):
        """요구사항 2: 편의점 요청 -> 편의점 도구 선택."""
        plan_json = (
            '{"goals": ["주변 편의점 조회"], '
            '"tool_calls": [{"tool": "find_nearby_convenience_stores", "reason": "사용자가 편의점을 물음"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "주변 편의점도 찾아줘.", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(executed_tools, ["find_nearby_convenience_stores"])

    def test_both_facilities_request_queries_both(self):
        """요구사항 3: 두 시설 요청 -> 두 시설 모두 조회."""
        plan_json = (
            '{"goals": ["버스정류장과 편의점 함께 조회"], '
            '"tool_calls": ['
            '{"tool": "find_nearby_bus_stops", "reason": "a"}, '
            '{"tool": "find_nearby_convenience_stores", "reason": "b"}'
            '], "unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "여기에서 버스정류장과 편의점이 얼마나 있는지 같이 보여줘.",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed_tools = {e["tool"] for e in result["executed_tool_calls"]}
        self.assertEqual(executed_tools, {"find_nearby_bus_stops", "find_nearby_convenience_stores"})

    def test_nearest_stop_request_uses_max_results_one(self):
        """요구사항 4: 가장 가까운 정류장 요청 -> 실제 거리순 첫 결과."""
        plan_json = (
            '{"goals": ["가장 가까운 버스정류장 조회"], '
            '"tool_calls": [{"tool": "find_nearby_bus_stops", "max_results": 1, "reason": "가장 가까운 것만"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "가장 가까운 버스정류장이 어디야?", SEARCH_CENTER, ui_radius_m=1000, ui_max_results=10
            )
        entry = result["executed_tool_calls"][0]
        self.assertEqual(entry["max_results"], 1)
        self.assertEqual(entry["result"]["status"], "ok")
        self.assertLessEqual(len(entry["result"]["stops"]), 1)

    def test_explicit_radius_in_text_is_applied(self):
        """요구사항 5: 자연어 반경 300m가 실제로 적용되는지."""
        plan_json = '{"tool_calls": [{"tool": "find_nearby_convenience_stores", "reason": "x"}]}'
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "300m 안의 편의점 알려줘.", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["resolved_radius_m"], 300)
        self.assertEqual(result["radius_source"], "explicit_text")
        self.assertEqual(result["executed_tool_calls"][0]["radius_m"], 300)

    def test_no_explicit_radius_uses_ui_default(self):
        """요구사항 6: 반경 미명시 -> 화면 선택값 사용."""
        plan_json = '{"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "x"}]}'
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "주변 버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=1000, ui_max_results=10
            )
        self.assertEqual(result["resolved_radius_m"], 1000)
        self.assertEqual(result["radius_source"], "ui_default")

    def test_unsupported_radius_rejected_without_calling_ollama(self):
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            result = location_agent.run_location_agent(
                "200m 안에 버스정류장 있어?", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
            mock_chat.assert_not_called()
        self.assertEqual(result["status"], "rejected_input")
        self.assertIn("200m", result["message"])

    def test_ai_proposed_coordinates_do_not_change_search_location(self):
        """요구사항 7,8: AI가 임의 좌표를 제안해도 승인된 검색 중심만 사용."""
        plan_json = (
            '{"tool_calls": [{"tool": "find_nearby_bus_stops", "lat": 0.0, "lon": 0.0, "reason": "x"}]}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["search_center"], SEARCH_CENTER)
        # 실제로 실행된 조회가 적도(0,0) 근처가 아니라 창원시 데이터를 기준으로 했는지는
        # result status/message로 간접 확인(적도 좌표였다면 범위 밖 경고가 떴을 것).
        self.assertNotIn("창원시 범위", " ".join(n for n in result["notes"]))

    def test_disallowed_tool_rejected_and_falls_back(self):
        """요구사항 9: 허용되지 않은 도구 호출 차단 -> 기본 절차로 폴백."""
        plan_json = '{"tool_calls": [{"tool": "delete_all_files", "reason": "x"}]}'
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIsNone(result["planner_error"])
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(executed_tools, ["compare_nearby_facilities"])

    def test_malformed_json_falls_back(self):
        """요구사항 10: 잘못된 JSON 응답 처리."""
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response("이것은 JSON이 아닙니다")
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIsNotNone(result["planner_error"])
        self.assertEqual(result["executed_tool_calls"][0]["tool"], "compare_nearby_facilities")

    def test_too_many_tool_calls_falls_back(self):
        """요구사항 11: 과도한 도구 호출 차단."""
        plan_json = (
            '{"tool_calls": ['
            '{"tool": "find_nearby_bus_stops", "max_results": 1, "reason": "a"},'
            '{"tool": "find_nearby_bus_stops", "max_results": 2, "reason": "b"},'
            '{"tool": "find_nearby_bus_stops", "max_results": 3, "reason": "c"},'
            '{"tool": "find_nearby_bus_stops", "max_results": 4, "reason": "d"},'
            '{"tool": "find_nearby_bus_stops", "max_results": 5, "reason": "e"}'
            ']}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["mode"], "fallback_default")

    def test_actual_zero_vs_no_data_distinguished(self):
        """요구사항 12: 실제 0건과 데이터 미확보 구분 - 창원시에서 먼 좌표라면
        데이터 자체는 정상 조회되고 0건으로 나와야 한다(오류가 아님)."""
        plan_json = '{"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "x"}]}'
        far_away = (37.5665, 126.9780)  # 서울시청 부근
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", far_away, ui_radius_m=100, ui_max_results=10
            )
        entry = result["executed_tool_calls"][0]
        self.assertEqual(entry["result"]["status"], "ok")
        self.assertEqual(entry["result"]["total_count"], 0)

    def test_ollama_connection_failure_falls_back(self):
        """요구사항 13: Ollama 연결 실패 시 기본 조회 기능 정상 동작."""
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.side_effect = ConnectionError("서버 없음")
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIn("서버 없음", result["planner_error"])
        self.assertEqual(result["executed_tool_calls"][0]["tool"], "compare_nearby_facilities")
        self.assertEqual(result["executed_tool_calls"][0]["executed"], True)

    def test_execution_log_only_contains_actually_executed_tools(self):
        """요구사항 14: AI가 실행하지 않았는데 실행했다고 기록하지 않음 - 검증에
        실패한 AI의 원래 제안은 planned_tool_calls에만 남고 executed_tool_calls
        에는 실제로 실행된 기본 절차만 담겨야 한다."""
        plan_json = '{"tool_calls": [{"tool": "not_a_real_tool", "reason": "x"}]}'
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["planned_tool_calls"], [{"tool": "not_a_real_tool", "reason": "x"}])
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertNotIn("not_a_real_tool", executed_tools)

    def test_unsupported_commute_time_request_does_not_call_bus_tool_blindly(self):
        """요구사항: 지원하지 않는 질문에 관계없는 도구를 억지로 실행하지 않음."""
        plan_json = (
            '{"goals": [], "tool_calls": [], '
            '"unsupported_requests": [{"request": "버스 이동시간", '
            '"reason": "실제 대중교통 이동시간 데이터가 없습니다"}]}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "여기서 창원시청까지 버스로 몇 분 걸려?", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["mode"], "ai_planned")
        self.assertEqual(result["executed_tool_calls"], [])
        self.assertEqual(len(result["unsupported_requests"]), 1)
        self.assertIn("이동시간", result["unsupported_requests"][0]["request"])

    def test_housing_price_request_is_unsupported_not_fabricated(self):
        plan_json = (
            '{"goals": [], "tool_calls": [], '
            '"unsupported_requests": [{"request": "월세 40만원 이하 주택", '
            '"reason": "주거비·매물 데이터가 없습니다"}]}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "이 위치에서 월세 40만 원 이하인 집을 찾아줘.", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["executed_tool_calls"], [])
        self.assertTrue(result["unsupported_requests"])

    def test_empty_plan_with_nothing_useful_falls_back(self):
        plan_json = '{"goals": [], "tool_calls": [], "unsupported_requests": []}'
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
            )
        self.assertEqual(result["mode"], "fallback_default")


class ExtractRequestConstraintsTest(unittest.TestCase):
    """extract_request_constraints() - guardrail 제약조건 추출."""

    def test_bus_keyword_detected(self):
        result = location_agent.extract_request_constraints("500m 안에 버스정류장이 몇 개 있어?")
        self.assertIn("bus_stop", result["requested_facilities"])
        self.assertNotIn("convenience", result["requested_facilities"])

    def test_convenience_keyword_detected(self):
        result = location_agent.extract_request_constraints("500m 안에 편의점이 몇 개 있어?")
        self.assertIn("convenience", result["requested_facilities"])
        self.assertNotIn("bus_stop", result["requested_facilities"])

    def test_both_facilities_detected(self):
        result = location_agent.extract_request_constraints("500m 안에 버스정류장과 편의점이 얼마나 있어?")
        self.assertIn("bus_stop", result["requested_facilities"])
        self.assertIn("convenience", result["requested_facilities"])

    def test_no_facility_keywords(self):
        result = location_agent.extract_request_constraints("주변 시설 알려줘")
        self.assertEqual(result["requested_facilities"], [])

    def test_nearest_only_detected(self):
        result = location_agent.extract_request_constraints("가장 가까운 버스정류장이 어디야?")
        self.assertTrue(result["nearest_only"])

    def test_nearest_only_not_detected(self):
        result = location_agent.extract_request_constraints("500m 안에 버스정류장이 몇 개 있어?")
        self.assertFalse(result["nearest_only"])

    def test_comparison_keyword_detected(self):
        result = location_agent.extract_request_constraints("300m, 500m, 1km별로 시설 수를 비교해줘")
        self.assertTrue(result["wants_radius_comparison"])

    def test_comparison_keyword_radius_byeol(self):
        result = location_agent.extract_request_constraints("반경별 시설 현황을 비교해줘")
        self.assertTrue(result["wants_radius_comparison"])

    def test_comparison_not_detected_for_single_query(self):
        result = location_agent.extract_request_constraints("500m 안에 버스정류장이 몇 개 있어?")
        self.assertFalse(result["wants_radius_comparison"])

    def test_explicit_radius_extracted(self):
        result = location_agent.extract_request_constraints("300m 안의 편의점 알려줘")
        self.assertEqual(result["explicit_radius_m"], 300)

    def test_unsupported_radius_is_none(self):
        result = location_agent.extract_request_constraints("200m 안에 뭐 있어?")
        self.assertIsNone(result["explicit_radius_m"])


class ScopeCorrectionUnitTest(unittest.TestCase):
    """validate_and_normalize_plan() + constraints - 범위 보정 단위 테스트."""

    def _bus_constraints(self, nearest_only=False):
        return {
            "requested_facilities": ["bus_stop"],
            "explicit_radius_m": 500,
            "wants_radius_comparison": False,
            "nearest_only": nearest_only,
        }

    def _conv_constraints(self):
        return {
            "requested_facilities": ["convenience"],
            "explicit_radius_m": 500,
            "wants_radius_comparison": False,
            "nearest_only": False,
        }

    def _both_constraints(self):
        return {
            "requested_facilities": ["bus_stop", "convenience"],
            "explicit_radius_m": 500,
            "wants_radius_comparison": False,
            "nearest_only": False,
        }

    def _compare_constraints(self):
        return {
            "requested_facilities": ["bus_stop", "convenience"],
            "explicit_radius_m": None,
            "wants_radius_comparison": True,
            "nearest_only": False,
        }

    def test_compare_corrected_to_bus_for_bus_only_request(self):
        """시나리오 7: AI가 compare를 제안했는데 버스 단일 요청 → bus 도구로 보정."""
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._bus_constraints()
        )
        self.assertEqual(result["status"], "ok")
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("find_nearby_bus_stops", tools)
        self.assertNotIn("compare_nearby_facilities", tools)
        self.assertTrue(result["corrections"])
        self.assertEqual(result["corrections"][0]["original_tool"], "compare_nearby_facilities")

    def test_compare_corrected_to_conv_for_conv_only_request(self):
        """편의점 단일 요청에 compare → convenience 도구로 보정."""
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._conv_constraints()
        )
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("find_nearby_convenience_stores", tools)
        self.assertNotIn("compare_nearby_facilities", tools)
        self.assertTrue(result["corrections"])

    def test_compare_corrected_to_both_for_both_facilities_request(self):
        """시나리오 3: 두 시설 요청에 compare → 두 단일 도구로 보정."""
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._both_constraints()
        )
        tools = {c["tool"] for c in result["tool_calls"]}
        self.assertEqual(tools, {"find_nearby_bus_stops", "find_nearby_convenience_stores"})
        self.assertNotIn("compare_nearby_facilities", tools)

    def test_compare_allowed_when_comparison_requested(self):
        """시나리오 4: 반경별 비교 요청 → compare_nearby_facilities 그대로 허용."""
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._compare_constraints()
        )
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("compare_nearby_facilities", tools)
        self.assertEqual(result["corrections"], [])

    def test_bus_tool_corrected_when_only_convenience_requested(self):
        """시나리오 8: 편의점 요청에 bus 도구 제안 → convenience로 보정."""
        plan = {"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._conv_constraints()
        )
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("find_nearby_convenience_stores", tools)
        self.assertNotIn("find_nearby_bus_stops", tools)
        self.assertTrue(result["corrections"])

    def test_conv_tool_corrected_when_only_bus_requested(self):
        """버스 요청에 convenience 도구 제안 → bus로 보정."""
        plan = {"tool_calls": [{"tool": "find_nearby_convenience_stores", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._bus_constraints()
        )
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("find_nearby_bus_stops", tools)
        self.assertNotIn("find_nearby_convenience_stores", tools)
        self.assertTrue(result["corrections"])

    def test_nearest_only_forces_max_results_one(self):
        """시나리오 5/6: nearest_only=True → max_results 강제 1."""
        plan = {"tool_calls": [{"tool": "find_nearby_bus_stops", "max_results": 10, "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(
            plan, 500, 10, constraints=self._bus_constraints(nearest_only=True)
        )
        self.assertEqual(result["tool_calls"][0]["max_results"], 1)
        self.assertTrue(result["corrections"])

    def test_no_correction_without_constraints(self):
        """constraints=None이면 보정 없음 - 기존 동작 유지."""
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(plan, 500, 10)
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("compare_nearby_facilities", tools)
        self.assertEqual(result.get("corrections", []), [])

    def test_no_correction_when_no_facility_keywords(self):
        """시설 키워드 없으면 compare 보정 안 함."""
        constraints = {
            "requested_facilities": [],
            "explicit_radius_m": None,
            "wants_radius_comparison": False,
            "nearest_only": False,
        }
        plan = {"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}
        result = location_agent.validate_and_normalize_plan(plan, 500, 10, constraints=constraints)
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("compare_nearby_facilities", tools)
        self.assertEqual(result["corrections"], [])


class RunLocationAgentScopeCorrectionTest(unittest.TestCase):
    """run_location_agent() 통합 - 범위 보정 시나리오."""

    # ── 시나리오 1: 버스 단일 요청, AI가 올바로 bus 제안 ──────────────────────
    def test_bus_only_request_with_correct_ai_plan(self):
        """AI가 올바로 bus 도구를 제안 → 보정 없이 bus 실행."""
        plan_json = (
            '{"goals": ["버스정류장 조회"], '
            '"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "버스 요청"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "이 위치에서 500m 안에 버스정류장이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(executed, ["find_nearby_bus_stops"])
        self.assertEqual(result["corrections"], [])

    # ── 시나리오 2: 편의점 단일 요청, AI가 올바로 conv 제안 ────────────────────
    def test_conv_only_request_with_correct_ai_plan(self):
        """AI가 올바로 convenience 도구를 제안 → 보정 없이 convenience 실행."""
        plan_json = (
            '{"goals": ["편의점 조회"], '
            '"tool_calls": [{"tool": "find_nearby_convenience_stores", "reason": "편의점 요청"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "이 위치에서 500m 안에 편의점이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(executed, ["find_nearby_convenience_stores"])
        self.assertEqual(result["corrections"], [])

    # ── 시나리오 3: 두 시설 요청, AI가 두 단일 도구 제안 ──────────────────────
    def test_both_facilities_two_single_tools_no_compare(self):
        """두 시설 요청 → 두 단일 도구 실행, compare 실행 안 함."""
        plan_json = (
            '{"goals": ["버스+편의점 조회"], '
            '"tool_calls": ['
            '{"tool": "find_nearby_bus_stops", "reason": "a"}, '
            '{"tool": "find_nearby_convenience_stores", "reason": "b"}'
            '], "unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "500m 안에 버스정류장과 편의점이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = {e["tool"] for e in result["executed_tool_calls"]}
        self.assertEqual(executed, {"find_nearby_bus_stops", "find_nearby_convenience_stores"})
        self.assertNotIn("compare_nearby_facilities", executed)

    # ── 시나리오 3 변형: 두 시설 요청인데 AI가 compare 제안 → 보정 ──────────────
    def test_both_facilities_ai_proposes_compare_corrected_to_two_single(self):
        """AI가 두 시설 요청에 compare를 제안 → 두 단일 도구로 보정."""
        plan_json = (
            '{"goals": ["시설 비교"], '
            '"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "시설 조회"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "500m 안에 버스정류장과 편의점이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = {e["tool"] for e in result["executed_tool_calls"]}
        self.assertEqual(executed, {"find_nearby_bus_stops", "find_nearby_convenience_stores"})
        self.assertTrue(result["corrections"])

    # ── 시나리오 4: 반경별 비교 요청 → compare 사용 가능 ─────────────────────
    def test_comparison_request_allows_compare(self):
        """반경별 비교 명시 요청 → compare_nearby_facilities 보정 없이 실행."""
        plan_json = (
            '{"goals": ["반경별 비교"], '
            '"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "비교 요청"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "300m, 500m, 1km별 시설 수를 비교해줘",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertIn("compare_nearby_facilities", executed)
        self.assertEqual(result["corrections"], [])

    # ── 시나리오 5: 가장 가까운 버스정류장 → max_results=1 ─────────────────────
    def test_nearest_bus_stop_forces_max_results_one(self):
        """'가장 가까운' 버스정류장 → max_results=1 강제."""
        plan_json = (
            '{"goals": ["가장 가까운 버스"], '
            '"tool_calls": [{"tool": "find_nearby_bus_stops", "max_results": 10, "reason": "x"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "가장 가까운 버스정류장이 어디야?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        entry = result["executed_tool_calls"][0]
        self.assertEqual(entry["tool"], "find_nearby_bus_stops")
        self.assertEqual(entry["max_results"], 1)
        self.assertTrue(result["corrections"])

    # ── 시나리오 6: 가장 가까운 편의점 → max_results=1 ─────────────────────────
    def test_nearest_convenience_forces_max_results_one(self):
        """'가장 가까운' 편의점 → max_results=1 강제."""
        plan_json = (
            '{"goals": ["가장 가까운 편의점"], '
            '"tool_calls": [{"tool": "find_nearby_convenience_stores", "max_results": 10, "reason": "x"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "가장 가까운 편의점 알려줘",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        entry = result["executed_tool_calls"][0]
        self.assertEqual(entry["tool"], "find_nearby_convenience_stores")
        self.assertEqual(entry["max_results"], 1)

    # ── 시나리오 7: AI가 버스 단일 요청에 compare 제안 → 보정 ──────────────────
    def test_ai_proposes_compare_for_bus_only_request_gets_corrected(self):
        """AI가 버스 단일 요청에 compare_nearby_facilities 제안 → bus 도구로 보정."""
        plan_json = (
            '{"goals": [], '
            '"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "조회"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "이 위치에서 500m 안에 버스정류장이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertNotIn("compare_nearby_facilities", executed)
        self.assertIn("find_nearby_bus_stops", executed)
        self.assertTrue(result["corrections"])
        self.assertEqual(result["corrections"][0]["original_tool"], "compare_nearby_facilities")
        self.assertEqual(result["mode"], "ai_planned")

    # ── 시나리오 8: AI가 편의점 요청에 bus 제안 → 보정 ─────────────────────────
    def test_ai_proposes_bus_for_convenience_request_gets_corrected(self):
        """AI가 편의점 요청에 bus 도구 제안 → convenience로 보정."""
        plan_json = (
            '{"goals": [], '
            '"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "조회"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "이 위치에서 500m 안에 편의점이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertNotIn("find_nearby_bus_stops", executed)
        self.assertIn("find_nearby_convenience_stores", executed)
        self.assertTrue(result["corrections"])

    # ── 시나리오 16: 보정 발생 시 corrections 기록 ──────────────────────────────
    def test_corrections_recorded_when_scope_correction_applied(self):
        """보정 발생 시 result['corrections']가 비어 있지 않아야 한다."""
        plan_json = (
            '{"goals": [], '
            '"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}], '
            '"unsupported_requests": []}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "500m 안에 버스정류장이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        self.assertTrue(result["corrections"])
        correction = result["corrections"][0]
        self.assertIn("original_tool", correction)
        self.assertIn("corrected_to", correction)
        self.assertIn("note", correction)

    # ── 시나리오 14: unsupported 요청에 관계없는 도구 억지 실행 안 함 ────────────
    def test_unsupported_request_no_forced_tool_execution(self):
        """지원하지 않는 요청 → 시설 도구 억지 실행하지 않음 (기존 테스트 유지)."""
        plan_json = (
            '{"goals": [], "tool_calls": [], '
            '"unsupported_requests": [{"request": "버스 이동시간", "reason": "데이터 없음"}]}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "여기서 창원시청까지 버스로 몇 분 걸려?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        self.assertEqual(result["executed_tool_calls"], [])
        self.assertEqual(result["mode"], "ai_planned")

    # ── 시나리오 15: executed_tool_calls에 실제 실행 도구만 기록 ───────────────
    def test_executed_tool_calls_only_contains_actually_executed(self):
        """보정 발생 시에도 executed_tool_calls에 원래 AI 제안 도구는 없음."""
        plan_json = (
            '{"tool_calls": [{"tool": "compare_nearby_facilities", "reason": "x"}]}'
        )
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "500m 안에 편의점이 몇 개 있어?",
                SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertNotIn("compare_nearby_facilities", executed_tools)
        self.assertIn("find_nearby_convenience_stores", executed_tools)

    # ── 결과 반환 구조 검증 ──────────────────────────────────────────────────────
    def test_result_always_has_constraints_and_corrections_fields(self):
        """run_location_agent() 반환에 항상 constraints와 corrections 포함."""
        plan_json = '{"tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "x"}]}'
        with mock.patch("agent.location_agent.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = location_agent.run_location_agent(
                "버스정류장 알려줘", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10,
            )
        self.assertIn("constraints", result)
        self.assertIn("corrections", result)
        self.assertIsInstance(result["corrections"], list)


class RealOllamaIntegrationSmokeTest(unittest.TestCase):
    """실제 qwen3.5:4b를 사용하는 통합 스모크 테스트 - 기본적으로 건너뛴다.
    LOCATION_AGENT_REAL_OLLAMA_TEST=1 환경변수가 설정됐을 때만 실행하며,
    실제 Ollama 서버가 떠 있어야 통과한다(단위 테스트 모음에는 포함되지 않음)."""

    @unittest.skipUnless(
        __import__("os").environ.get("LOCATION_AGENT_REAL_OLLAMA_TEST") == "1",
        "실제 Ollama 서버 연동 테스트는 기본적으로 건너뜀",
    )
    def test_real_bus_stop_request(self):
        result = location_agent.run_location_agent(
            "이 위치에서 500m 안에 버스정류장이 몇 개 있어?", SEARCH_CENTER, ui_radius_m=500, ui_max_results=10
        )
        self.assertIn(result["mode"], ("ai_planned", "fallback_default"))


if __name__ == "__main__":
    unittest.main()
