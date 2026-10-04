"""
경남 확장 A단계: data/gyeongnam/ 집계 파일이 22개 지역 마스터와 맞고, 창원 5개 구 값이 기존 검증값과
어긋나지 않는지 확인한다. 버스정류장 '자기 관할 등록분만' 규칙은 합성 행으로 확인한다(네트워크 없음).
"""

import csv
import unittest
from pathlib import Path

from shapely.geometry import box

from scripts import gyeongnam_regions as gn
from scripts import ingest_gyeongnam_bus_stops as bus
from scripts import ingest_school_counts as g5a

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "gyeongnam"
FILES = {
    "bus_stop_counts.csv": "bus_stop_count",
    "hospital_counts.csv": "hospital_count",
    "convenience_counts.csv": "convenience_store_count",
    "school_counts.csv": "school_count",
    "population.csv": "population",
}


def _read(name: str) -> list[dict]:
    with open(DATA_DIR / name, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class RegionMasterTest(unittest.TestCase):
    def test_22_unique_regions_with_changwon_ids_kept(self):
        self.assertEqual(len(gn.REGIONS), 22)
        self.assertEqual(len(set(gn.REGION_IDS)), 22)
        self.assertEqual(sum(1 for r in gn.REGIONS if r[0].startswith("CW-")), 5)
        self.assertEqual({r[2] for r in gn.REGIONS}, {"구", "시", "군"})


class AggregatedFilesTest(unittest.TestCase):
    def test_every_file_has_exactly_the_22_regions_with_positive_values(self):
        for name, column in FILES.items():
            rows = _read(name)
            self.assertEqual([r["region_id"] for r in rows], list(gn.REGION_IDS), name)
            self.assertTrue(all(int(r[column]) > 0 for r in rows), name)
            self.assertEqual(len({r["reference_date" if "reference_date" in r else "reference_ym"] for r in rows}), 1, name)

    def test_changwon_values_match_previous_verified_counts(self):
        schools = {r["region_id"]: int(r["school_count"]) for r in _read("school_counts.csv")}
        for gu, levels in g5a.EXPECTED_COUNTS.items():
            self.assertEqual(schools[g5a.GU_NAME_TO_REGION_ID[gu]], sum(levels.values()))
        convenience = {r["region_id"]: int(r["convenience_store_count"]) for r in _read("convenience_counts.csv")}
        self.assertEqual([convenience[rid] for rid in gn.REGION_IDS[:5]], [199, 245, 159, 151, 196])


class BusOwnRegistryRuleTest(unittest.TestCase):
    """다른 시·군 BIS가 등록한 정류장은 같은 물리 정류장 중복 가능성이 있어 세지 않는다."""

    def test_only_stops_registered_by_the_region_itself_are_counted(self):
        polygons = [box(128.0, 35.0, 128.1, 35.1), box(128.2, 35.0, 128.3, 35.1)]
        ids = ["CW-UICHANG", "GN-GIMHAE"]

        def row(no, lat, lon, city):
            return {"정류장번호": no, "정류장명": no, "위도": str(lat), "경도": str(lon), "정보수집일": "2025-10-31",
                    "도시코드": "x", "도시명": city, "관리도시명": "x"}

        rows = [
            row("A", 35.05, 128.05, "경상남도 창원시"),
            row("B", 35.05, 128.06, "경상남도 김해시"),   # 김해가 창원 안에 등록 -> 제외
            row("C", 35.05, 128.07, "경상남도 마산시"),   # 통합 전 이름도 창원
            row("D", 35.05, 128.25, "경상남도 김해시"),
            row("E", 35.05, 128.25, "부산광역시"),        # 부산BIS 등록 -> 제외
            row("F", 35.50, 128.50, "경상남도 김해시"),   # 경계 밖 -> 제외
        ]
        report = bus.build_report(rows, polygons, ids)
        self.assertEqual(report["counts"]["CW-UICHANG"], 2)
        self.assertEqual(report["counts"]["GN-GIMHAE"], 1)
        self.assertEqual(sum(report["other_registry"].values()), 2)


if __name__ == "__main__":
    unittest.main()
