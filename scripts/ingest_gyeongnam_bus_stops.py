# 국토교통부 전국 버스정류장 위치정보 -> 경남 22개 비교 단위별 버스정류장 수
"""
원본: data/raw/전국_버스정류장_위치정보_20251031.csv (공공데이터포털 15067528, cp949, 20MB - git 제외)
      내려받기: https://www.data.go.kr/data/15067528/fileData.do
출력: data/gyeongnam/gyeongnam_bus_stops.csv  (경남 22개 지역으로 판정된 정류장 목록, 커밋)
      data/gyeongnam/bus_stop_counts.csv      (지역별 수)

[집계 규칙] 22개 지역 모두 같은 규칙.
    "각 시·군이 자기 관할로 등록한 정류장(도시명이 그 시·군) 중 그 지역 경계 안에 있는 것"을
    정류장번호 기준으로 센다. 경계는 SGIS 2025년 2분기 시군구 경계(data/raw/gyeongnam_boundaries.geojson)
    점-다각형 판정. 다른 시·도/시·군 BIS가 등록한 정류장(예: 부산BIS가 양산에, 김해BIS가 창원에 등록한
    광역노선 정류장)은 같은 물리 정류장이 중복 등록된 경우가 많아 제외하고 건수만 기록한다.
    창원시는 통합 전 이름(마산시·진해시)으로 남은 행도 창원시로 본다.
    경계 판정이 안 되거나(경계선 위·바다) 두 지역에 겹치는 행은 추정하지 않고 제외해 건수를 남긴다.

    python scripts/ingest_gyeongnam_bus_stops.py           # 집계·검증 결과만
    python scripts/ingest_gyeongnam_bus_stops.py --write   # data/gyeongnam/ 에 저장
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gyeongnam_regions as gn  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = PROJECT_ROOT / "data" / "raw" / "전국_버스정류장_위치정보_20251031.csv"
BOUNDARY_GEOJSON = PROJECT_ROOT / "data" / "raw" / "gyeongnam_boundaries.geojson"
OUT_DIR = PROJECT_ROOT / "data" / "gyeongnam"
STOPS_OUT = OUT_DIR / "gyeongnam_bus_stops.csv"
COUNTS_OUT = OUT_DIR / "bus_stop_counts.csv"

SOURCE_LABEL = "국토교통부 전국 버스정류장 위치정보 (공공데이터포털 15067528) 및 통계청 SGIS 시군구 경계"
GYEONGNAM_BBOX = {"lat": (34.3, 36.0), "lon": (127.4, 129.4)}
CHANGWON_LEGACY_CITY_NAMES = ("경상남도 창원시", "경상남도 마산시", "경상남도 진해시")


def own_city_names(region_id: str) -> tuple[str, ...]:
    """그 지역을 관할로 등록한 원본 '도시명' 값."""
    if region_id.startswith("CW-"):
        return CHANGWON_LEGACY_CITY_NAMES
    return (f"{gn.PROVINCE} {gn.NAME_BY_ID[region_id]}",)


def load_polygons() -> tuple[list, list[str]]:
    gj = json.loads(BOUNDARY_GEOJSON.read_text(encoding="utf-8"))
    return [shape(f["geometry"]) for f in gj["features"]], [f["properties"]["region_id"] for f in gj["features"]]


def classify(lat: float, lon: float, polygons: list, ids: list[str], tree: STRtree) -> tuple[str | None, str]:
    point = Point(lon, lat)
    hits = [ids[i] for i in tree.query(point) if polygons[i].contains(point)]
    if len(hits) == 1:
        return hits[0], "contains"
    return None, "overlap" if hits else "outside"


def build_report(rows: list[dict], polygons: list, ids: list[str]) -> dict:
    tree = STRtree(polygons)
    excluded = Counter()
    other_registry = Counter()
    kept: list[dict] = []
    seen_ids: set[str] = set()
    for row in rows:
        try:
            lat, lon = float(row["위도"]), float(row["경도"])
        except (TypeError, ValueError):
            excluded["좌표 없음/형식 오류"] += 1
            continue
        if not (GYEONGNAM_BBOX["lat"][0] < lat < GYEONGNAM_BBOX["lat"][1]
                and GYEONGNAM_BBOX["lon"][0] < lon < GYEONGNAM_BBOX["lon"][1]):
            continue
        region_id, reason = classify(lat, lon, polygons, ids, tree)
        if region_id is None:
            if row["도시명"].startswith(gn.PROVINCE):
                excluded[f"경남 등록이지만 경계 판정 불가({reason})"] += 1
            continue
        if row["도시명"] not in own_city_names(region_id):
            other_registry[(region_id, row["도시명"])] += 1
            continue
        if row["정류장번호"] in seen_ids:
            excluded["정류장번호 중복"] += 1
            continue
        seen_ids.add(row["정류장번호"])
        kept.append({"region_id": region_id, "region_name": gn.NAME_BY_ID[region_id], **row})

    counts = Counter(k["region_id"] for k in kept)
    dates = sorted({k["정보수집일"] for k in kept})
    return {"raw_rows": len(rows), "kept": kept, "counts": {rid: counts.get(rid, 0) for rid in gn.REGION_IDS},
            "excluded": dict(excluded), "other_registry": other_registry, "reference_dates": dates}


def consistency_errors(report: dict) -> list[str]:
    errors = []
    if any(v == 0 for v in report["counts"].values()):
        errors.append("정류장이 0개인 지역이 있음 - 경계·도시명 대응 확인 필요")
    if sum(report["counts"].values()) != len(report["kept"]):
        errors.append("지역별 합 != 판정된 정류장 수")
    if len(report["reference_dates"]) != 1:
        errors.append(f"정보수집일이 하나가 아님: {report['reference_dates']}")
    return errors


def write_outputs(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    columns = ["region_id", "region_name", "정류장번호", "정류장명", "위도", "경도", "정보수집일", "도시코드", "도시명", "관리도시명"]
    with open(STOPS_OUT, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(report["kept"], key=lambda k: (gn.REGION_IDS.index(k["region_id"]), k["정류장번호"])))
    with open(COUNTS_OUT, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["region_id", "region_name", "bus_stop_count", "excluded_other_registry", "source", "reference_date"])
        for rid in gn.REGION_IDS:
            other = sum(v for (r, _), v in report["other_registry"].items() if r == rid)
            writer.writerow([rid, gn.NAME_BY_ID[rid], report["counts"][rid], other, SOURCE_LABEL,
                             report["reference_dates"][0]])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not RAW_CSV.exists():
        raise SystemExit(f"원본이 없습니다: {RAW_CSV}")
    with open(RAW_CSV, encoding="cp949", newline="") as f:
        rows = list(csv.DictReader(f))
    polygons, ids = load_polygons()
    report = build_report(rows, polygons, ids)
    print(f"원본 {report['raw_rows']}행 → 경남 22개 지역 판정 {len(report['kept'])}개 (정보수집일 {report['reference_dates']})")
    for rid in gn.REGION_IDS:
        other = sum(v for (r, _), v in report["other_registry"].items() if r == rid)
        print(f"  {gn.NAME_BY_ID[rid]:<10} {report['counts'][rid]:>5}  (다른 시·군 등록 제외 {other})")
    print(f"제외: {report['excluded']}")
    errors = consistency_errors(report)
    for e in errors:
        print(f"[오류] {e}")
    if errors:
        sys.exit(1)
    if args.write:
        write_outputs(report)
        print(f"저장: {STOPS_OUT.relative_to(PROJECT_ROOT)}, {COUNTS_OUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
