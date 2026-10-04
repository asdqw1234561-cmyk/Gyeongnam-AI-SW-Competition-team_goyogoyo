# 창원시 5개 구 초·중·고등학교 수 집계 (G5-A, scoring 미연결)
"""
data/raw/전국초중등학교위치표준데이터.csv(공공데이터포털 15021148, 한국교육시설안전원 제공,
사용자가 내려받아 둔 원본)를 읽어 창원시 5개 구의 학교급별 학교 수를 집계한다.

원본은 읽기만 한다(cp949). 결과는 data/schools/ 아래에 따로 쓴다.
region_indicators.csv·scoring.py에는 아직 연결하지 않는다(G5-B, 사용자 승인 후).

[구 판정]
1. 소재지도로명주소가 "경상남도 창원시 <구> "로 시작하는 행만 창원 학교로 본다.
   "창원" 부분 문자열로 거르면 서울 "효창원로", 단양 "별방창원로" 같은 다른 지역이 섞인다.
2. 교차검증 (하나라도 어긋나면 집계를 멈춘다 - 추정으로 고치지 않는다)
   - 소재지지번주소의 구가 도로명주소의 구와 같은가
   - 위도·경도의 SGIS 행정경계 점-다각형 판정(버스정류장과 같은 방식)이 같은 구인가
   - 교육지원청명이 경상남도창원교육지원청인가
   - 원본 전체에서 창원교육지원청 소관 학교가 모두 추출됐는가

[제외 기준] 원본 컬럼 값으로 명확히 구분되는 경우만 제외한다.
   - 운영상태가 "운영"이 아닌 행
   - 학교급구분이 초등학교·중학교·고등학교가 아닌 행
   학교명으로 특수학교 등을 추정해 빼지 않는다. 분교(본교분교구분)는 운영 중인 학교이므로 포함하고 수만 따로 기록한다.

[사용법]
    python scripts/ingest_school_counts.py           # 집계 + 검증 결과만 출력
    python scripts/ingest_school_counts.py --write   # 검증 통과 시 data/schools/ 에 결과 저장
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import map_bus_stops_to_districts as geo_map  # noqa: E402  (SGIS 경계·공간판정 재사용)

RAW_CSV = PROJECT_ROOT / "data" / "raw" / "전국초중등학교위치표준데이터.csv"
RAW_ENCODING = "cp949"
OUT_DIR = PROJECT_ROOT / "data" / "schools"
SCHOOLS_OUT = OUT_DIR / "changwon_schools.csv"
COUNTS_OUT = OUT_DIR / "changwon_school_counts.csv"

SOURCE_LABEL = "전국초중등학교위치표준데이터 (공공데이터포털 15021148, 제공 한국교육시설안전원)"
CHANGWON_OFFICE = "경상남도창원교육지원청"
OPERATING = "운영"
SCHOOL_LEVELS = ("초등학교", "중학교", "고등학교")
GUS = geo_map.GUS
GU_NAME_TO_REGION_ID = geo_map.GU_NAME_TO_REGION_ID

ROAD_ADDR_PATTERN = re.compile(r"^경상남도 창원시 (" + "|".join(GUS) + r") ")
JIBUN_ADDR_PATTERN = re.compile(r"^경상남도 창원시 (" + "|".join(GUS) + r") ")

# 2026-03-20 기준 원본으로 확인한 값(검증용 상수). 원본이 바뀌면 다시 확인해 갱신한다.
EXPECTED_COUNTS = {
    "의창구": {"초등학교": 26, "중학교": 13, "고등학교": 11},
    "성산구": {"초등학교": 24, "중학교": 17, "고등학교": 14},
    "마산합포구": {"초등학교": 23, "중학교": 11, "고등학교": 10},
    "마산회원구": {"초등학교": 20, "중학교": 13, "고등학교": 7},
    "진해구": {"초등학교": 20, "중학교": 11, "고등학교": 6},
}
EXPECTED_TOTAL = 226


def load_raw_rows(path: Path = RAW_CSV) -> list[dict]:
    with open(path, encoding=RAW_ENCODING, newline="") as f:
        return list(csv.DictReader(f))


def road_gu(row: dict) -> str | None:
    m = ROAD_ADDR_PATTERN.match(row.get("소재지도로명주소", ""))
    return m.group(1) if m else None


def jibun_gu(row: dict) -> str | None:
    m = JIBUN_ADDR_PATTERN.match(row.get("소재지지번주소", ""))
    return m.group(1) if m else None


def spatial_gu(row: dict, polygons: dict) -> tuple[str | None, str]:
    ok, lat, lon = geo_map.has_valid_coord(row)
    if not ok:
        return None, "invalid_coord"
    region_id, reason = geo_map.classify_by_spatial(lat, lon, polygons)
    if region_id is None:
        return None, reason
    gu = next(name for name, rid in GU_NAME_TO_REGION_ID.items() if rid == region_id)
    return gu, reason


def build_report(rows: list[dict], polygons: dict | None) -> dict:
    """원본 행 → 창원 추출 → 교차검증 → 제외 → 구·학교급별 집계. 숫자 계산만 하고 쓰지 않는다."""
    changwon = [(row, road_gu(row)) for row in rows]
    changwon = [(row, gu) for row, gu in changwon if gu]

    issues: list[dict] = []
    for row, gu in changwon:
        name = row.get("학교명", "")
        jg = jibun_gu(row)
        if jg != gu:
            issues.append({"학교명": name, "check": "지번주소 구 불일치", "road": gu, "other": jg})
        if row.get("교육지원청명") != CHANGWON_OFFICE:
            issues.append({"학교명": name, "check": "교육지원청 불일치", "road": gu, "other": row.get("교육지원청명")})
        if polygons is not None:
            sg, reason = spatial_gu(row, polygons)
            if sg != gu:
                issues.append({"학교명": name, "check": f"좌표 공간판정 불일치({reason})", "road": gu, "other": sg})

    extracted_ids = {row.get("학교ID") for row, _ in changwon}
    office_only = [
        row for row in rows
        if row.get("교육지원청명") == CHANGWON_OFFICE and row.get("학교ID") not in extracted_ids
    ]
    for row in office_only:
        issues.append({"학교명": row.get("학교명", ""), "check": "창원교육지원청 소관인데 주소로 추출 안 됨",
                       "road": None, "other": row.get("소재지도로명주소")})

    excluded = Counter()
    included: list[tuple[dict, str]] = []
    for row, gu in changwon:
        if row.get("운영상태") != OPERATING:
            excluded[f"운영상태={row.get('운영상태')}"] += 1
        elif row.get("학교급구분") not in SCHOOL_LEVELS:
            excluded[f"학교급구분={row.get('학교급구분')}"] += 1
        else:
            included.append((row, gu))

    counts = {gu: {level: 0 for level in SCHOOL_LEVELS} for gu in GUS}
    branch = {gu: 0 for gu in GUS}
    for row, gu in included:
        counts[gu][row["학교급구분"]] += 1
        if row.get("본교분교구분") == "분교":
            branch[gu] += 1

    totals = {gu: sum(counts[gu].values()) for gu in GUS}
    reference_dates = sorted({row.get("데이터기준일자", "") for row, _ in changwon})
    providers = sorted({row.get("제공기관명", "") for row, _ in changwon})

    return {
        "raw_rows": len(rows),
        "changwon_rows": len(changwon),
        "excluded": dict(excluded),
        "included_rows": len(included),
        "counts": counts,
        "totals": totals,
        "grand_total": sum(totals.values()),
        "branch_schools": branch,
        "reference_dates": reference_dates,
        "providers": providers,
        "issues": issues,
        "included": included,
    }


def consistency_errors(report: dict) -> list[str]:
    """행 수·합계가 서로 맞는지, 기대값과 같은지. 비어 있으면 통과."""
    errors = []
    if report["issues"]:
        errors.append(f"구 매핑 교차검증 실패 {len(report['issues'])}건")
    if report["changwon_rows"] != report["included_rows"] + sum(report["excluded"].values()):
        errors.append("창원 추출 행 수 ≠ 포함 + 제외")
    if report["grand_total"] != report["included_rows"]:
        errors.append("구별 합계 ≠ 포함 행 수")
    for gu in GUS:
        if sum(report["counts"][gu].values()) != report["totals"][gu]:
            errors.append(f"{gu} 학교급 합 ≠ 총합")
    if len(report["reference_dates"]) != 1:
        errors.append(f"데이터기준일자가 하나가 아님: {report['reference_dates']}")
    if report["counts"] != EXPECTED_COUNTS or report["grand_total"] != EXPECTED_TOTAL:
        errors.append("기대 집계(EXPECTED_COUNTS)와 다름 - 원본이 바뀌었으면 확인 후 상수 갱신")
    return errors


def write_outputs(report: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reference_date = report["reference_dates"][0]
    with open(SCHOOLS_OUT, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["region_id", "region_name", "학교ID", "학교명", "학교급구분", "설립형태",
                         "본교분교구분", "운영상태", "소재지도로명주소", "위도", "경도", "데이터기준일자"])
        for row, gu in sorted(report["included"], key=lambda x: (GUS.index(x[1]), x[0]["학교급구분"], x[0]["학교명"])):
            writer.writerow([GU_NAME_TO_REGION_ID[gu], gu, row["학교ID"], row["학교명"], row["학교급구분"],
                             row["설립형태"], row["본교분교구분"], row["운영상태"], row["소재지도로명주소"],
                             row["위도"], row["경도"], row["데이터기준일자"]])
    with open(COUNTS_OUT, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["region_id", "region_name", "elementary_school_count", "middle_school_count",
                         "high_school_count", "school_count", "branch_school_count", "source", "reference_date"])
        for gu in GUS:
            c = report["counts"][gu]
            writer.writerow([GU_NAME_TO_REGION_ID[gu], gu, c["초등학교"], c["중학교"], c["고등학교"],
                             report["totals"][gu], report["branch_schools"][gu], SOURCE_LABEL, reference_date])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="검증 통과 시 data/schools/ 에 결과 저장")
    args = parser.parse_args()

    polygons, _ = geo_map.load_district_polygons()
    report = build_report(load_raw_rows(), polygons)

    print(f"원본 행 수: {report['raw_rows']}")
    print(f"창원 5개 구 추출 행 수(도로명주소 기준): {report['changwon_rows']}")
    print(f"제외: {report['excluded'] or '없음'} / 포함: {report['included_rows']}")
    print(f"데이터기준일자: {report['reference_dates']} / 제공기관: {report['providers']}")
    for gu in GUS:
        c = report["counts"][gu]
        print(f"  {gu}: 초 {c['초등학교']} · 중 {c['중학교']} · 고 {c['고등학교']} = {report['totals'][gu]}"
              f" (분교 {report['branch_schools'][gu]})")
    print(f"합계: {report['grand_total']}")
    for issue in report["issues"]:
        print(f"  [검증 실패] {issue}")

    errors = consistency_errors(report)
    if errors:
        for e in errors:
            print(f"[오류] {e}")
        sys.exit(1)
    print("검증 통과: 지번주소·좌표 공간판정·교육지원청 교차검증 일치, 행 수·합계 일치")
    if args.write:
        write_outputs(report)
        print(f"저장: {SCHOOLS_OUT.relative_to(PROJECT_ROOT)}, {COUNTS_OUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
