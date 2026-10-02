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

    def test_app_input_form_uses_select_options(self):
        """희망지역·직장/학교 위치·주거비 예산은 자유입력이 아니라 선택지다."""
        at = self._run("app.py")
        labels = {s.label: s.options for s in at.selectbox}
        region = next(opts for label, opts in labels.items() if label.startswith("희망 지역"))
        self.assertEqual(region[0], "창원시 전체")
        self.assertIn("창원시 진해구", region)
        self.assertIn("선택 안 함", labels["직장 또는 학교 위치"])
        self.assertIn("월세 30~50만원", labels["주거비 예산"])
        # 세 항목은 더 이상 text_input이 아니다
        self.assertFalse(any(t.label.startswith(("희망 지역", "직장", "주거비")) for t in at.text_input))

    def test_app_submit_saves_selected_values(self):
        """선택값 저장: '선택 안 함'은 기존 자유입력의 빈 값과 같게 ""로 저장된다."""
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"), default_timeout=TIMEOUT_S).run()
        at.selectbox[0].select("창원시 성산구")  # 희망 지역
        at.selectbox[1].select("창원시 의창구")  # 직장 또는 학교 위치
        # 주거비 예산은 기본값("선택 안 함") 그대로
        no_questions = {"message": {"content": '{"questions": []}'}}
        with mock.patch("ollama.chat", return_value=no_questions):
            at.button[0].click().run()  # "다음 단계로"
        self.assertEqual([e.value for e in at.exception], [])
        saved = at.session_state["initial_input"]
        self.assertEqual(saved["희망지역"], "창원시 성산구")
        self.assertEqual(saved["직장/학교 위치"], "창원시 의창구")
        self.assertEqual(saved["주거비 예산"], "")
        self.assertEqual(at.session_state["stage"], "done")

    def test_done_screen_shows_candidate_roles_and_critic(self):
        """결과 화면: 종합 1위 하나가 아니라 최적·균형 후보와 Critic 점검이 실제 데이터로 표시된다."""
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"), default_timeout=TIMEOUT_S).run()
        at.selectbox[1].select("창원시 의창구")  # 직장 또는 학교 위치 - 점수에 반영 못 하는 입력
        no_questions = {"message": {"content": '{"questions": [], "tool_calls": []}'}}
        with mock.patch("ollama.chat", return_value=no_questions):
            at.button[0].click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertEqual(at.session_state["stage"], "done")
        review = at.session_state["agent_execution_log"]["candidate_review"]
        self.assertEqual(review["status"], "ok")
        markdown = " ".join(m.value for m in at.markdown)
        self.assertIn("정착 후보군", markdown)
        self.assertIn("최적 · 성산구", markdown)
        self.assertIn("균형 · 의창구", markdown)
        captions = " ".join(c.value for c in at.caption)
        self.assertIn("가성비** — 산출 불가", captions)
        warnings = " ".join(w.value for w in at.warning)
        self.assertIn("직장/학교 위치", warnings)  # coverage 점검이 반영 못 한 입력을 밝힘

    @staticmethod
    def _fake_ollama(**kwargs):
        """가중치 해석 프롬프트에는 '지원하지 않는 지표' 응답, 그 외(추가질문·계획)에는 빈 응답."""
        system = kwargs["messages"][0]["content"]
        if "가중치 조정" in system:
            content = '{"type": "unsupported", "weights": null, "message": "주거비 데이터는 미확보입니다."}'
        else:
            content = '{"questions": [], "tool_calls": []}'
        return {"message": {"content": content}}

    def _not_used_captions(self, at: AppTest) -> list[str]:
        return [c.value for c in at.caption if c.value.startswith("· ")]

    def _finish_with_equal_weights(self, at: AppTest) -> None:
        with mock.patch("ollama.chat", side_effect=self._fake_ollama):
            next(b for b in at.button if b.label == "동일 가중치로 진행하기").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertEqual(at.session_state["stage"], "done")

    def test_not_used_inputs_no_duplicate_for_extra_request_ratio(self):
        """D1: 추가 요청사항의 비율을 적용하지 못했을 때 'AI 추가질문(가중치 확인) 답변'으로 중복 표시하지 않는다."""
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"), default_timeout=TIMEOUT_S).run()
        at.multiselect[0].set_value(["교통", "의료"])
        at.text_area[0].input("교통 70%, 주거비 30%")
        with mock.patch("ollama.chat", side_effect=self._fake_ollama):
            at.button[0].click().run()
        self.assertEqual(at.session_state["stage"], "weight_confirm")
        self._finish_with_equal_weights(at)
        captions = self._not_used_captions(at)
        self.assertTrue(any(c.startswith("· 추가 요청사항(") for c in captions))
        self.assertFalse(any("AI 추가질문(가중치 확인) 답변" in c for c in captions))

    def test_not_used_inputs_shows_followup_weight_answer(self):
        """AI 추가질문(가중치 확인)에 답했는데 적용하지 못한 경우에는 그 줄이 그대로 나온다."""
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"), default_timeout=TIMEOUT_S).run()
        at.multiselect[0].set_value(["교통", "의료"])
        with mock.patch("ollama.chat", side_effect=self._fake_ollama):
            at.button[0].click().run()
        self.assertEqual(at.session_state["stage"], "followup")
        at.text_input[0].input("교통 70%, 주거비 30%")
        with mock.patch("ollama.chat", side_effect=self._fake_ollama):
            next(b for b in at.button if b.label == "답변 제출").click().run()
        self.assertEqual(at.session_state["stage"], "weight_confirm")
        self._finish_with_equal_weights(at)
        self.assertTrue(any("AI 추가질문(가중치 확인) 답변" in c for c in self._not_used_captions(at)))

    def test_user_page(self):
        at = self._run("pages/user.py")
        self.assertTrue(any("관심 위치" in t.value for t in at.title))
        self.assertTrue(any("AI에게 주변 생활시설 분석 요청하기" in s.value for s in at.subheader))

    def test_government_page(self):
        at = self._run("pages/government.py")
        self.assertTrue(any("시설 현황" in t.value for t in at.title))
        # 원본 지표 표와 가상 시뮬레이션 섹션이 모두 그려졌는지
        self.assertTrue(any("가상 시설 증감 시뮬레이션" in h.value for h in at.header))

    @staticmethod
    def _location_result(mode: str, final_answer: dict) -> dict:
        return {
            "status": "ok", "message": None, "mode": mode, "user_text": "버스정류장 몇 개야?",
            "search_center": SEARCH_CENTER, "resolved_radius_m": 500, "radius_source": "ui_default",
            "goals": ["주변 버스정류장 조회"], "unsupported_requests": [], "planned_tool_calls": None,
            "executed_tool_calls": [], "planner_error": None, "constraints": {}, "corrections": [],
            "notes": [], "agent_steps": [], "final_answer": final_answer, "review_error": None,
        }

    def test_user_page_renders_rejected_mcp_answer_as_python_summary(self):
        """N2: MCP 반복형 답변이 숫자 검증에 실패하면 Python 요약과 버린 사유가 보인다."""
        final = {"text": "- 반경 500m(직선거리) 안 버스정류장: 14개.", "source": "python_summary",
                 "rejected_reason": "조회 결과에 없는 개수(50개)를 사용했습니다."}
        at = self._run("pages/user.py", {"search_center": SEARCH_CENTER,
                                         "location_agent_result": self._location_result("ai_agent", final)})
        self.assertTrue(any("조회 결과 요약" in i.value and "50개" in i.value for i in at.info))
        self.assertFalse(any("AI 답변" in s.value for s in at.success))

    def test_user_page_renders_verified_answer_for_both_paths(self):
        final = {"text": "500m 안에 버스정류장 14개가 있습니다.", "source": "ai_verified", "rejected_reason": None}
        for mode in ("ai_agent", "ai_planned"):
            at = self._run("pages/user.py", {"search_center": SEARCH_CENTER,
                                             "location_agent_result": self._location_result(mode, final)})
            self.assertTrue(any("AI 답변" in s.value for s in at.success), mode)

if __name__ == "__main__":
    unittest.main()
