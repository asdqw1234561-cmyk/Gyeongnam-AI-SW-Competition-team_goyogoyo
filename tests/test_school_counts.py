"""
G5-A 창원시 5개 구 학교 수 집계 테스트. 합성 행으로 추출·검증·제외 규칙을 확인하고,
원본 CSV가 있으면 실제 집계가 기대값·합계와 맞는지 다시 센다.
"""

import unittest

from scripts import ingest_school_counts as sc


def _row(name, road, jibun=None, level="초등학교", status="운영", office=sc.CHANGWON_OFFICE,
         school_id=None, branch="본교", date="2026-03-20"):
    return {
        "학교ID": school_id or name, "학교명": name, "학교급구분": level, "운영상태": status,
        "본교분교구분": branch, "소재지도로명주소": road, "소재지지번주소": jibun if jibun is not None else road,
        "교육지원청명": office, "데이터기준일자": date, "제공기관명": "한국교육시설안전원",
        "위도": "", "경도": "",
    }


class ChangwonExtractionTest(unittest.TestCase):
    def test_only_gyeongnam_changwon_gu_prefix_is_extracted(self):
        rows = [
            _row("가", "경상남도 창원시 의창구 원이대로 1"),
            _row("서울청파초", "서울특별시 용산구 효창원로 228", office="서울특별시중부교육지원청"),
            _row("영춘분교", "충청북도 단양군 영춘면 별방창원로 449", office="충청북도단양교육지원청"),
        ]
        report = sc.build_report(rows, polygons=None)
        self.assertEqual(report["changwon_rows"], 1)
        self.assertEqual(report["totals"]["의창구"], 1)
        self.assertEqual(report["issues"], [])

    def test_jibun_gu_mismatch_is_reported_not_corrected(self):
        rows = [_row("나", "경상남도 창원시 성산구 중앙대로 1", jibun="경상남도 창원시 의창구 용호동 1")]
        report = sc.build_report(rows, polygons=None)
        self.assertEqual([i["check"] for i in report["issues"]], ["지번주소 구 불일치"])
        self.assertEqual(report["totals"]["성산구"], 1)  # 도로명 기준 집계는 그대로, 고치지 않음
        self.assertTrue(sc.consistency_errors(report))

    def test_office_school_missing_from_address_extraction_is_reported(self):
        rows = [_row("다", "경상남도 김해시 어딘가 1")]  # 창원교육지원청인데 주소가 창원이 아님
        report = sc.build_report(rows, polygons=None)
        self.assertEqual(report["changwon_rows"], 0)
        self.assertEqual(report["issues"][0]["check"], "창원교육지원청 소관인데 주소로 추출 안 됨")

    def test_exclusion_only_by_explicit_columns(self):
        rows = [
            _row("폐교", "경상남도 창원시 진해구 a 1", status="폐교"),
            _row("유치원", "경상남도 창원시 진해구 b 1", level="유치원"),
            _row("특수학교라는이름", "경상남도 창원시 진해구 c 1", level="고등학교"),
            _row("분교장", "경상남도 창원시 진해구 d 1", branch="분교"),
        ]
        report = sc.build_report(rows, polygons=None)
        self.assertEqual(report["excluded"], {"운영상태=폐교": 1, "학교급구분=유치원": 1})
        self.assertEqual(report["counts"]["진해구"], {"초등학교": 1, "중학교": 0, "고등학교": 1})
        self.assertEqual(report["branch_schools"]["진해구"], 1)
        self.assertEqual(report["changwon_rows"], report["included_rows"] + 2)


@unittest.skipUnless(sc.RAW_CSV.exists(), "원본 학교위치표준데이터 CSV 없음")
class RealDataTest(unittest.TestCase):
    def test_real_counts_cross_checked_and_consistent(self):
        polygons, _ = sc.geo_map.load_district_polygons()
        report = sc.build_report(sc.load_raw_rows(), polygons)
        self.assertEqual(sc.consistency_errors(report), [])
        self.assertEqual(report["grand_total"], sc.EXPECTED_TOTAL)
        self.assertEqual(report["reference_dates"], ["2026-03-20"])


if __name__ == "__main__":
    unittest.main()
