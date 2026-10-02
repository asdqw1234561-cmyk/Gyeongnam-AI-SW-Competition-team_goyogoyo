"""
Streamlit 화면 스모크 테스트 - app.py / pages/user.py / pages/government.py가
첫 화면을 예외 없이 그리는지 streamlit.testing.v1.AppTest로 확인한다.

버튼을 누르지 않는 범위만 다루며, 첫 화면에서 실제 AI(Ollama·claude CLI)가 호출되면
실패하도록 막아 둔다(CLAUDE.md: Agent는 버튼 클릭 때만 실행). folium 지도는 AppTest에서
실제로 그려지지 않으므로 예외 없이 지나가는지만 본다.

    python -m unittest tests.test_pages_smoke -v
"""

import os
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

# AppTest.from_file()은 상대경로를 호출한 파일(tests/) 기준으로 풀기 때문에 프로젝트 루트 기준 절대경로로 넘긴다.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT_S = 120
SEARCH_CENTER = (35.2280, 128.6811)


def _no_ai_calls():
    """첫 화면 렌더링 중 AI 호출이 일어나면 즉시 실패시킨다."""
    return (
        mock.patch("ollama.chat", side_effect=AssertionError("첫 화면에서 Ollama가 호출됨")),
        mock.patch(
            "agent.location_agent.subprocess.run",
            side_effect=AssertionError("첫 화면에서 claude CLI가 호출됨"),
        ),
    )


class PagesFirstRenderTest(unittest.TestCase):
    def _run(self, path: str, session_state: dict | None = None) -> AppTest:
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, path), default_timeout=TIMEOUT_S)
        for key, value in (session_state or {}).items():
            at.session_state[key] = value
        ollama_patch, cli_patch = _no_ai_calls()
        with ollama_patch, cli_patch:
            at.run()
        self.assertEqual(
            [e.value for e in at.exception], [], f"{path} 첫 화면 렌더링 중 예외 발생"
        )
        return at

    def test_app_input_stage(self):
        at = self._run("app.py")
        self.assertTrue(any("경남 이주자" in t.value for t in at.title))
        self.assertEqual(at.session_state["stage"], "input")

    def test_user_page(self):
        at = self._run("pages/user.py")
        self.assertTrue(any("관심 위치" in t.value for t in at.title))
        self.assertTrue(any("AI에게 주변 생활시설 분석 요청하기" in s.value for s in at.subheader))

    def test_government_page(self):
        at = self._run("pages/government.py")
        self.assertTrue(any("시설 현황" in t.value for t in at.title))
        # 원본 지표 표와 가상 시뮬레이션 섹션이 모두 그려졌는지
        self.assertTrue(any("가상 시설 증감 시뮬레이션" in h.value for h in at.header))

    def test_user_page_renders_agent_result_with_verification_warning(self):
        """저장된 위치 Agent 결과(답변 숫자 검증 실패)를 다시 그릴 때 경고와 답변이 함께 보인다."""
        result = {
            "status": "ok", "message": None, "mode": "ai_agent", "user_text": "버스정류장 몇 개야?",
            "search_center": SEARCH_CENTER, "resolved_radius_m": 500, "radius_source": "ui_default",
            "goals": ["주변 버스정류장 조회"], "unsupported_requests": [], "planned_tool_calls": None,
            "executed_tool_calls": [], "planner_error": None, "constraints": {}, "corrections": [],
            "notes": ["AI 답변의 일부 수치(50)를 실제 조회 결과에서 확인하지 못했습니다."],
            "agent_answer": "정류장은 50개입니다.",
            "answer_verification": {"status": "unverified_numbers", "unverified": ["50"]},
            "agent_steps": [],
        }
        at = self._run("pages/user.py", {"search_center": SEARCH_CENTER, "location_agent_result": result})
        self.assertTrue(any("확인하지 못했습니다" in w.value for w in at.warning))
        self.assertTrue(any("50개" in s.value for s in at.success))


if __name__ == "__main__":
    unittest.main()
