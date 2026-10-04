"""
Critic·후보 역할을 AI 설명 단계에 연결한 결과 테스트(실제 LLM 호출 없음, 실제 region_indicators.csv 사용).

    python -m unittest tests.test_candidate_explanation -v
"""

import json
import os
import unittest
from unittest import mock

from agent import planner, planner_loop
from analysis import feedback
from analysis.candidates import build_candidate_set
from analysis.scoring import compute_region_scores_from_weights

ALL3 = ["교통", "의료", "생활편의(마트/편의점)"]
EQUAL = {"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1}
PLAN = {"goals": ["교통·의료·생활편의"], "unsupported_requests": [], "tool_calls": [
    {"tool": "get_available_indicators", "reason": "r"},
    {"tool": "calculate_region_scores", "weights": EQUAL, "reason": "r"}]}

GOOD = ("현재 확보된 교통·의료·생활편의 기준에서는 성산구가 최적 후보이고, 의창구가 균형 후보입니다. "
        "대안 후보도 의창구인데, 처음 고른 마산합포구는 의창구보다 모든 평가축에서 낮거나 같아 Critic이 교체했습니다. "
        "가성비 후보는 주거비 데이터를 확보하지 못해 산출할 수 없습니다. "
        "주거비·교육·직장 접근성은 미확보라 판단에 포함하지 않았고, 입력하신 주거비 예산도 반영하지 못했습니다. "
        "점수는 시설 수 기반 상대 비교일 뿐 실제 거주 적합도를 확정하지 않습니다.")


def _resp(obj):
    return {"message": {"content": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)}}


def _observation_from(call) -> dict:
    content = call.kwargs["messages"][-1]["content"]
    return json.loads(content.split("[관찰 내용]\n", 1)[1].split("\n\n", 1)[0])


def _run(answers):
    """answers: 리뷰어 판단 뒤 AI 응답들. 새 흐름에서는 [리뷰어("" = 추가 조회 없음), 다듬은 문장] 순서."""
    responses = [_resp(PLAN)] + [_resp({"action": "answer", "answer": a}) for a in answers]
    with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"}), \
            mock.patch("ollama.chat", side_effect=responses) as m:
        result = planner.run_agent_plan(ALL3, EQUAL, "창원시", 3, unscored_inputs=["주거비 예산"])
    return result, m


