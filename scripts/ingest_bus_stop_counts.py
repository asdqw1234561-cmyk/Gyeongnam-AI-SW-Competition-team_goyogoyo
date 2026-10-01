# 창원시 버스정류소 위치정보 + SGIS 행정경계 -> data/region_indicators.csv의 bus_stop_count 반영
"""
지표 정의: "공공데이터에 등록된 좌표가 SGIS 창원시 행정경계 내부에 위치하는 버스정류장 수"

[집계 기준 - 채팅에서 합의된 내용 그대로]
    1. 정류소아이디를 집계 단위로 사용(정류소아이디는 3,526건 전부 고유).
    2. 법정동명(동코드)보다 SGIS 공식 행정경계의 공간 판정(점-다각형)을 우선한다.
    3. 단, 원본(단순화 전) SGIS SHP로 재검증한 결과 2m 단순화 오차로 확인된 3건
       (까치아파트입구/유목교/지귀상가, SPATIAL_OVERRIDES 참고)은 원본 SHP 판정인
       성산구로 보정해서 반영한다. 이 3건은 원본 SHP와 법정동명이 모두 성산구로
       일치했고, 커밋된 2m-단순화 GeoJSON만 경계선에 너무 가까워 의창구로 잘못
       판정했었다(scripts/map_bus_stops_to_districts.py 커밋 이력 참고).
    4. 그 외 법정동명과 공간판정이 다른 나머지 8건은 좌표를 임의로 고치지 않고
       공간판정 결과 그대로 집계에 포함하되, "데이터 품질 예외 목록"으로 별도 기록한다.
    5. 창원시 경계 밖으로 판정된 600건은 집계에서 제외한다(오류라고 단정하지 않음 -
       정의상 "경계 내부"만 세는 지표이므로 자연스럽게 빠지는 것).

[실행]
    python scripts/ingest_bus_stop_counts.py            # dry-run, 집계+검증만
    python scripts/ingest_bus_stop_counts.py --commit    # 검증 통과 시에만 CSV 반영

    기대 집계(2026-10-01 합의)와 재계산 결과가 다르면 --commit 여부와 무관하게
    에러를 내고 종료한다(절대 하드코딩된 숫자를 그대로 쓰지 않고 항상 원본에서 재계산).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import map_bus_stops_to_districts as m  # noqa: E402

INDICATORS_CSV = PROJECT_ROOT / "data" / "region_indicators.csv"

BUS_SOURCE_LABEL = "창원시 버스정류소 위치정보(data.go.kr 15037805) 및 국가데이터처 SGIS 행정경계"
BUS_REFERENCE_DATE = "2025-12-31 (버스정류소), 2025-06-30 (SGIS 행정경계)"

# 2m 단순화 GeoJSON에서는 의창구/성산구 경계선에 너무 가까워 의창구로 잘못 판정됐지만,
# 원본(단순화 전) SGIS SHP와 법정동명(동코드) 양쪽 모두 성산구로 일치한 3건.
# scripts/map_bus_stops_to_districts.py 커밋 이력의 재검증 결과를 그대로 반영한다.
SPATIAL_OVERRIDES: dict[str, str] = {
    "379000560": "성산구",  # 까치아파트입구 - 원본 SHP·법정동명(반지동) 모두 성산구
    "379001994": "성산구",  # 유목교 - 원본 SHP·법정동명(반지동) 모두 성산구
    "379000575": "성산구",  # 지귀상가 - 원본 SHP·법정동명(반지동) 모두 성산구
}

# 법정동명과 공간판정이 끝까지 다르게 나온 나머지 8건(위 3건 제외). 좌표를 임의로
# 고치지 않고 공간판정 결과 그대로 집계에 포함하되, 위치정보 신뢰도가 낮을 수 있는
# 예외로 별도 보존한다.
KNOWN_DATA_QUALITY_EXCEPTIONS: list[dict] = [
    {"정류소아이디": "379003422", "정류소명": "남문지구(임시)", "동코드": "북부동",
     "법정동판정": "진해구", "공간판정(채택)": "성산구", "비고": "특이사항 없음, 좌표 신뢰도 불명"},
    {"정류소아이디": "379000717", "정류소명": "창원공고.파티마병원", "동코드": "명서동",
     "법정동판정": "의창구", "공간판정(채택)": "성산구", "비고": "특이사항 없음, 좌표 신뢰도 불명"},
    {"정류소아이디": "379002383", "정류소명": "창원공고.홈플러스", "동코드": "명서동",
     "법정동판정": "의창구", "공간판정(채택)": "성산구", "비고": "특이사항 없음, 좌표 신뢰도 불명"},
    {"정류소아이디": "379000739", "정류소명": "홈플러스", "동코드": "명서동",
     "법정동판정": "의창구", "공간판정(채택)": "성산구", "비고": "특이사항 없음, 좌표 신뢰도 불명"},
    {"정류소아이디": "379000685", "정류소명": "대원현대사원아파트", "동코드": "대원동",
     "법정동판정": "성산구", "공간판정(채택)": "마산합포구",
     "비고": "좌표가 동마산시장/동민정공/마산소방서 등과 완전히 동일(원본 데이터 좌표 신뢰도 의심)"},
    {"정류소아이디": "379002357", "정류소명": "동민정공", "동코드": "팔용동",
     "법정동판정": "의창구", "공간판정(채택)": "마산합포구",
     "비고": "좌표가 대원현대사원아파트/동마산시장/마산소방서 등과 완전히 동일(원본 데이터 좌표 신뢰도 의심)"},
    {"정류소아이디": "379001008", "정류소명": "롯데캐슬프리미어아파트", "동코드": "회원동",
     "법정동판정": "마산회원구", "공간판정(채택)": "마산합포구", "비고": "특이사항 없음, 좌표 신뢰도 불명"},
    {"정류소아이디": "379000684", "정류소명": "마산시외버스터미널", "동코드": "합성동",
     "법정동판정": "마산회원구", "공간판정(채택)": "마산합포구",
     "비고": "좌표가 밤밭고개와 완전히 동일(원본 데이터 좌표 신뢰도 의심)"},
]

EXPECTED_COUNTS = {
    "CW-UICHANG": 831, "CW-SEONGSAN": 452, "CW-MASANHAPPO": 760,
    "CW-MASANHOEWON": 365, "CW-JINHAE": 518,
}
EXPECTED_TOTAL = 2926


def compute_counts() -> dict[str, int]:
    """
    원본 CSV + 검증된 경계 GeoJSON에서 '처음부터 다시' 구별 정류장 수를 계산한다.
    절대 기대값(EXPECTED_COUNTS)을 그대로 쓰지 않는다 - 아래 main()에서 둘을 비교해
    불일치하면 반영을 중단한다.
    """
    polygons, _meta = m.load_district_polygons()

    with open(m.BUS_CSV, encoding="utf-8-sig", newline="") as f:
        bus_rows = list(csv.DictReader(f))

    ids = [r["정류소아이디"] for r in bus_rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("정류소아이디 중복 발견 - 집계 중단")

    counts = {rid: 0 for rid in EXPECTED_COUNTS}
    outside_count = 0
    override_applied = set()

    for row in bus_rows:
        stop_id = row["정류소아이디"]

        if stop_id in SPATIAL_OVERRIDES:
            gu_name = SPATIAL_OVERRIDES[stop_id]
            region_id = m.GU_NAME_TO_REGION_ID[gu_name]
            counts[region_id] += 1
            override_applied.add(stop_id)
            continue

        valid, lat, lon = m.has_valid_coord(row)
        if not valid:
            outside_count += 1
            continue
        region_id, reason = m.classify_by_spatial(lat, lon, polygons)
        if region_id is None:
            outside_count += 1
            continue
        counts[region_id] += 1

    missing_overrides = set(SPATIAL_OVERRIDES) - override_applied
    if missing_overrides:
        raise SystemExit(f"보정 대상 정류소아이디를 CSV에서 찾지 못했습니다: {missing_overrides}")

    return counts, outside_count, len(bus_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="검증 통과 시 CSV에 실제로 반영")
    args = parser.parse_args()

    counts, outside_count, total_rows = compute_counts()

    print("=== 재계산된 구별 집계 (원본 CSV + 검증된 경계 GeoJSON 기준, 하드코딩 아님) ===")
    for region_id in ["CW-UICHANG", "CW-SEONGSAN", "CW-MASANHAPPO", "CW-MASANHOEWON", "CW-JINHAE"]:
        gu_name = next(k for k, v in m.GU_NAME_TO_REGION_ID.items() if v == region_id)
        print(f"  {region_id} ({gu_name}): {counts[region_id]}건  (합의된 기대값: {EXPECTED_COUNTS[region_id]}건)")
    total = sum(counts.values())
    print(f"  합계: {total}건 (합의된 기대값: {EXPECTED_TOTAL}건)")
    print(f"  경계 밖 제외: {outside_count}건")
    print(f"  전체 CSV 행 수: {total_rows}건 (검산: {total} + {outside_count} = {total + outside_count})")

    mismatches = [
        region_id for region_id in EXPECTED_COUNTS
        if counts[region_id] != EXPECTED_COUNTS[region_id]
    ]
    if mismatches or total != EXPECTED_TOTAL:
        print("\n[중단] 재계산 결과가 합의된 기대값과 다릅니다. CSV를 반영하지 않습니다.")
        print(f"  불일치 구: {mismatches}")
        raise SystemExit(1)

    print("\n[검증 통과] 재계산 결과가 합의된 기대값과 정확히 일치합니다.")

    print(f"\n=== 데이터 품질 예외 목록 ({len(KNOWN_DATA_QUALITY_EXCEPTIONS)}건, 집계에는 포함됨) ===")
    for ex in KNOWN_DATA_QUALITY_EXCEPTIONS:
        print(f"  {ex['정류소아이디']} {ex['정류소명']} | 법정동={ex['법정동판정']} vs 채택={ex['공간판정(채택)']} | {ex['비고']}")

    if not args.commit:
        print("\n--commit 옵션 없이 실행되어 CSV에는 반영하지 않았습니다 (dry-run).")
        return

    _write_bus_stop_counts_to_csv(counts)
    print("\ndata/region_indicators.csv 의 bus_stop_count 5건을 갱신했습니다 (data_status=확보).")


def _write_bus_stop_counts_to_csv(counts: dict[str, int]) -> None:
    exceptions_summary = ", ".join(
        f"{ex['정류소아이디']}({ex['정류소명']})" for ex in KNOWN_DATA_QUALITY_EXCEPTIONS
    )
    note = (
        "좌표(위도·경도) 기반 공간 매칭(SGIS 행정경계 점-다각형 판정)으로 집계. "
        "정류소아이디 기준, 창원시 행정경계 밖으로 판정된 600건은 제외. "
        "법정동명과 공간판정이 달라 위치정보 불일치 가능성이 있는 8건이 포함되어 있음: "
        f"{exceptions_summary}. "
        "버스정류장 개수는 대중교통 접근성의 참고 지표일 뿐 실제 출퇴근 소요시간을 뜻하지 않음."
    )

    with open(INDICATORS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    updated = 0
    for row in rows:
        if row["indicator_code"] == "bus_stop_count" and row["region_id"] in counts:
            row["value"] = str(counts[row["region_id"]])
            row["source"] = BUS_SOURCE_LABEL
            row["reference_date"] = BUS_REFERENCE_DATE
            row["data_status"] = "확보"
            row["note"] = note
            updated += 1

    if updated != len(counts):
        raise SystemExit(
            f"bus_stop_count 행을 {updated}개만 찾았습니다(기대: {len(counts)}). "
            "data/region_indicators.csv 구조가 바뀌지 않았는지 확인하세요."
        )

    # hospital_count 등 다른 지표 행은 절대 건드리지 않는다(의료기관 데이터 보존)
    with open(INDICATORS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
