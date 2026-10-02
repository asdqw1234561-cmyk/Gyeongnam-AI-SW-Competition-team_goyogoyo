"""
agent/llm.py 사용량 제한(남용 방지) 테스트. 실제 LLM을 호출하지 않는다.

    python -m unittest tests.test_llm -v
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import llm  # noqa: E402


# 이 파일은 계획형 경로(Ollama/Claude CLI 계획 -> 결과 검토 루프)를 검증한다.
# 위치 Agent 기본값(claude_agent: claude -p + MCP)이 실제 CLI를 부르지 않도록 고정한다.
_backend_patch = mock.patch.dict("os.environ", {"LOCATION_AGENT_BACKEND": "ollama"})


def setUpModule():
    _backend_patch.start()


def tearDownModule():
    _backend_patch.stop()

OK_RESPONSE = {"message": {"content": "{}"}}
USER_MSG = [{"role": "system", "content": "S" * 9000}, {"role": "user", "content": "질문"}]


class LimitTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"})
        self.env.start()
        for key in ("LLM_MAX_CALLS_PER_SESSION", "LLM_MAX_CALLS_PER_DAY", "LLM_MAX_INPUT_CHARS"):
            os.environ.pop(key, None)
        llm._daily_counter.update(date=None, count=0)
        self.backend = mock.patch("ollama.chat", return_value=OK_RESPONSE)
        self.backend_mock = self.backend.start()

    def tearDown(self):
        self.backend.stop()
        self.env.stop()
        llm._daily_counter.update(date=None, count=0)

    def _with_session(self, state):
        return mock.patch.object(llm, "_session_state", return_value=state)

    def test_limit_error_is_runtime_error(self):
        # agent 코드의 `except Exception` 폴백이 그대로 동작해야 한다
        self.assertTrue(issubclass(llm.LLMLimitError, RuntimeError))

    def test_session_limit(self):
        os.environ["LLM_MAX_CALLS_PER_SESSION"] = "2"
        state = {}
        with self._with_session(state):
            llm.chat(model="m", messages=USER_MSG)
            llm.chat(model="m", messages=USER_MSG)
            with self.assertRaises(llm.LLMLimitError) as ctx:
                llm.chat(model="m", messages=USER_MSG)
        self.assertIn("2회", str(ctx.exception))
        self.assertEqual(self.backend_mock.call_count, 2)   # 제한 후에는 백엔드를 부르지 않음
        self.assertEqual(state[llm._SESSION_KEY], 2)

    def test_daily_limit_across_sessions(self):
        os.environ["LLM_MAX_CALLS_PER_DAY"] = "3"
        for _ in range(3):
            with self._with_session({}):                  # 새로고침 = 새 세션
                llm.chat(model="m", messages=USER_MSG)
        with self._with_session({}):
            with self.assertRaises(llm.LLMLimitError) as ctx:
                llm.chat(model="m", messages=USER_MSG)
        self.assertIn("오늘", str(ctx.exception))

    def test_daily_counter_resets_next_day(self):
        os.environ["LLM_MAX_CALLS_PER_DAY"] = "1"
        llm._daily_counter.update(date="2000-01-01", count=999)
        with self._with_session({}):
            llm.chat(model="m", messages=USER_MSG)          # 날짜가 바뀌었으니 다시 허용
        self.assertEqual(llm._daily_counter["count"], 1)

    def test_zero_means_unlimited(self):
        os.environ["LLM_MAX_CALLS_PER_SESSION"] = "0"
        os.environ["LLM_MAX_CALLS_PER_DAY"] = "0"
        with self._with_session({}):
            for _ in range(50):
                llm.chat(model="m", messages=USER_MSG)
        self.assertEqual(self.backend_mock.call_count, 50)

    def test_invalid_env_uses_default(self):
        os.environ["LLM_MAX_CALLS_PER_SESSION"] = "abc"
        self.assertEqual(llm._limit("LLM_MAX_CALLS_PER_SESSION", 30), 30)

    def test_input_length_limit_ignores_system(self):
        os.environ["LLM_MAX_INPUT_CHARS"] = "100"
        llm.chat(model="m", messages=USER_MSG)              # system 9000자는 세지 않음
        with self.assertRaises(llm.LLMLimitError) as ctx:
            llm.chat(model="m", messages=[{"role": "user", "content": "가" * 101}])
        self.assertIn("100자", str(ctx.exception))

    def test_outside_streamlit_not_counted(self):
        os.environ["LLM_MAX_CALLS_PER_SESSION"] = "1"
        os.environ["LLM_MAX_CALLS_PER_DAY"] = "1"
        with self._with_session(None):                    # 테스트·스크립트 실행
            for _ in range(5):
                llm.chat(model="m", messages=USER_MSG)
        self.assertEqual(llm._daily_counter["count"], 0)

    def test_failed_call_still_counted(self):
        os.environ["LLM_MAX_CALLS_PER_SESSION"] = "1"
        self.backend_mock.side_effect = ConnectionError("down")
        with self._with_session({}):
            with self.assertRaises(ConnectionError):
                llm.chat(model="m", messages=USER_MSG)
            with self.assertRaises(llm.LLMLimitError):
                llm.chat(model="m", messages=USER_MSG)

    def test_backend_label_and_usage(self):
        self.assertEqual(llm.backend_label(), "로컬 Ollama")
        os.environ["LLM_BACKEND"] = "claude_cli"
        self.assertEqual(llm.backend_label(), "Claude(로컬 CLI)")
        with self._with_session({llm._SESSION_KEY: 4}):
            usage = llm.get_usage()
        self.assertEqual(usage["session_used"], 4)
        self.assertEqual(usage["session_limit"], 30)

    def test_real_session_state_detection_outside_streamlit(self):
        self.assertIsNone(llm._session_state())


class AgentFallbackOnLimitTest(unittest.TestCase):
    """제한에 걸려도 각 agent 기능이 예외 없이 기본 절차로 넘어가는지."""

    def setUp(self):
        self.patch = mock.patch.object(
            llm, "_check_and_count",
            side_effect=llm.LLMLimitError("이번 접속에서 사용할 수 있는 AI 호출 횟수(30회)를 모두 사용했습니다."),
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_weight_feedback(self):
        from agent import ollama_agent
        result = ollama_agent.interpret_weight_feedback("의료 80, 교통 20")
        self.assertEqual(result["status"], "ollama_error")    # 기존 상태 코드 유지
        self.assertIn("30회", result["message"])

    def test_followup_plan(self):
        from agent import ollama_agent
        plan = ollama_agent.plan_followup_questions(
            {"희망지역": "창원시", "중요 생활조건": ["교통", "의료"], "원하는 후보 개수": 3})
        self.assertIsNotNone(plan["ai_questions_error"])
        self.assertIn("30회", plan["ai_questions_error"])

    def test_planner(self):
        from agent import planner
        result = planner.run_agent_plan(["교통", "의료"], None, "창원시", 3)
        self.assertEqual(result["mode"], "fallback_default")
        self.assertIn("30회", result["planner_error"])

    def test_location_agent(self):
        from agent import location_agent
        result = location_agent.run_location_agent(
            "500m 안 편의점 알려줘", (35.2280, 128.6811), 500, 10)
        self.assertEqual(result["mode"], "fallback_default")
        self.assertTrue(result["executed_tool_calls"])       # 기본 조회는 그대로 실행


if __name__ == "__main__":
    unittest.main()
