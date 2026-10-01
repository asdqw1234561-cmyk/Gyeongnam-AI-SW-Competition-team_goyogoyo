"""
agent/planner.py의 Agent Planner(계획 수립 -> 검증 -> 허용된 도구 실행) 단위 테스트.
ollama.chat과 services.region_data.get_all_changwon_regions를 모킹해서 실제 서버/CSV
없이도 결정적으로 검증한다.

    python -m unittest tests.test_planner -v
"""

import copy
import unittest
from unittest import mock

from agent import planner

FAKE_REGIONS = [
    {
        "region_id": rid,
        "region_name": name,
        "categories": {
            "교통": [
                {
                    "indicator_code": "bus_stop_count", "indicator_name": "버스정류장 수",
                    "value": str(bus), "unit": "개", "source": "s", "reference_date": "2026-01-01",
                    "data_status": "확보",
                }
            ],
            "의료": [
                {
                    "indicator_code": "hospital_count", "indicator_name": "병원 수",
                    "value": str(hosp), "unit": "개", "source": "s", "reference_date": "2026-01-01",
                    "data_status": "확보",
                }
            ],
            "생활편의": [
                {
                    "indicator_code": "convenience_store_count", "indicator_name": "편의점 수",
                    "value": None, "unit": "개", "source": None, "reference_date": None,
                    "data_status": "미확보",
                }
            ],
        },
    }
    for rid, name, bus, hosp in [
        ("CW-UICHANG", "의창구", 831, 262),
        ("CW-SEONGSAN", "성산구", 452, 398),
        ("CW-MASANHAPPO", "마산합포구", 760, 242),
        ("CW-MASANHOEWON", "마산회원구", 365, 256),
        ("CW-JINHAE", "진해구", 518, 200),
    ]
]


def _fake_chat_response(content: str) -> dict:
    return {"message": {"content": content}}


def _patched_regions():
    return mock.patch("agent.planner.get_all_changwon_regions", return_value=copy.deepcopy(FAKE_REGIONS))


class ToolGetAvailableIndicatorsTest(unittest.TestCase):
    def test_confirmed_and_unconfirmed_distinguished(self):
        result = planner.tool_get_available_indicators(FAKE_REGIONS)
        self.assertEqual(
            result,
            {"bus_stop_count": True, "hospital_count": True, "convenience_store_count": False},
        )


class ToolGetRegionIndicatorsTest(unittest.TestCase):
    def test_returns_values_for_confirmed_codes_only(self):
        result = planner.tool_get_region_indicators(FAKE_REGIONS, ["bus_stop_count", "convenience_store_count"])
        self.assertIn("bus_stop_count", result)
        self.assertNotIn("convenience_store_count", result, "미확보 지표는 조회 결과에 포함되면 안 됨")
        self.assertEqual(result["bus_stop_count"]["values"]["CW-UICHANG"], 831.0)


