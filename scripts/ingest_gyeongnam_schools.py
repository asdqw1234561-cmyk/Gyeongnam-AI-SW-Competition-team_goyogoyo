# 전국초중등학교위치표준데이터 -> 경남 22개 비교 단위별 초·중·고 학교 수 (참고정보, 점수 미사용)
"""
scripts/ingest_school_counts.py(G5-A, 창원 5개 구)와 같은 원본·같은 규칙을 22개 지역으로 넓힌다.
    - 도로명주소가 "경상남도 <지역> "으로 시작하는 행만(창원시는 "경상남도 창원시 <구> ")
    - 지번주소의 지역, 좌표의 SGIS 경계 판정(data/raw/gyeongnam_boundaries.geojson)이 모두 같아야 함 - 어긋나면 멈춤
    - 원본 컬럼으로 명확한 경우만 제외(운영상태≠운영, 학교급구분이 초·중·고가 아님)
출력: data/gyeongnam/school_counts.csv

    python scripts/ingest_gyeongnam_schools.py [--write]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gyeongnam_regions as gn  # noqa: E402
import ingest_school_counts as g5a  # noqa: E402  (원본 경로·인코딩·제외 기준·출처 재사용)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOUNDARY_GEOJSON = PROJECT_ROOT / "data" / "raw" / "gyeongnam_boundaries.geojson"
OUT_CSV = PROJECT_ROOT / "data" / "gyeongnam" / "school_counts.csv"

_NAMES = sorted(gn.ID_BY_NAME, key=len, reverse=True)  # "창원시 의창구"가 "창원시"보다 먼저 맞도록
ADDR_PATTERN = re.compile(r"^경상남도 (" + "|".join(re.escape(n) for n in _NAMES) + r") ")


def region_of(address: str) -> str | None:
    m = ADDR_PATTERN.match(address or "")
    return gn.ID_BY_NAME[m.group(1)] if m else None


def build_report(rows: list[dict]) -> dict:
    gj = json.loads(BOUNDARY_GEOJSON.read_text(encoding="utf-8"))
    polygons = [shape(f["geometry"]) for f in gj["features"]]
    ids = [f["properties"]["region_id"] for f in gj["features"]]
    tree = STRtree(polygons)
    extracted, issues, excluded = [], [], Counter()
    for row in rows:
        rid = region_of(row.get("소재지도로명주소", ""))
        if rid is None:
            continue
        extracted.append((row, rid))
        if region_of(row.get("소재지지번주소", "")) != rid:
            issues.append((row["학교명"], "지번주소 지역 불일치"))
        try:
            point = Point(float(row["경도"]), float(row["위도"]))
            hits = [ids[i] for i in tree.query(point) if polygons[i].contains(point)]
        except (TypeError, ValueError):
            hits = []
        if hits != [rid]:
            issues.append((row["학교명"], f"좌표 판정 {hits or '경계 밖'}"))
    counts = {rid: {level: 0 for level in g5a.SCHOOL_LEVELS} for rid in gn.REGION_IDS}
    branch = Counter()
    for row, rid in extracted:
        if row.get("운영상태") != g5a.OPERATING:
            excluded[f"운영상태={row.get('운영상태')}"] += 1
        elif row.get("학교급구분") not in g5a.SCHOOL_LEVELS:
            excluded[f"학교급구분={row.get('학교급구분')}"] += 1
        else:
            counts[rid][row["학교급구분"]] += 1
            branch[rid] += row.get("본교분교구분") == "분교"
    return {"extracted": len(extracted), "issues": issues, "excluded": dict(excluded), "counts": counts,
            "branch": branch, "reference_dates": sorted({r.get("데이터기준일자", "") for r, _ in extracted})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = build_report(g5a.load_raw_rows())
    for rid in gn.REGION_IDS:
        c = report["counts"][rid]
        print(f"  {gn.NAME_BY_ID[rid]:<10} 초 {c['초등학교']:>3} · 중 {c['중학교']:>3} · 고 {c['고등학교']:>3} = {sum(c.values()):>3}"
              f" (분교 {report['branch'][rid]})")
    total = sum(sum(c.values()) for c in report["counts"].values())
    print(f"경남 추출 {report['extracted']} = 포함 {total} + 제외 {sum(report['excluded'].values())} · 기준일 {report['reference_dates']}")
    for issue in report["issues"]:
        print(f"  [검증 실패] {issue}")
    if report["issues"] or report["extracted"] != total + sum(report["excluded"].values()) \
            or len(report["reference_dates"]) != 1:
        raise SystemExit("교차검증·합계 불일치 - 저장하지 않습니다.")
    cw = {gn.NAME_BY_ID[rid].split()[-1]: report["counts"][rid] for rid in gn.REGION_IDS if rid.startswith("CW-")}
    if cw != g5a.EXPECTED_COUNTS:
        raise SystemExit("창원 5개 구 값이 G5-A 검증값과 다릅니다.")
    if args.write:
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region_id", "region_name", "elementary_school_count", "middle_school_count",
                             "high_school_count", "school_count", "branch_school_count", "source", "reference_date"])
            for rid in gn.REGION_IDS:
                c = report["counts"][rid]
                writer.writerow([rid, gn.NAME_BY_ID[rid], c["초등학교"], c["중학교"], c["고등학교"], sum(c.values()),
                                 report["branch"][rid], g5a.SOURCE_LABEL, report["reference_dates"][0]])
        print(f"저장: {OUT_CSV.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
