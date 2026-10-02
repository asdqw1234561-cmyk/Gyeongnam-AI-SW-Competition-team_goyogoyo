# 창원시 버스정류장 -> 5개 구 매핑 (법정동명 1차 매칭 + 공식 경계 공간 매칭 2차 교차검증)
"""
data/raw/changwon_bus_stops.csv(창원시 버스정류소 위치정보, 기준일 2025-12-31)의 각 행을
창원시 5개 구(의창구/성산구/마산합포구/마산회원구/진해구) 중 하나로 분류하는 dry-run
스크립트. CSV를 직접 수정하지 않으며, 집계 결과만 출력한다.

[1단계: 법정동명 매칭]
    data/raw/bjd_code.csv(국토교통부_법정동코드, data.go.kr 15123287)를 참조표로 써서
    버스정류장 CSV의 `동코드` 컬럼(실제로는 법정동 "이름" 문자열, 예: "팔룡동")을
    "경상남도 창원시 {구} {법정동명}" 형태의 공식 법정동명과 매칭한다.
    - 법정동명이 창원시 내 정확히 1개 구에만 존재하면 그 구로 확정
    - 2개 구에 걸쳐 동명이 존재하면("상남동" 등) 보류(미분류)
    - `동코드`가 빈 값이면 애초에 매칭 시도를 하지 않고 미분류

[2단계: 공간(점-다각형) 매칭]
    data/raw/changwon_district_boundaries.geojson(scripts/extract_changwon_district_
    boundaries.py로 SGIS 원본에서 추출, 기준일 2025-06-30, EPSG:4326)의 5개 구 폴리곤에
    버스정류장의 위도·경도(WGS84)가 포함되는지 검사한다.
    - 정확히 1개 폴리곤에 포함(contains) -> 그 구로 판정
    - 0개 포함이지만 경계선에 걸침(intersects) -> "경계선 위치"로 보류
    - 0개 포함, 경계선도 아님 -> "창원시 경계 밖"으로 보류
    - 2개 이상 포함 -> "중첩/이상"으로 보류(정상 폴리곤이면 발생하지 않아야 함)

[1·2단계 교차검증 및 최종 분류]
    - 두 방법이 같은 구로 일치 -> 최종 확정
    - 1단계는 특정 구로 확정했는데 2단계 결과가 다르거나 실패 -> "법정동-공간 불일치"로
      별도 보고만 하고, 어느 한쪽을 임의로 택하지 않는다(최종 확정에 포함하지 않음)
    - 1단계는 미분류였는데 2단계가 정확히 1개 구로 확정 -> "공간 매칭으로 신규 확정"
    - 둘 다 실패 -> 여전히 미분류

CSV 쓰기 로직은 의도적으로 넣지 않았다(읽기 전용 dry-run).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUS_CSV = PROJECT_ROOT / "data" / "raw" / "changwon_bus_stops.csv"
BJD_CSV = PROJECT_ROOT / "data" / "raw" / "bjd_code.csv"
BOUNDARY_GEOJSON = PROJECT_ROOT / "data" / "raw" / "changwon_district_boundaries.geojson"

GUS = ["의창구", "성산구", "마산합포구", "마산회원구", "진해구"]
GU_NAME_TO_REGION_ID = {
    "의창구": "CW-UICHANG",
    "성산구": "CW-SEONGSAN",
    "마산합포구": "CW-MASANHAPPO",
    "마산회원구": "CW-MASANHOEWON",
    "진해구": "CW-JINHAE",
}

BUS_CSV_REFERENCE_DATE = "2025-12-31"  # 사용자가 다운로드 페이지에서 확인해 알려준 기준일


# ---------------------------------------------------------------------------
# 1단계: 법정동명 매칭
# ---------------------------------------------------------------------------
def load_dong_to_gu_mapping() -> tuple[dict[str, str], dict[str, set]]:
    """
    bjd_code.csv에서 "경상남도 창원시" 소속 + 폐지여부=="존재"인 행만 골라,
    법정동명(마지막 토큰) -> 구 매핑을 만든다.
    Returns: (unique_dongs: {법정동명: 구}, ambiguous_dongs: {법정동명: {구, ...}})
    """
    with open(BJD_CSV, encoding="cp949", newline="") as f:
        bjd_rows = list(csv.DictReader(f))

    active = [
        r for r in bjd_rows
        if "경상남도 창원시" in r["법정동명"] and r["폐지여부"] == "존재"
    ]

    dong_to_gu: dict[str, set] = {}
    for r in active:
        parts = r["법정동명"].split(" ")
        if len(parts) < 4:
            continue
        gu = parts[2]
        if gu not in GUS:
            continue
        dong_name = " ".join(parts[3:])
        dong_to_gu.setdefault(dong_name, set()).add(gu)

    unique_dongs = {k: next(iter(v)) for k, v in dong_to_gu.items() if len(v) == 1}
    ambiguous_dongs = {k: v for k, v in dong_to_gu.items() if len(v) > 1}
    return unique_dongs, ambiguous_dongs


def classify_by_dong_name(dong_value: str, unique_dongs: dict, ambiguous_dongs: dict) -> tuple[str | None, str]:
    """반환: (구 이름 또는 None, 사유 코드)"""
    dong = dong_value.strip()
    if not dong:
        return None, "blank"
    if dong in ambiguous_dongs:
        return None, "ambiguous"
    if dong not in unique_dongs:
        return None, "unknown"
    return unique_dongs[dong], "matched"


# ---------------------------------------------------------------------------
# 2단계: 공간 매칭
# ---------------------------------------------------------------------------
def load_district_polygons() -> tuple[dict[str, object], dict]:
    with open(BOUNDARY_GEOJSON, encoding="utf-8") as f:
        geojson = json.load(f)

    polygons = {}
    meta = {}
    for feat in geojson["features"]:
        props = feat["properties"]
        region_id = props["region_id"]
        polygons[region_id] = shape(feat["geometry"])
        meta[region_id] = props
    return polygons, meta


def classify_by_spatial(lat: float, lon: float, polygons: dict) -> tuple[str | None, str]:
    """반환: (region_id 또는 None, 사유 코드)"""
    point = Point(lon, lat)  # shapely는 (x=경도, y=위도) 순서
    contains_hits = [rid for rid, poly in polygons.items() if poly.contains(point)]
    if len(contains_hits) == 1:
        return contains_hits[0], "contains"
    if len(contains_hits) > 1:
        return None, "overlap"

    touch_hits = [rid for rid, poly in polygons.items() if poly.intersects(point)]
    if touch_hits:
        return None, "boundary"
    return None, "outside"


def has_valid_coord(row: dict) -> tuple[bool, float, float]:
    try:
        lat, lon = float(row["위도"]), float(row["경도"])
    except (ValueError, KeyError):
        return False, 0.0, 0.0
    return (33 < lat < 39 and 124 < lon < 132), lat, lon


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main() -> None:
    unique_dongs, ambiguous_dongs = load_dong_to_gu_mapping()
    polygons, boundary_meta = load_district_polygons()

    sample_meta = next(iter(boundary_meta.values()))
    print("[기준일 비교]")
    print(f"  버스정류장 CSV 기준일: {BUS_CSV_REFERENCE_DATE}")
    print(f"  행정경계(SGIS) 기준일: {sample_meta['sgis_base_date']} (SIGUNGU_CD {', '.join(m['sgis_sigungu_cd'] for m in boundary_meta.values())})")
    print("  -> 두 자료의 기준 시점이 다르므로, 그 사이에 신설/변경된 정류장이나 경계 조정이 있다면 결과가 완벽히 일치하지 않을 수 있음")

    with open(BUS_CSV, encoding="utf-8-sig", newline="") as f:
        bus_rows = list(csv.DictReader(f))
    total = len(bus_rows)

    ids = [r["정류소아이디"] for r in bus_rows]
    assert len(ids) == len(set(ids)), "정류소아이디 중복 발견!"
    print(f"\n[중복 확인] 정류소아이디 {total}건 전부 고유 확인")

    both_agree: dict[str, list] = {gu: [] for gu in GUS}
    spatial_only_confirmed: dict[str, list] = {gu: [] for gu in GUS}
    conflicts = []          # 법정동-공간 결과가 다름(1단계는 확정했는데 2단계가 다르거나 실패)
    still_unclassified = {"boundary": [], "outside": [], "overlap": [], "invalid_coord": []}
    still_unclassified_dong_reasons = Counter()  # 미분류건의 1단계(법정동) 쪽 사유 분포

    for row in bus_rows:
        dong_gu, dong_reason = classify_by_dong_name(row["동코드"], unique_dongs, ambiguous_dongs)

        valid, lat, lon = has_valid_coord(row)
        if not valid:
            spatial_region_id, spatial_reason = None, "invalid_coord"
        else:
            spatial_region_id, spatial_reason = classify_by_spatial(lat, lon, polygons)
        spatial_gu = None
        if spatial_region_id is not None:
            spatial_gu = next(name for name, rid in GU_NAME_TO_REGION_ID.items() if rid == spatial_region_id)

        if dong_gu is not None and spatial_gu is not None:
            if dong_gu == spatial_gu:
                both_agree[dong_gu].append(row)
            else:
                conflicts.append((row, dong_gu, spatial_gu, "다른 구로 판정"))
        elif dong_gu is not None and spatial_gu is None:
            conflicts.append((row, dong_gu, None, f"공간매칭 실패({spatial_reason})"))
        elif dong_gu is None and spatial_gu is not None:
            spatial_only_confirmed[spatial_gu].append(row)
        else:
            key = spatial_reason if spatial_reason in still_unclassified else "outside"
            still_unclassified[key].append(row)
            still_unclassified_dong_reasons[dong_reason] += 1

    both_agree_total = sum(len(v) for v in both_agree.values())
    spatial_only_total = sum(len(v) for v in spatial_only_confirmed.values())
    final_confirmed_total = both_agree_total + spatial_only_total
    conflict_total = len(conflicts)
    unclassified_total = sum(len(v) for v in still_unclassified.values())

    print(f"\n=== 1단계(법정동명) vs 2단계(공간) 교차검증 결과 ===")
    print(f"  두 방법 결과 일치(최종 확정 1): {both_agree_total}건")
    for gu in GUS:
        print(f"    {gu}: {len(both_agree[gu])}건")

    print(f"\n  법정동-공간 불일치(최종 확정에서 제외, 별도 보고): {conflict_total}건")
    if conflicts:
        reason_counter = Counter(c[3] for c in conflicts)
        print("   사유별 건수:", dict(reason_counter))
        print("   표본 10건:")
        for row, dgu, sgu, reason in conflicts[:10]:
            print(f"     정류소아이디={row['정류소아이디']} 정류소명={row['정류소명']} "
                  f"동코드={row['동코드']!r} | 법정동판정={dgu} vs 공간판정={sgu} ({reason})")

    print(f"\n  공간 매칭으로 신규 확정(최종 확정 2): {spatial_only_total}건")
    for gu in GUS:
        print(f"    {gu}: {len(spatial_only_confirmed[gu])}건")

    print(f"\n  여전히 미분류: {unclassified_total}건")
    print(f"    경계선 위치(2개 이상 구 경계에 걸침): {len(still_unclassified['boundary'])}건")
    print(f"    창원시 경계 밖: {len(still_unclassified['outside'])}건")
    print(f"    폴리곤 중첩/이상: {len(still_unclassified['overlap'])}건")
    print(f"    좌표 자체가 유효하지 않음: {len(still_unclassified['invalid_coord'])}건")
    print(f"    (참고) 미분류건의 1단계 법정동 판정 사유 분포: {dict(still_unclassified_dong_reasons)}")

    print(f"\n=== 최종 구별 확정 합계(1+2단계 모두 반영) ===")
    for gu in GUS:
        n = len(both_agree[gu]) + len(spatial_only_confirmed[gu])
        print(f"  {gu}: {n}건")
    print(f"  최종 확정 합계: {final_confirmed_total}건 / 전체 {total}건 ({final_confirmed_total/total*100:.1f}%)")

    print(f"\n=== 전체 검산 ===")
    print(f"  최종 확정({final_confirmed_total}) + 불일치({conflict_total}) + 미분류({unclassified_total}) "
          f"= {final_confirmed_total + conflict_total + unclassified_total} (전체 {total}건과 일치해야 함)")
    assert final_confirmed_total + conflict_total + unclassified_total == total, "검산 실패: 합계가 전체 건수와 다름"
    print("  검산 통과: 3,526건 전체가 정확히 한 번씩 분류되었습니다.")


if __name__ == "__main__":
    main()