class ValidateAndNormalizePlanTest(unittest.TestCase):
    """요구사항 5: LLM의 계획을 그대로 신뢰하지 않고 Python이 검증한다."""

    def setUp(self):
        self.approved_weights = {"bus_stop_count": 70.0, "hospital_count": 30.0}
        self.available_codes = {"bus_stop_count", "hospital_count"}

    def test_valid_plan_accepted_and_normalized(self):
        plan = {
            "goals": ["교통 분석", "의료 분석"],
            "tool_calls": [
                {"tool": "get_available_indicators", "reason": "확인"},
                {
                    "tool": "get_region_indicators",
                    "indicator_codes": ["bus_stop_count", "hospital_count"],
                    "reason": "조회",
                },
                {
                    "tool": "calculate_region_scores",
                    "weights": {"bus_stop_count": 70, "hospital_count": 30},
                    "reason": "계산",
                },
            ],
        }
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["tool_calls"]), 3)
        self.assertEqual(result["tool_calls"][-1]["weights"], self.approved_weights)
        self.assertEqual(result["notes"], [])

    def test_disallowed_tool_name_rejected(self):
        plan = {"tool_calls": [{"tool": "run_python_code", "reason": "..."}]}
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("허용되지 않은 도구", result["reason"])

    def test_unsupported_indicator_code_rejected(self):
        plan = {
            "tool_calls": [
                {"tool": "get_region_indicators", "indicator_codes": ["monthly_rent_avg"], "reason": "..."}
            ]
        }
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("monthly_rent_avg", result["reason"])

    def test_unconfirmed_but_otherwise_valid_code_rejected(self):
        """허용된 지표(convenience_store_count)라도 현재 미확보 상태면 조회를 거부한다."""
        plan = {
            "tool_calls": [
                {"tool": "get_region_indicators", "indicator_codes": ["convenience_store_count"], "reason": "..."}
            ]
        }
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "rejected")

    def test_ai_proposed_weights_are_ignored_and_overwritten(self):
        """요구사항: 사용자 승인 없이 가중치를 변경할 수 없다 - AI가 다른 숫자를
        제안해도 Python은 승인된 가중치로 강제 치환한다."""
        plan = {
            "tool_calls": [
                {
                    "tool": "calculate_region_scores",
                    "weights": {"bus_stop_count": 10, "hospital_count": 90},  # AI가 임의로 바꾼 값
                    "reason": "계산",
                }
            ]
        }
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "ok")
        calc_call = next(c for c in result["tool_calls"] if c["tool"] == "calculate_region_scores")
        self.assertEqual(calc_call["weights"], self.approved_weights)
        self.assertTrue(any("무시" in n for n in result["notes"]))

    def test_missing_lookup_before_calculate_is_auto_inserted(self):
        """요구사항 5: 필요한 조회 작업이 누락되지 않았는지 - 빠졌으면 Python이 보완한다."""
        plan = {"tool_calls": [{"tool": "calculate_region_scores", "weights": self.approved_weights, "reason": "계산"}]}
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "ok")
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertEqual(tools, ["get_region_indicators", "calculate_region_scores"])
        self.assertTrue(any("보완" in n for n in result["notes"]))

    def test_missing_calculate_step_is_auto_added(self):
        plan = {"tool_calls": [{"tool": "get_available_indicators", "reason": "확인"}]}
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "ok")
        tools = [c["tool"] for c in result["tool_calls"]]
        self.assertIn("calculate_region_scores", tools)

    def test_duplicate_calculate_calls_rejected(self):
        plan = {
            "tool_calls": [
                {"tool": "calculate_region_scores", "weights": self.approved_weights, "reason": "a"},
                {"tool": "calculate_region_scores", "weights": self.approved_weights, "reason": "b"},
            ]
        }
        result = planner.validate_and_normalize_plan(plan, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "rejected")

    def test_empty_tool_calls_rejected(self):
        result = planner.validate_and_normalize_plan({"tool_calls": []}, self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "rejected")

    def test_non_dict_plan_rejected(self):
        result = planner.validate_and_normalize_plan("not a dict", self.approved_weights, self.available_codes)
        self.assertEqual(result["status"], "rejected")

    def test_non_dict_tool_call_rejected(self):
        result = planner.validate_and_normalize_plan(
            {"tool_calls": ["get_available_indicators"]}, self.approved_weights, self.available_codes
        )
        self.assertEqual(result["status"], "rejected")


