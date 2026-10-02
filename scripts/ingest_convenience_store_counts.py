# 창원시 편의점 집계(hwang 브랜치에서 수집한 CSV) -> data/region_indicators.csv 반영
"""
data/convenience/changwon_convenience_counts.csv(황승구가 소상공인시장진흥공단
상가(상권)정보 API로 이미 수집해 둔 구별 집계)를 읽어 region_indicators.csv의
convenience_store_count 5개 행에 반영하는 스크립트.

API를 다시 호출하지 않는다 - 이미 수집되어 완전성 검증(5개 구 전부 확보)을 통과한
CSV를 그대로 쓴다. mart_count는 이번 수집 범위가 아니므로 건드리지 않는다
(미확보 상태 그대로 유지).

[사용법]
    python scripts/ingest_convenience_store_counts.py           # dry-run, 집계+검증만
    python scripts/ingest_convenience_store_counts.py --commit  # 검증 통과 시에만 CSV 반영

    기대 집계(2026-10-01 기준, docs/convenience_data.md에 기록된 값)와 CSV에서 읽은
    값이 다르면 --commit 여부와 무관하게 에러를 내고 종료한다.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COUNTS_CSV = PROJECT_ROOT / "data" / "convenience" / "changwon_convenience_counts.csv"
INDICATORS_CSV = PROJECT_ROOT / "data" / "region_indicators.csv"

EXPECTED_COUNTS = {
    "CW-UICHANG": 199, "CW-SEONGSAN": 245, "CW-MASANHAPPO": 159,
    "CW-MASANHOEWON": 151, "CW-JINHAE": 196,
}
EXPECTED_TOTAL = 950

SOURCE_LABEL = "소상공인시장진흥공단 상가(상권)정보 API (data.go.kr 15012005)"
REFERENCE_DATE = "2026-06 (기준년월)"
NOTE = (
    "상가(상권)정보에 등록된 업소 기준 집계(상권업종 소분류 '편의점'). "
    "등록 업소 수이므로 폐업 반영 지연이나 동일 주소 중복 등록(같은 매장이 "
    "다른 이름으로 중복 등록된 경우 포함 가능) 등으로 실제 영업 매장 수와 "
    "다를 수 있음. 자세한 수집·검증 과정은 docs/convenience_data.md 참고."
)


def read_counts_from_csv() -> dict[str, int]:
    """
    changwon_convenience_counts.csv에서 facility_type='편의점' 행만 읽어
    region_id -> count 를 돌려준다. 하나라도 '확보'가 아니거나 비어 있으면 중단한다.
    """
    if not COUNTS_CSV.exists():
        raise SystemExit(f"수집된 CSV를 찾을 수 없습니다: {COUNTS_CSV}")

    with open(COUNTS_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    counts: dict[str, int] = {}
    for row in rows:
        if row["facility_type"] != "편의점":
            continue
        region_id = row["region_id"]
        if region_id not in EXPECTED_COUNTS:
            continue
        if row["data_status"] != "확보":
            raise SystemExit(f"{region_id}({row['district']}) 편의점 집계가 '확보' 상태가 아닙니다: {row}")
        if not row["count"].strip():
            raise SystemExit(f"{region_id}({row['district']}) 편의점 count 값이 비어 있습니다: {row}")
        counts[region_id] = int(row["count"])

    missing = set(EXPECTED_COUNTS) - set(counts)
    if missing:
        raise SystemExit(f"다음 구의 편의점 집계를 CSV에서 찾지 못했습니다: {sorted(missing)}")

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="검증 통과 시 CSV에 실제로 반영")
    args = parser.parse_args()

    counts = read_counts_from_csv()

    print("=== changwon_convenience_counts.csv에서 읽은 구별 편의점 수 ===")
    for region_id, expected in EXPECTED_COUNTS.items():
        actual = counts[region_id]
        print(f"  {region_id}: {actual}건  (기대값: {expected}건)")
    total = sum(counts.values())
    print(f"  합계: {total}건 (기대값: {EXPECTED_TOTAL}건)")

    mismatches = [rid for rid in EXPECTED_COUNTS if counts[rid] != EXPECTED_COUNTS[rid]]
    if mismatches or total != EXPECTED_TOTAL:
        print("\n[중단] CSV 값이 합의된 기대값과 다릅니다. region_indicators.csv를 반영하지 않습니다.")
        print(f"  불일치 구: {mismatches}")
        raise SystemExit(1)

    print("\n[검증 통과] CSV 값이 합의된 기대값과 정확히 일치합니다.")

    if not args.commit:
        print("\n--commit 옵션 없이 실행되어 region_indicators.csv에는 반영하지 않았습니다 (dry-run).")
        return

    _write_convenience_counts_to_csv(counts)
    print("\ndata/region_indicators.csv 의 convenience_store_count 5건을 갱신했습니다 (data_status=확보).")


def _write_convenience_counts_to_csv(counts: dict[str, int]) -> None:
    with open(INDICATORS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    updated = 0
    for row in rows:
        if row["indicator_code"] == "convenience_store_count" and row["region_id"] in counts:
            row["value"] = str(counts[row["region_id"]])
            row["source"] = SOURCE_LABEL
            row["reference_date"] = REFERENCE_DATE
            row["data_status"] = "확보"
            row["note"] = NOTE
            updated += 1

    if updated != len(counts):
        raise SystemExit(
            f"convenience_store_count 행을 {updated}개만 찾았습니다(기대: {len(counts)}). "
            "data/region_indicators.csv 구조가 바뀌지 않았는지 확인하세요."
        )

    # mart_count, hospital_count, bus_stop_count 등 다른 지표 행은 절대 건드리지 않는다
    with open(INDICATORS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
