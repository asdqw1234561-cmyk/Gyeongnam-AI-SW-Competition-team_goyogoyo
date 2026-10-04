"""
services/demand_log.py - 동의 기반 비식별 선택 통계(수요 기록)·집계 테스트.

    python -m unittest tests.test_demand_log -v
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from analysis import scoring
from analysis.candidates import build_candidate_set
from services import demand_log
from services.region_data import get_all_regions

INPUT = {
    "희망지역": "창원시 5개 구",
    "중요 생활조건": ["의료", "교통", "교육"],
    "직장/학교 위치": "창원시 성산구",
    "주거비 예산": "월세 30~50만원",
    "자가용 보유 여부": "미보유",
    "원하는 후보 개수": 3,
    "추가 요청사항": "홍길동 010-1234-5678 상남동 근처 원룸",
}


def _record(session="s1", feedback_history=None, input_=None):
    input_ = input_ or INPUT
    result = scoring.compute_region_scores(input_["중요 생활조건"], 3, regions=get_all_regions(region_type="구"))
    return demand_log.build_record(session, input_, result, build_candidate_set(result, []),
                                   feedback_history, today=date(2026, 10, 4))


class BuildRecordTest(unittest.TestCase):
    def test_keeps_choices_and_drops_free_text(self):
        record = _record()
        text = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("홍길동", text)
        self.assertNotIn("010", text)
        self.assertNotIn("상남동", text)
        self.assertNotIn("추가 요청사항", text)
        self.assertEqual(record["date"], "2026-10-04")
        self.assertEqual(record["scope"], "창원시 5개 구")
        self.assertEqual(record["conditions"], ["의료", "교통", "교육"])
        self.assertEqual(record["unscored_inputs"], {"주거비 예산": "월세 30~50만원", "직장/학교 위치": "창원시 성산구"})
        self.assertIn("교육", record["unscored_conditions"])  # 교육은 점수 축이 아니므로 반영 못 한 조건
        self.assertEqual(record["has_car"], "미보유")

    def test_values_are_copied_from_result(self):
        record = _record()
        self.assertAlmostEqual(sum(record["weights"].values()), 100.0, places=0)
        self.assertTrue(record["candidates"])
        self.assertTrue(all(set(c) == {"role", "region"} for c in record["candidates"]))

    def test_only_approved_feedback_directions(self):
        history = [
            {"approved": True, "targets": [{"axis": "의료", "direction": "increase"}]},
            {"approved": False, "targets": [{"axis": "교통", "direction": "decrease"}]},
            {"approved": True, "source": "slider", "targets": []},
        ]
        record = _record(feedback_history=history)
        self.assertEqual(record["feedback_directions"], [{"axis": "의료", "direction": "increase"}])
        self.assertEqual(record["feedback_count"], 2)

    def test_no_coordinates_or_location_question_fields(self):
        keys = set(_record())
        self.assertFalse(keys & {"lat", "lon", "search_center", "question", "text", "추가 요청사항"})


class StorageTest(unittest.TestCase):
    def test_missing_file_means_no_records(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(demand_log.load_records(Path(d) / "none.jsonl"), [])

    def test_append_and_latest_per_session(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "requests.jsonl"
            demand_log.append_record(_record("a"), path)
            demand_log.append_record(_record("b"), path)
            updated = _record("a", feedback_history=[{"approved": True, "targets": [{"axis": "의료", "direction": "increase"}]}])
            demand_log.append_record(updated, path)
            with path.open("a", encoding="utf-8") as f:
                f.write("{broken json\n\n")
            records = demand_log.load_records(path)
            self.assertEqual(len(records), 2)
            a = next(r for r in records if r["session"] == "a")
            self.assertEqual(a["feedback_count"], 1)

    def test_env_path(self):
        with tempfile.TemporaryDirectory() as d:
            import os
            from unittest import mock

            with mock.patch.dict(os.environ, {"DEMAND_LOG_PATH": str(Path(d) / "x.jsonl")}):
                demand_log.append_record(_record())
                self.assertEqual(len(demand_log.load_records()), 1)


class SummarizeTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(demand_log.summarize([]), {"total": 0})

    def test_counts_are_per_recorded_session(self):
        other_input = dict(INPUT, **{"중요 생활조건": [], "주거비 예산": "", "직장/학교 위치": ""})
        records = [
            _record("a", feedback_history=[{"approved": True, "targets": [{"axis": "의료", "direction": "increase"}]}]),
            _record("b"),
            _record("c", input_=other_input),
        ]
        summary = demand_log.summarize(records)
        self.assertEqual(summary["total"], 3)
        conditions = {row["항목"]: row["건수"] for row in summary["conditions"]}
        self.assertEqual(conditions, {"의료": 2, "교통": 2, "교육": 2})
        self.assertEqual(summary["no_condition"], 1)
        unmet = {row["항목"]: row["건수"] for row in summary["unmet"]}
        self.assertEqual(unmet["주거비 예산"], 2)
        self.assertEqual(unmet["교육"], 2)
        self.assertEqual(summary["budget"], [{"항목": "월세 30~50만원", "건수": 2, "비율(%)": 66.7}])
        self.assertEqual(summary["feedback_sessions"], 1)
        self.assertEqual(summary["raised"][0]["항목"], "의료")
        self.assertEqual(summary["period"], ("2026-10-04", "2026-10-04"))
        self.assertTrue(summary["candidates"])


if __name__ == "__main__":
    unittest.main()
