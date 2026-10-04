# 행정안전부 주민등록 인구(행정동) -> 경남 22개 비교 단위별 인구
"""
원본: data/raw/주민등록인구_행정동_20260831.csv
      (공공데이터포털 15097972 '행정안전부_지역별(행정동) 성별 연령별 주민등록 인구수', 기준연월 2026-08-31, cp949)
출력: data/gyeongnam/population.csv

시도명 == '경상남도'인 행정동 행의 '계'를 시군구명별로 더한다. 시군구명은 scripts/gyeongnam_regions.py의
지역 이름("창원시 의창구", "진주시" ...)과 정확히 같아야 하며, 대응하지 않는 이름이 있으면 멈춘다.
인구는 '인구 1만 명당 시설 수' 계산의 분모로만 쓴다(점수 축 아님).

    python scripts/ingest_gyeongnam_population.py [--write]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gyeongnam_regions as gn  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = PROJECT_ROOT / "data" / "raw" / "주민등록인구_행정동_20260831.csv"
OUT_CSV = PROJECT_ROOT / "data" / "gyeongnam" / "population.csv"
SOURCE_LABEL = "행정안전부 지역별(행정동) 성별 연령별 주민등록 인구수 (공공데이터포털 15097972)"


def build_report(rows: list[dict]) -> dict:
    totals: dict[str, int] = defaultdict(int)
    dong_counts: dict[str, int] = defaultdict(int)
    unknown = set()
    dates = set()
    for row in rows:
        if row["시도명"] != gn.PROVINCE:
            continue
        if not row["읍면동명"].strip():  # 시군구 합계 행이 있다면 이중 합산하지 않는다
            continue
        region_id = gn.ID_BY_NAME.get(row["시군구명"])
        if region_id is None:
            unknown.add(row["시군구명"])
            continue
        totals[region_id] += int(row["계"].replace(",", ""))
        dong_counts[region_id] += 1
        dates.add(row["기준연월"])
    return {"population": {rid: totals.get(rid, 0) for rid in gn.REGION_IDS},
            "dong_counts": dict(dong_counts), "unknown_names": sorted(unknown), "reference_dates": sorted(dates)}


def consistency_errors(report: dict) -> list[str]:
    errors = []
    if report["unknown_names"]:
        errors.append(f"22개 지역에 대응하지 않는 시군구명: {report['unknown_names']}")
    if any(v <= 0 for v in report["population"].values()):
        errors.append("인구가 0인 지역이 있음")
    if len(report["reference_dates"]) != 1:
        errors.append(f"기준연월이 하나가 아님: {report['reference_dates']}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with open(RAW_CSV, encoding="cp949", newline="") as f:
        report = build_report(list(csv.DictReader(f)))
    for rid in gn.REGION_IDS:
        print(f"  {gn.NAME_BY_ID[rid]:<10} {report['population'][rid]:>9,}명 (행정동 {report['dong_counts'].get(rid, 0)}개)")
    print(f"합계 {sum(report['population'].values()):,}명 · 기준연월 {report['reference_dates']}")
    errors = consistency_errors(report)
    for e in errors:
        print(f"[오류] {e}")
    if errors:
        sys.exit(1)
    if args.write:
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region_id", "region_name", "population", "source", "reference_date"])
            for rid in gn.REGION_IDS:
                writer.writerow([rid, gn.NAME_BY_ID[rid], report["population"][rid], SOURCE_LABEL,
                                 report["reference_dates"][0]])
        print(f"저장: {OUT_CSV.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
