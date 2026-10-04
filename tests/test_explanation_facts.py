"""
explanation_facts(Python이 확정한 강점·약점·대소관계)와 그 사실 기준 설명 검증 테스트. 실제 LLM 호출 없음.

    python -m unittest tests.test_explanation_facts -v

실데이터(동일 가중치) 축별 순위 - 강점 1~2위 / 중립 3위 / 약점 4~5위:
    성산구  교통 4위(약점)  의료 1위(강점)  생활편의 1위(강점)
    의창구  교통 1위(강점)  의료 2위(강점)  생활편의 2위(강점)
    마산합포구 교통 2위(강점) 의료 4위(약점) 생활편의 4위(약점)
"""

import json
import unittest
from unittest import mock

from agent import planner_loop
from analysis import feedback
from analysis.candidates import build_candidate_set
from analysis.explanation_facts import build_explanation_facts, judge
from analysis.scoring import compute_region_scores_from_weights

EQUAL = {"bus_stop_count": 1, "hospital_count": 1, "convenience_store_count": 1}
SCOPE = "현재 확보된 교통·의료·생활편의 기준에서는 성산구가 최적 후보입니다. "


def _facts(weights=EQUAL, count=3):
    score = compute_region_scores_from_weights(weights, count)
    return build_explanation_facts(score, build_candidate_set(score, ["주거비 예산"])), score


def _obs(weights=EQUAL, count=3, what_ifs=()):
    score = compute_region_scores_from_weights(weights, count)
    return planner_loop.build_observation(
        score_result=score, approved_weights=weights, available_indicators={}, selected_conditions=[],
        extra_indicators={}, what_ifs=list(what_ifs), candidate_review=build_candidate_set(score, ["주거비 예산"]))


def _check(text, weights=EQUAL):
    return planner_loop.verify_answer(text, _obs(weights))


class FactsGenerationTest(unittest.TestCase):
    """판정·순위·비교·충분성은 전부 Python이 결정적으로 만든다."""

    def test_region_axis_facts(self):
        facts, _ = _facts()
        r = {x["region"]: x for x in facts["regions"]}
        self.assertEqual(r["성산구"]["strengths"], ["의료", "생활편의"])
        self.assertEqual(r["성산구"]["weaknesses"], ["교통"])
        self.assertEqual(r["성산구"]["axes"]["교통"], {"value": 452.0, "score": 18.7, "rank": 4})
        self.assertEqual(r["성산구"]["roles"], ["최적"])
        self.assertEqual(r["의창구"]["roles"], ["균형", "대안"])
        self.assertEqual(r["마산합포구"]["weaknesses"], ["의료", "생활편의"])

    def test_judgement_rule(self):
        self.assertEqual([judge(k) for k in range(1, 6)], ["강점", "강점", "중립", "약점", "약점"])

    def test_dimensions_direction_and_sufficiency(self):
        facts, _ = _facts()
        self.assertEqual({d["axis"]: d["direction"] for d in facts["dimensions"]},
                         {"교통": "높을수록 좋음", "의료": "높을수록 좋음", "생활편의": "높을수록 좋음"})
        self.assertEqual(facts["data_sufficiency"], {
            "available_dimensions": 3, "total_dimensions": 6, "missing_dimensions": ["주거비", "교육", "직장 접근성"],
            "unused_confirmed_dimensions": [], "not_reflected_conditions": ["주거비 예산"]})

    def test_comparisons_and_dominance(self):
        facts, _ = _facts()
        self.assertEqual(facts["comparisons"], [
            {"axis": "교통", "high_to_low": ["의창구", "성산구"]},
            {"axis": "의료", "high_to_low": ["성산구", "의창구"]},
            {"axis": "생활편의", "high_to_low": ["성산구", "의창구"]}])
        self.assertIn({"dominant": "의창구", "dominated": "마산합포구"}, facts["dominance"])

    def test_unused_axis_is_reported_not_judged(self):
        facts, _ = _facts({"bus_stop_count": 50, "hospital_count": 50})
        self.assertEqual(facts["data_sufficiency"]["unused_confirmed_dimensions"], ["생활편의"])
        for region in facts["regions"]:
            self.assertNotIn("생활편의", region["axes"])

    def test_no_facts_without_candidate_review(self):
        _, score = _facts()
        self.assertIsNone(build_explanation_facts(score, None))