class RunAgentPlanTest(unittest.TestCase):
    """run_agent_plan()은 regions를 한 번만 조회해 모든 도구가 같은 스냅샷을
    공유하게 하고, 검증을 통과한 계획만 실행한다."""

    def test_valid_ai_plan_is_executed_and_weights_preserved(self):
        """요구사항: 교통·의료 선택 시 필요한 조회 도구만 계획하고, 실제 도구 실행
        결과가 점수 계산에 사용되며, 승인된 가중치가 그대로 유지되는지."""
        approved = {"bus_stop_count": 70.0, "hospital_count": 30.0}
        plan_json = (
            '{"goals": ["교통 분석", "의료 분석"], '
            '"tool_calls": ['
            '{"tool": "get_available_indicators", "reason": "확인"}, '
            '{"tool": "get_region_indicators", "indicator_codes": ["bus_stop_count", "hospital_count"], "reason": "조회"}, '
            '{"tool": "calculate_region_scores", "weights": {"bus_stop_count": 999, "hospital_count": 1}, "reason": "계산"}'
            '], "unsupported_requests": []}'
        )
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = planner.run_agent_plan(
                selected_conditions=["교통", "의료"],
                confirmed_weights=approved,
                desired_region="창원시",
                candidate_count=3,
            )

        self.assertEqual(result["mode"], "ai_planned")
        self.assertEqual(result["approved_weights"], approved)
        self.assertEqual(result["weights_source"], "user_confirmed")
        self.assertEqual(result["score_result"]["status"], "ok")

        # 실제 계산에 쓰인 가중치가 AI가 제안한 엉뚱한 값(999/1)이 아니라 승인된
        # 70/30 그대로인지 확인한다.
        used = {uc["indicator_code"]: round(uc["weight"] * 100, 1) for uc in result["score_result"]["used_conditions"]}
        self.assertEqual(used, {"bus_stop_count": 70.0, "hospital_count": 30.0})

        # 실행 로그에 호출하지 않은 도구가 섞이지 않았는지.
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(
            executed_tools, ["get_available_indicators", "get_region_indicators", "calculate_region_scores"]
        )
        for entry in result["executed_tool_calls"]:
            self.assertTrue(entry["executed"])
            self.assertIsNone(entry["error"])

    def test_score_result_matches_direct_call_with_same_snapshot(self):
        """요구사항 9: Agent를 거쳐도 기존 추천 점수·순위가 바뀌지 않는지 -
        동일 데이터 스냅샷으로 직접 compute_region_scores_from_weights()를 호출한
        결과와 정확히 같아야 한다."""
        approved = {"bus_stop_count": 60.0, "hospital_count": 40.0}
        plan_json = (
            '{"tool_calls": ['
            '{"tool": "get_region_indicators", "indicator_codes": ["bus_stop_count", "hospital_count"], "reason": "r"}, '
            '{"tool": "calculate_region_scores", "weights": {"bus_stop_count": 60, "hospital_count": 40}, "reason": "r"}'
            ']}'
        )
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = planner.run_agent_plan(
                selected_conditions=["교통", "의료"],
                confirmed_weights=approved,
                desired_region="창원시",
                candidate_count=5,
            )

        from analysis.scoring import compute_region_scores_from_weights

        direct = compute_region_scores_from_weights(approved, 5, regions=copy.deepcopy(FAKE_REGIONS))
        self.assertEqual(
            [r["total_score"] for r in result["score_result"]["region_scores"]],
            [r["total_score"] for r in direct["region_scores"]],
        )
        self.assertEqual(
            [r["rank"] for r in result["score_result"]["region_scores"]],
            [r["rank"] for r in direct["region_scores"]],
        )

    def test_equal_weight_baseline_used_when_no_confirmed_weights(self):
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(
                '{"tool_calls": [{"tool": "get_available_indicators", "reason": "r"}]}'
            )
            result = planner.run_agent_plan(
                selected_conditions=["교통", "의료"],
                confirmed_weights=None,
                desired_region="창원시",
                candidate_count=3,
            )
        self.assertEqual(result["weights_source"], "equal_weight")
        self.assertEqual(result["approved_weights"], {"bus_stop_count": 50.0, "hospital_count": 50.0})
        self.assertEqual(result["score_result"]["status"], "ok")

    def test_unconfirmed_indicator_not_mistaken_for_available(self):
        """요구사항: 미확보(편의점) 지표를 확보된 데이터로 오인하지 않는지."""
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(
                '{"tool_calls": [{"tool": "get_available_indicators", "reason": "r"}]}'
            )
            result = planner.run_agent_plan(
                selected_conditions=["교통"], confirmed_weights=None, desired_region="창원시", candidate_count=3
            )
        self.assertEqual(result["available_indicators"]["convenience_store_count"], False)

    def test_malformed_json_falls_back_to_default_procedure(self):
        """요구사항 6,7: 잘못된 JSON도 안전하게 처리하고 기본 절차로 정상 진행."""
        approved = {"bus_stop_count": 100.0}
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response("이것은 JSON이 아닙니다")
            result = planner.run_agent_plan(
                selected_conditions=["교통"], confirmed_weights=approved, desired_region="창원시", candidate_count=3
            )
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIsNotNone(result["planner_error"])
        self.assertEqual(result["score_result"]["status"], "ok")
        used = {uc["indicator_code"] for uc in result["score_result"]["used_conditions"]}
        self.assertEqual(used, {"bus_stop_count"})

    def test_ollama_connection_failure_falls_back_and_still_recommends(self):
        """요구사항 7: Ollama 장애 시에도 기존 추천 기능이 중단되지 않아야 한다."""
        approved = {"bus_stop_count": 70.0, "hospital_count": 30.0}
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.side_effect = ConnectionError("서버 없음")
            result = planner.run_agent_plan(
                selected_conditions=["교통", "의료"],
                confirmed_weights=approved,
                desired_region="창원시",
                candidate_count=3,
            )
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIn("서버 없음", result["planner_error"])
        self.assertEqual(result["score_result"]["status"], "ok")
        self.assertIsNone(result["planned_tool_calls"])
        # 기본 절차 실행 로그도 실제로 get_available_indicators/get_region_indicators/
        # calculate_region_scores 전부를 순서대로 실행했어야 한다.
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertEqual(
            executed_tools, ["get_available_indicators", "get_region_indicators", "calculate_region_scores"]
        )

    def test_invalid_plan_falls_back_but_keeps_rejected_plan_for_display(self):
        """요구사항 8: 실행 기록이 실제 수행한 도구 호출과 일치해야 한다 - 검증에
        실패한 AI의 원래 계획은 '제안됐지만 실행되지 않은 계획'으로만 보존되고,
        executed_tool_calls에는 기본 절차만 담겨야 한다."""
        approved = {"bus_stop_count": 70.0, "hospital_count": 30.0}
        plan_json = '{"tool_calls": [{"tool": "run_arbitrary_python", "reason": "..."}]}'
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = planner.run_agent_plan(
                selected_conditions=["교통", "의료"],
                confirmed_weights=approved,
                desired_region="창원시",
                candidate_count=3,
            )
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIsNone(result["planner_error"])  # Ollama 자체는 성공했음
        self.assertTrue(any("검증하지 못해" in n for n in result["notes"]))
        self.assertEqual(result["planned_tool_calls"], [{"tool": "run_arbitrary_python", "reason": "..."}])
        executed_tools = [e["tool"] for e in result["executed_tool_calls"]]
        self.assertNotIn("run_arbitrary_python", executed_tools)
        self.assertIn("calculate_region_scores", executed_tools)

    def test_no_computable_conditions_returns_no_usable_conditions(self):
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response('{"tool_calls": [{"tool": "get_available_indicators"}]}')
            result = planner.run_agent_plan(
                selected_conditions=["교육", "안전"], confirmed_weights=None, desired_region="창원시", candidate_count=3
            )
        self.assertEqual(result["score_result"]["status"], "no_usable_conditions")

    def test_unsupported_requests_are_sanitized_not_trusted_blindly(self):
        plan_json = (
            '{"tool_calls": [{"tool": "get_available_indicators", "reason": "r"}], '
            '"unsupported_requests": [{"request": "주거비", "reason": "미확보"}, "bad-entry"]}'
        )
        approved = {"bus_stop_count": 100.0}
        with _patched_regions(), mock.patch("agent.planner.ollama.chat") as mock_chat:
            mock_chat.return_value = _fake_chat_response(plan_json)
            result = planner.run_agent_plan(
                selected_conditions=["교통"], confirmed_weights=approved, desired_region="창원시", candidate_count=3
            )
        self.assertEqual(result["unsupported_requests"], [{"request": "주거비", "reason": "미확보"}])


if __name__ == "__main__":
    unittest.main()
