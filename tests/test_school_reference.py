"""
G5-B 교육시설 수 참고정보 테스트 (DEC-19 / OPEN-9 (b)).

- 표시 값이 G5-A 집계와 같은지
- 교육이 점수·가중치·후보 계산에 들어가지 않는지
- 파일이 없거나 깨져도 추천 화면이 그대로 동작하고 점수·순위가 같은지
- 문구에 교육환경·학군 우수 같은 과장 표현이 없는지
"""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from analysis import feedback, scoring
from analysis.candidates import EVALUATION_AXES
from scripts import ingest_school_counts as ingest
from services import schools

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAGGERATION_WORDS = ("교육환경", "교육 환경", "학군 우수", "우수", "좋은 학군", "교육 여건이 좋", "명문")
NAME_TO_REGION_ID = ingest.GU_NAME_TO_REGION_ID


class SchoolReferenceDataTest(unittest.TestCase):
    def test_counts_match_g5a_aggregation(self):
        reference = schools.load_school_reference()
        self.assertEqual(reference["status"], "확보")
        self.assertEqual(reference["reference_date"], "2026-03-20")
        by_region = {row["region_id"]: row for row in reference["rows"]}
        for gu, levels in ingest.EXPECTED_COUNTS.items():
            row = by_region[NAME_TO_REGION_ID[gu]]
            self.assertEqual(row["elementary_school_count"], levels["초등학교"])
            self.assertEqual(row["middle_school_count"], levels["중학교"])
            self.assertEqual(row["high_school_count"], levels["고등학교"])
            self.assertEqual(row["school_count"], sum(levels.values()))
        self.assertEqual(sum(r["school_count"] for r in reference["rows"]), ingest.EXPECTED_TOTAL)

    def test_missing_file_is_unavailable_not_error(self):
        reference = schools.load_school_reference(Path(tempfile.gettempdir()) / "no_such_school_counts.csv")
        self.assertEqual(reference["status"], "미확보")
        self.assertEqual(reference["rows"], [])

    def _write(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig")
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return Path(handle.name)

    def test_incomplete_or_inconsistent_file_is_unavailable(self):
        header = ",".join(("region_id", "region_name") + schools.COUNT_FIELDS + ("source", "reference_date"))
        only_one_region = self._write(header + "\nCW-UICHANG,의창구,26,13,11,50,0,s,d\n")
        self.assertEqual(schools.load_school_reference(only_one_region)["status"], "미확보")

        lines = [header] + [f"{rid},x,1,1,1,{99 if rid == 'CW-JINHAE' else 3},0,s,d" for rid in schools.REGION_ORDER]
        bad_sum = self._write("\n".join(lines) + "\n")
        self.assertEqual(schools.load_school_reference(bad_sum)["status"], "미확보")

    def test_wording_has_no_exaggeration(self):
        text = schools.SCHOOL_REFERENCE_TITLE + " ".join(schools.SCHOOL_REFERENCE_LIMITATIONS)
        for word in EXAGGERATION_WORDS:
            self.assertNotIn(word, text)
        self.assertIn("추천 점수에 사용하지 않음", schools.SCHOOL_REFERENCE_TITLE)


class EducationNotScoredTest(unittest.TestCase):
    def test_education_not_in_scoring_weights_or_candidates(self):
        self.assertNotIn("교육", scoring.CONDITION_TO_INDICATOR_CODE)
        self.assertNotIn("school_count", scoring.VALID_SCORABLE_INDICATOR_CODES)
        self.assertNotIn("school_count", scoring.INDICATOR_CATEGORY)
        self.assertEqual(dict(EVALUATION_AXES)["교육"], None)
        self.assertEqual(feedback.resolve_axis("교육"), ("missing", "교육"))


class ResultScreenTest(unittest.TestCase):
    def _done_screen(self, school_counts_csv: Path | None = None) -> AppTest:
        at = AppTest.from_file(os.path.join(PROJECT_ROOT, "app.py"), default_timeout=120).run()
        no_questions = {"message": {"content": '{"questions": [], "tool_calls": []}'}}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("ollama.chat", return_value=no_questions))
            if school_counts_csv is not None:  # 실제 파일 경로를 없는 파일로 바꿔 "파일 없음"을 재현
                stack.enter_context(mock.patch.object(schools, "SCHOOL_COUNTS_CSV", school_counts_csv))
            at.button[0].click().run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertEqual(at.session_state["stage"], "done")
        return at

    @staticmethod
    def _scores(at: AppTest) -> tuple:
        result = at.session_state["initial_recommendation"]
        review = at.session_state["agent_execution_log"]["candidate_review"]
        return (
            result["region_scores"],  # 점수·순위·축별 점수 전체를 반올림 없이 비교
            [(c["role"], c.get("region_id"), c["status"]) for c in review["roles"]],
            review["critic"],
        )

    def test_reference_shown_and_scores_identical_with_or_without_data(self):
        with_data = self._done_screen()
        captions = " ".join(c.value for c in with_data.caption)
        self.assertIn("2026-03-20", captions)
        self.assertIn("학군 수준이나 교육의 질을 뜻하지 않습니다", captions)
        expander_labels = " ".join(e.label for e in with_data.expander)
        self.assertIn("교육시설 수 참고정보", expander_labels)
        self.assertIn("실제 접근성", captions)
        self.assertIn("읍·면 지역", captions)
        for word in EXAGGERATION_WORDS:
            self.assertNotIn(word, captions + expander_labels)

        missing = Path(tempfile.gettempdir()) / "no_such_dir_g5b" / "changwon_school_counts.csv"
        self.assertFalse(missing.exists())
        without_data = self._done_screen(missing)
        infos = " ".join(i.value for i in without_data.info)
        self.assertIn("교육시설 수: 미확보", infos)

        self.assertEqual(self._scores(with_data), self._scores(without_data))


if __name__ == "__main__":
    unittest.main()
