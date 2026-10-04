"""
공용 설명 경로 테스트: candidate_review -> explanation_facts -> Python 기본 설명 -> (선택) AI 다듬기 1회 -> 검증
-> 실패 시 기본 설명. 최초 추천과 피드백 재평가가 같은 경로를 쓰는지도 확인한다. 실제 LLM 호출 없음.

    python -m unittest tests.test_unified_explanation -v
"""

import json
import os
import unittest
from unittest import mock

from agent import planner, planner_loop
from analysis import feedback
from analysis.candidates import build_candidate_set
from analysis.explanation_facts import build_explanation_facts, render_explanation
from analysis.scoring import compute_region_scores_from_weights

from tests import changwon_fixture

# 규칙 테스트는 경남 확장 이전 창원 5개 구 시설 수(고정값)로 시나리오를 재현한다(tests/changwon_fixture.py).
setUpModule = changwon_fixture.start
tearDownModule = changwon_fixture.stop

EQUAL = {"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1}
ALL3 = ["교통", "의료", "생활편의(마트/편의점)"]


def _resp(obj):
    return {"message": {"content": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)}}


def _inputs(weights=EQUAL, count=3):
    score = compute_region_scores_from_weights(weights, count)
    return score, build_candidate_set(score, ["주거비 예산"])


def _explain(llm_side_effect, weights=EQUAL):
    score, review = _inputs(weights)
    with mock.patch("agent.llm.chat", side_effect=llm_side_effect) as m:
        res = planner_loop.explain_from_facts(score_result=score, candidate_review=review, model="m")
    return res, m, render_explanation(build_explanation_facts(score, review))


class DeterministicExplanationTest(unittest.TestCase):
    """Python 기본 설명은 explanation_facts만으로 항상 사실대로 만든다."""

    def test_contains_required_elements(self):
        score, review = _inputs()
        text = render_explanation(build_explanation_facts(score, review))
        for expected in ("현재 확보된 교통·의료·생활편의 기준",
                         "최적 후보는 성산구입니다", "균형 후보는 의창구입니다", "대안 후보는 의창구입니다",
                         "가성비 후보는 산출할 수 없습니다",
                         "성산구의 강점은 의료, 생활편의이고, 약점은 교통입니다",
                         "Critic이 처음 고른 마산합포구는 의창구에 비해 평가에 쓴 모든 축에서 낮거나 같아",
                         "전체 6개 평가축 중 3개(교통·의료·생활편의)만 확보",
                         "미확보 평가축(주거비, 교육, 직장 접근성)은 데이터가 없어 판단에 포함하지 않았습니다",
                         "반영하지 못한 조건: 주거비 예산",
                         "판단 한계", "실제 거주 적합도를 확정하지 않습니다"):
            self.assertIn(expected, text)

    def test_deterministic(self):
        score, review = _inputs()
        facts = build_explanation_facts(score, review)
        self.assertEqual(render_explanation(facts), render_explanation(facts))

    def test_numbers_come_only_from_facts(self):
        score, review = _inputs()
        facts = build_explanation_facts(score, review)
        allowed = planner_loop._numbers_in(json.dumps(facts, ensure_ascii=False))
        self.assertEqual(planner_loop._numbers_in(render_explanation(facts)) - allowed, set())

    def test_self_paraphrase_passes_validator_across_weightings(self):
        """기본 설명을 그대로 돌려준 '다듬기'는 어떤 가중치에서도 검증을 통과해야 한다(검증기 오탐으로 AI가 늘 탈락하지 않게)."""
        for weights in (EQUAL, {"bus_stop_count": 50, "hospital_count": 25, "convenience_store_count": 25},
                        {"bus_stop_count": 1}, {"hospital_count": 80, "bus_stop_count": 20},
                        {"hospital_count": 1, "convenience_store_count": 1}):
            score, review = _inputs(weights, 5)
            facts = build_explanation_facts(score, review)
            base = render_explanation(facts)
            self.assertEqual(planner_loop.validate_paraphrase(base, base, facts), (True, ""), weights)