class RequiredSemanticTest(unittest.TestCase):
    """사용자 지정 최소 테스트 1~7."""

    def test_1_weakness_called_strength_fails(self):
        for text in (SCOPE + "성산구는 교통이 강점입니다.",
                     SCOPE + "버스정류장 수도 18.7점으로 다른 구보다 근소하게 앞서는 종합 점수 72.9점을 기록했습니다.",
                     SCOPE + "성산구는 버스정류장이 많습니다."):
            ok, why = _check(text)
            self.assertFalse(ok, text)
            self.assertIn("성산구의 교통", why)

    def test_2_strength_called_weakness_fails(self):
        for text in (SCOPE + "성산구는 의료가 약점입니다.",
                     SCOPE + "성산구는 의료기관이 적습니다.",
                     SCOPE + "다만 생활편의는 부족합니다."):  # 주어 생략 - 직전 문장의 성산구
            ok, why = _check(text)
            self.assertFalse(ok, text)
            self.assertIn("성산구의", why)

    def test_3_inverted_dimension_count_fails(self):
        ok, why = _check(SCOPE + "평가에 사용된 평가축은 총 3개 중 6개입니다.")
        self.assertFalse(ok)
        self.assertIn("3개 중 6개", why)
        ok, why = _check(SCOPE + "평가축 5개 중 3개만 확보했습니다.")
        self.assertFalse(ok)
        self.assertIn("6개 중 3개", why)
        self.assertTrue(_check(SCOPE + "전체 6개 평가축 중 3개만 확보했습니다.")[0])

    def test_4_other_regions_strength_borrowed_fails(self):
        # 의료 1위는 성산구 - 의창구(2위)에 '가장 많다'를 붙이면 실패
        ok, why = _check(SCOPE + "균형 후보 의창구는 의료기관이 가장 많습니다.")
        self.assertFalse(ok)
        self.assertIn("의창구의 의료", why)
        # 교통은 의창구가 성산구보다 높다 - 뒤집으면 실패
        ok, why = _check(SCOPE + "성산구는 의창구보다 교통이 앞섭니다.")
        self.assertFalse(ok)
        self.assertIn("교통에서 성산구는 의창구보다", why)
        # 지배 관계 방향을 뒤집으면 실패
        ok, why = _check(SCOPE + "마산합포구는 의창구를 지배합니다.")
        self.assertFalse(ok)
        self.assertTrue(_check(SCOPE + "마산합포구는 의창구에 지배되어 대안에서 빠졌습니다.")[0])

    def test_5_feedback_uses_latest_facts(self):
        plan = feedback.adjust_weights({c: 100 / 3 for c in EQUAL},
                                       [{"indicator_code": "bus_stop_count", "direction": "increase", "strength": "strong"}])
        out = feedback.reevaluate(plan["after"], 3, ["주거비 예산"])
        captured = {}

        def fake(**kwargs):
            captured["input"] = kwargs["messages"][-1]["content"]
            # 피드백 전 사실(성산구 최적)을 그대로 말함 -> 최신 기본 설명 기준으로 거부돼야 한다
            return {"message": {"content": json.dumps({"answer": SCOPE}, ensure_ascii=False)}}
        with mock.patch("agent.llm.chat", side_effect=fake):
            res = planner_loop.explain_candidates(score_result=out["score_result"], candidate_review=out["candidate_review"],
                                                  selected_conditions=[], model="m")
        self.assertIn("최적 후보는 의창구입니다", captured["input"])  # AI가 받은 것은 최신 기본 설명
        self.assertEqual(res["final_answer"]["source"], "deterministic")
        self.assertIn("최적 후보는 의창구입니다", res["final_answer"]["text"])
        self.assertIn("의창구의 종합점수는 교통 한 축에 크게 기대고", res["final_answer"]["text"])

    def test_6_missing_dimension_as_strength_or_weakness_fails(self):
        for text in (SCOPE + "성산구는 주거비가 약점입니다.",
                     SCOPE + "의창구는 교육이 강점이지만 데이터는 미확보입니다.",
                     SCOPE + "성산구는 월세가 저렴합니다."):
            ok, why = _check(text)
            self.assertFalse(ok, text)
            self.assertIn("미확보 평가축", why)
        self.assertTrue(_check(SCOPE + "주거비는 미확보라 판단에 포함하지 않았습니다.")[0])

    def test_7_numeric_hallucination_still_checked(self):
        self.assertFalse(_check(SCOPE + "성산구는 85.0점입니다.")[0])
        self.assertFalse(_check(SCOPE + "성산구 의료기관은 999개입니다.")[0])
        self.assertFalse(_check(SCOPE + "성산구는 의창구보다 2배 좋습니다.")[0])
        self.assertTrue(_check(SCOPE + "성산구는 의료기관이 398개로 가장 많고 교통은 약점입니다.")[0])