class InitialExplanationTest(unittest.TestCase):

    def test_observation_carries_confirmed_candidates_and_limits(self):
        result, m = _run(["", GOOD])
        obs = _observation_from(m.call_args_list[1])["explanation_facts"]  # 리뷰어가 받은 관찰
        roles = {r["role"]: r for r in obs["roles"]}
        self.assertEqual(roles["최적"]["region"], "성산구")
        self.assertEqual(roles["균형"]["region"], "의창구")
        self.assertEqual(roles["가성비"]["status"], "산출 불가")
        self.assertNotIn("region", roles["가성비"])
        suff = obs["data_sufficiency"]
        self.assertEqual(suff["missing_dimensions"], ["주거비", "교육", "직장 접근성"])
        self.assertEqual((suff["available_dimensions"], suff["total_dimensions"]), (3, 6))
        self.assertEqual(suff["not_reflected_conditions"], ["주거비 예산"])
        self.assertEqual(obs["scope"], "현재 확보된 교통·의료·생활편의 기준")
        self.assertFalse(any("message" in c for c in obs["critic"]))
        # 다듬기 단계는 관찰 JSON이 아니라 Python 기본 설명만 받는다
        paraphrase_input = m.call_args_list[2].kwargs["messages"][-1]["content"]
        self.assertTrue(paraphrase_input.startswith("[기본 설명]"))
        self.assertIn("최적 후보는 성산구입니다", paraphrase_input)
        self.assertEqual(result["final_answer"]["source"], "ai_paraphrase")
        self.assertEqual(result["final_answer"]["text"], GOOD)

    def test_candidates_unchanged_by_explanation(self):
        result, _ = _run([GOOD])
        direct = build_candidate_set(result["score_result"], ["주거비 예산"])
        self.assertEqual(result["candidate_review"], direct)

    def test_critic_revision_is_in_observation_and_summary(self):
        result, m = _run(["", ""])
        obs = _observation_from(m.call_args_list[1])
        alt = next(r for r in obs["explanation_facts"]["roles"] if r["role"] == "대안")
        self.assertEqual((alt["region"], alt["revised_from"]["region"]), ("의창구", "마산합포구"))
        self.assertEqual(alt["revised_from"]["dominated_by"], ["의창구"])
        text = result["final_answer"]["text"]  # 빈 다듬기 응답 -> 기본 설명
        self.assertEqual(result["final_answer"]["source"], "deterministic")
        self.assertIn("대안 후보는 의창구입니다", text)
        self.assertIn("Critic이 처음 고른 마산합포구는 의창구에 비해 평가에 쓴 모든 축에서 낮거나 같아", text)

    def test_ai_cannot_reassign_alternative_to_revised_out_region(self):
        bad = GOOD.replace("대안 후보도 의창구인데, 처음 고른 마산합포구는 의창구보다 모든 평가축에서 낮거나 같아 Critic이 교체했습니다.",
                           "대안 후보는 마산합포구입니다.")
        result, m = _run(["", bad])
        self.assertEqual(m.call_count, 3)  # 재시도 없음
        self.assertEqual(result["final_answer"]["source"], "deterministic")
        self.assertIn("대안 후보는 의창구인데", result["final_answer"]["rejected_reason"])
        self.assertIn("대안 후보는 의창구입니다", result["final_answer"]["text"])

    def test_missing_axis_scope_warning_required(self):
        no_scope = "성산구가 최적 후보이고 의창구가 균형 후보입니다. 점수는 시설 수 기반 상대 비교입니다."
        result, _ = _run(["", no_scope])
        self.assertEqual(result["final_answer"]["source"], "deterministic")
        self.assertIn("판단 범위", result["final_answer"]["rejected_reason"])
        self.assertIn("미확보 평가축(주거비, 교육, 직장 접근성)", result["final_answer"]["text"])
        self.assertIn("반영하지 못한 조건: 주거비 예산", result["final_answer"]["text"])

    def test_single_axis_warning_reaches_summary(self):
        obs = planner_loop.build_observation(
            score_result=(r := compute_region_scores_from_weights({"hospital_count": 80, "bus_stop_count": 20}, 3)),
            approved_weights={"hospital_count": 80, "bus_stop_count": 20}, available_indicators={},
            selected_conditions=[], extra_indicators={}, what_ifs=[], candidate_review=build_candidate_set(r))
        self.assertIn({"code": "single_axis", "level": "warning", "region": "성산구", "axis": "의료"},
                      obs["explanation_facts"]["critic"])
        self.assertIn("의료 한 축에 크게 기대고", planner_loop.python_summary(obs))


class GuardrailTest(unittest.TestCase):
    """미확보 주거비 추정·과장·숫자 hallucination 거부."""

    @classmethod
    def setUpClass(cls):
        score = compute_region_scores_from_weights(EQUAL, 3)
        cls.obs = planner_loop.build_observation(
            score_result=score, approved_weights=EQUAL, available_indicators={}, selected_conditions=ALL3,
            extra_indicators={}, what_ifs=[], candidate_review=build_candidate_set(score, ["주거비 예산"]))

    def _check(self, text):
        return planner_loop.verify_answer(text, self.obs)

    def test_good_answer_passes(self):
        self.assertEqual(self._check(GOOD), (True, ""))

    def test_housing_cost_guess_rejected(self):
        ok, why = self._check("현재 확보된 기준에서 성산구가 최적 후보입니다. 성산구는 주거비도 저렴한 편입니다.")
        self.assertFalse(ok)
        self.assertIn("주거비", why)
        ok, why = self._check("현재 확보된 기준에서 의창구가 균형 후보입니다. 의창구는 월세가 비쌉니다.")
        self.assertFalse(ok)

    def test_housing_cost_as_limit_allowed(self):
        ok, _ = self._check("현재 확보된 교통·의료·생활편의 기준에서는 성산구가 최적 후보입니다. "
                            "주거비는 미확보라 판단에 포함하지 않았습니다.")
        self.assertTrue(ok)

    def test_overclaim_rejected(self):
        ok, why = self._check("현재 확보된 기준에서 성산구가 가장 살기 좋은 지역입니다.")
        self.assertFalse(ok)
        self.assertIn("가장 살기 좋은", why)

    def test_value_role_cannot_be_given_a_region(self):
        ok, why = self._check("현재 확보된 기준에서 가성비 후보는 진해구입니다.")
        self.assertFalse(ok)
        self.assertIn("가성비", why)

    def test_numeric_hallucination_still_rejected(self):
        self.assertFalse(self._check("현재 확보된 기준에서 성산구가 85.0점으로 최적 후보입니다.")[0])
        self.assertFalse(self._check("현재 확보된 기준에서 성산구는 의창구보다 2배 좋습니다.")[0])
        self.assertFalse(self._check("현재 확보된 기준에서 성산구 의료기관이 999개입니다.")[0])

    def test_axis_count_numbers_allowed(self):
        self.assertTrue(self._check("현재 확보된 기준은 6개 평가축 중 3개입니다. 성산구가 최적 후보입니다.")[0])