class ParaphraseOutcomeTest(unittest.TestCase):
    """AI 다듬기는 1회만, 실패하면 기본 설명 그대로."""

    def test_normal_llm_response(self):
        score, review = _inputs()
        base = render_explanation(build_explanation_facts(score, review))
        good = base.replace("Agent가 고른 정착 후보입니다.", "이주 예정자께 권하는 정착 후보입니다.")
        res, m, _ = _explain([_resp({"answer": good})])
        self.assertEqual(m.call_count, 1)
        self.assertEqual(m.call_args.kwargs["messages"][-1]["content"], f"[기본 설명]\n{base}")
        self.assertIs(m.call_args.kwargs["think"], False)
        self.assertEqual(res["final_answer"]["source"], "ai_paraphrase")
        self.assertEqual(res["final_answer"]["text"], good)
        self.assertEqual(res["final_answer"]["deterministic_text"], base)

    def test_llm_timeout(self):
        res, m, base = _explain(TimeoutError("timed out"))
        self.assertEqual(m.call_count, 1)
        self.assertEqual(res["final_answer"]["source"], "deterministic")
        self.assertEqual(res["final_answer"]["text"], base)
        self.assertIn("timed out", res["review_error"])

    def test_empty_response(self):
        for content in ("", "   ", '{"answer": ""}', "not json"):
            res, m, base = _explain([_resp(content)])
            self.assertEqual(m.call_count, 1)
            self.assertEqual(res["final_answer"]["source"], "deterministic", content)
            self.assertEqual(res["final_answer"]["text"], base)
            self.assertIn("빈 응답", res["final_answer"]["rejected_reason"])

    def test_validator_failure_no_retry(self):
        score, review = _inputs()
        base = render_explanation(build_explanation_facts(score, review))
        cases = {
            "새 숫자": base.replace("72.9점", "75.0점"),
            "강약 뒤집기": base.replace("약점은 교통입니다", "약점은 의료입니다"),
            "역할 바꾸기": base.replace("균형 후보는 의창구입니다", "균형 후보는 진해구입니다"),
            "내용 누락": base.split("\n- 데이터 범위")[0],
            "미확보 축 단정": base + "\n성산구는 주거비가 저렴합니다.",
        }
        for name, paraphrase in cases.items():
            res, m, _ = _explain([_resp({"answer": paraphrase})])
            self.assertEqual(m.call_count, 1, name)
            self.assertEqual(res["final_answer"]["source"], "deterministic", name)
            self.assertEqual(res["final_answer"]["text"], base, name)
            self.assertTrue(res["final_answer"]["rejected_reason"], name)


class SamePathTest(unittest.TestCase):
    """최초 추천(정상·AI 계획 실패)과 피드백 재평가가 모두 explain_from_facts를 쓴다."""

    PLAN = {"tool_calls": [{"tool": "get_available_indicators", "reason": "r"},
                           {"tool": "calculate_region_scores", "weights": EQUAL, "reason": "r"}]}

    def _run_initial(self, responses):
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"}), \
                mock.patch("ollama.chat", side_effect=responses), \
                mock.patch.object(planner_loop, "explain_from_facts", wraps=planner_loop.explain_from_facts) as spy:
            result = planner.run_agent_plan(ALL3, EQUAL, "창원시", 3, unscored_inputs=["주거비 예산"])
        return result, spy

    def test_initial_uses_shared_path_and_shows_deterministic_when_llm_keeps_failing(self):
        wrong = {"answer": "현재 확보된 기준에서 진해구가 최적 후보입니다."}
        result, spy = self._run_initial([_resp(self.PLAN), _resp({"action": "answer", "answer": "아무 설명"}), _resp(wrong)])
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(result["final_answer"]["source"], "deterministic")
        self.assertIn("최적 후보는 성산구입니다", result["final_answer"]["text"])

    def test_initial_planner_failure_still_explains(self):
        result, spy = self._run_initial([ConnectionError("down"), ConnectionError("down")])
        self.assertEqual(result["mode"], "fallback_default")
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(result["final_answer"]["source"], "deterministic")
        self.assertIn("최적 후보는 성산구입니다", result["final_answer"]["text"])

    def test_feedback_uses_shared_path_with_new_candidates(self):
        plan = feedback.adjust_weights({c: 100 / 3 for c in EQUAL},
                                       [{"indicator_code": "bus_stop_count", "direction": "increase", "strength": "strong"}])
        out = feedback.reevaluate(plan["after"], 3, ["주거비 예산"])
        with mock.patch("agent.llm.chat", side_effect=TimeoutError("t")), \
                mock.patch.object(planner_loop, "explain_from_facts", wraps=planner_loop.explain_from_facts) as spy:
            res = planner_loop.explain_candidates(score_result=out["score_result"], candidate_review=out["candidate_review"],
                                                  selected_conditions=ALL3, model="m")
        self.assertEqual(spy.call_count, 1)
        text = res["final_answer"]["text"]
        self.assertIn("최적 후보는 의창구입니다", text)
        self.assertIn("대안 후보는 성산구입니다", text)
        self.assertIn("의창구의 종합점수는 교통 한 축에 크게 기대고", text)


if __name__ == "__main__":
    unittest.main()