class AttributionTest(unittest.TestCase):
    """숫자 자체는 관찰 내용에 있지만 다른 구·다른 축의 값을 가져온 경우 - 2026-10-03 실제 Ollama 답변에서 나온 유형."""

    def test_wrong_axis_rank_attributed_fails(self):
        ok, why = _check(SCOPE + "성산구는 버스정류장 수는 452개로 3위지만 종합 점수 72.9점으로 1위를 차지했습니다.")
        self.assertFalse(ok)
        self.assertIn("3위", why)  # 성산구 교통은 4위

    def test_other_regions_score_attributed_fails(self):
        w = {"bus_stop_count": 50, "hospital_count": 25, "convenience_store_count": 25}
        text = ("현재 확보된 교통·의료·생활편의 기준에서는 의창구가 최적 후보입니다. "
                "의창구의 교통은 84.8점, 성산구의 의료는 31.3점으로 각각 다른 축에서 우위를 보입니다.")
        ok, why = _check(text, w)
        self.assertFalse(ok)
        self.assertIn("84.8점", why)  # 84.8은 마산합포구 교통, 의창구 교통은 100.0

    def test_role_phrase_with_omitted_subject_fails(self):
        # 실제 답변: 성산구(최적) 다음 문장에서 주어를 생략하고 "균형과 대안 역할" - 균형·대안은 의창구
        ok, why = _check(SCOPE + "의료와 생활편의는 1위, 교통은 4위로 약점이 가장 작은 구로 균형과 대안 역할을 합니다.")
        self.assertFalse(ok)
        self.assertIn("균형 후보는 의창구", why)
        self.assertTrue(_check(SCOPE + "의창구는 균형과 대안 역할을 함께 맡습니다.")[0])

    def test_correctly_attributed_numbers_pass(self):
        self.assertTrue(_check(SCOPE + "성산구는 교통이 452개로 4위이고, 의료기관은 398개로 100점입니다.")[0])
        self.assertTrue(_check(SCOPE + "성산구는 종합 72.9점으로 1위이며 의료가 강점입니다.")[0])


class NoFalseRejectionTest(unittest.TestCase):
    """맞는 설명은 통과해야 한다(검증이 지나치면 AI 설명이 늘 Python 요약으로 빠진다)."""

    def test_correct_explanation_passes(self):
        text = ("현재 확보된 교통·의료·생활편의 기준에서는 성산구가 최적 후보입니다. 성산구는 의료와 생활편의가 강점이고, "
                "교통은 약점입니다. 균형 후보 의창구는 버스정류장이 831개로 가장 많고 세 축 모두 강점입니다. "
                "의창구는 성산구보다 교통이 앞섭니다. 대안 후보도 의창구이며, 처음 고른 마산합포구는 의창구에 지배되어 교체되었습니다. "
                "가성비 후보는 주거비 데이터를 확보하지 못해 산출할 수 없습니다. 전체 6개 평가축 중 3개만 확보했고, "
                "주거비·교육·직장 접근성은 미확보라 판단에 포함하지 않았습니다.")
        self.assertEqual(_check(text), (True, ""))

    def test_ambiguous_multi_region_clause_not_rejected(self):
        self.assertTrue(_check(SCOPE + "대안 후보 의창구는 성산구가 약한 교통을 보완합니다.")[0])

    def test_hypothetical_requires_what_if(self):
        text = SCOPE + "가중치를 바꿔도 성산구가 최적 후보입니다."
        self.assertFalse(_check(text)[0])
        sim = {"weights_percent": {"hospital_count": 70.0, "bus_stop_count": 30.0}, "status": "ok",
               "ranking": [{"rank": 1, "region_name": "성산구", "total_score": 80.0}]}
        self.assertTrue(planner_loop.verify_answer(text, _obs(what_ifs=[sim]))[0])


if __name__ == "__main__":
    unittest.main()