class InputLengthTest(unittest.TestCase):
    """후보군을 넣은 관찰 내용도 LLM_MAX_INPUT_CHARS(기본 4000) 안에 들어가야 한다 - 넘으면 설명이 항상 Python 요약으로 빠진다."""

    def test_worst_case_fits_default_limit(self):
        from agent.llm import DEFAULT_MAX_INPUT_CHARS
        score = compute_region_scores_from_weights(EQUAL, 5)  # 3축 전부 + 후보 5개 요청
        sim = {"weights_percent": {"bus_stop_count": 70.0, "hospital_count": 30.0}, "status": "ok",
               "ranking": [{"rank": r["rank"], "region_name": r["region_name"], "total_score": r["total_score"]}
                           for r in planner_loop._ranking_view(score)]}
        obs = planner_loop.build_observation(
            score_result=score, approved_weights=EQUAL, available_indicators={k: True for k in EQUAL},
            selected_conditions=ALL3, extra_indicators={}, what_ifs=[sim, sim],
            candidate_review=build_candidate_set(score, ["주거비 예산", "직장/학교 위치"]))
        captured = {}
        with mock.patch("agent.llm.chat", side_effect=lambda **kw: captured.update(kw) or _resp({"action": "answer", "answer": "x"})):
            planner_loop.call_reviewer(obs, True, "가" * 150, "m")
        self.assertLess(len(captured["messages"][-1]["content"]), DEFAULT_MAX_INPUT_CHARS)


class FeedbackExplanationTest(unittest.TestCase):
    """G3 재평가 뒤 설명: 바뀐 후보·Critic이 반영된다."""

    def _reevaluated(self):
        plan = feedback.adjust_weights(
            {"bus_stop_count": 100 / 3, "hospital_count": 100 / 3, "convenience_store_count": 100 / 3},
            [{"indicator_code": "bus_stop_count", "direction": "increase", "strength": "strong"}])
        return feedback.reevaluate(plan["after"], 3, ["주거비 예산"])

    def test_best_changed_after_feedback_is_explained(self):
        out = self._reevaluated()
        base = planner_loop.explain_from_facts(score_result=out["score_result"], candidate_review=out["candidate_review"],
                                               model="m", use_llm=False)["final_answer"]["text"]
        good = base.replace("Agent가 고른 정착 후보입니다.", "교통을 더 중요하게 본 결과입니다.")
        with mock.patch("ollama.chat", return_value=_resp({"answer": good})) as m:
            res = planner_loop.explain_candidates(score_result=out["score_result"], candidate_review=out["candidate_review"],
                                                  selected_conditions=ALL3, model="m")
        self.assertIn("최적 후보는 의창구입니다", m.call_args.kwargs["messages"][-1]["content"])
        self.assertEqual(res["final_answer"]["source"], "ai_paraphrase")
        self.assertEqual(res["final_answer"]["text"], good)

    def test_stale_best_from_before_feedback_rejected_then_summary(self):
        out = self._reevaluated()
        stale = "현재 확보된 기준에서 성산구가 최적 후보입니다."  # 피드백 전 결과를 그대로 말함
        with mock.patch("ollama.chat", return_value=_resp({"answer": stale})):
            res = planner_loop.explain_candidates(score_result=out["score_result"], candidate_review=out["candidate_review"],
                                                  selected_conditions=ALL3, model="m")
        self.assertEqual(res["final_answer"]["source"], "deterministic")
        self.assertIn("최적 후보는 의창구입니다", res["final_answer"]["text"])

    def test_ai_unavailable_falls_back(self):
        out = self._reevaluated()
        with mock.patch("ollama.chat", side_effect=ConnectionError("down")):
            res = planner_loop.explain_candidates(score_result=out["score_result"], candidate_review=out["candidate_review"],
                                                  selected_conditions=ALL3, model="m")
        self.assertEqual(res["final_answer"]["source"], "deterministic")
        self.assertIsNotNone(res["review_error"])


if __name__ == "__main__":
    unittest.main()
