# 창원시 버스정류장 -> 5개 구 매핑 (법정동명 기반 1차 매칭 + 공간 매칭 2차 보강)
"""
data/raw/changwon_bus_stops.csv(창원시 버스정류소 위치정보)의 각 행을 창원시
5개 구(의창구/성산구/마산합포구/마산회원구/진해구) 중 하나로 분류하는 dry-run 스크립트.
CSV를 직접 수정하지 않으며, 집계 결과만 출력한다.

[1단계: 법정동명 매칭] (현재 구현됨)
    data/raw/bjd_code.csv(국토교통부_법정동코드, data.go.kr 15123287)를 참조표로 써서
    버스정류장 CSV의 `동코드` 컬럼(실제로는 법정동 "이름" 문자열, 예: "팔룡동")을
    "경상남도 창원시 {구} {법정동명}" 형태의 공식 법정동명과 매칭한다.
    - 법정동명이 창원시 내 정확히 1개 구에만 존재하면 그 구로 확정
    - 2개 구에 걸쳐 동명이 존재하면("상남동" 등) 보류(미분류)
    - `동코드`가 빈 값이면 애초에 매칭 시도를 하지 않고 미분류

[2단계: 공간(점-다각형) 매칭] (TODO - 공식 행정경계 폴리곤 확보 후 구현 예정)
    1단계에서 미분류로 남은 레코드(현재 기준 1,150건, 전부 위경도 유효함)를
    5개 구의 공식 경계 폴리곤과 Point-in-Polygon 검사로 보강 분류한다.
    아직 공식 경계 파일을 확보하지 못해 이 스크립트에는 구현하지 않았다.
    (필요 라이브러리: shapely만으로 충분. geopandas/fiona는 Windows에서 설치가
    번거로워 가급적 피한다. GeoJSON은 json 표준 라이브러리로 읽고
    shapely.geometry.shape()로 변환해서 쓰면 된다.)
    경계 파일이 확보되면 이 스크립트의 `spatial_match()` 함수를 채워 넣을 것.

[사용법]
    python scripts/map_bus_stops_to_districts.py
    (읽기 전용 dry-run. --commit 옵션이나 CSV 쓰기 로직은 의도적으로 넣지 않았다.
    bus_stop_count를 실제로 반영하는 건 별도 ingest 스크립트에서, 그리고 전체
    3,526건이 검증된 뒤에만 하기로 했다.)
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict

ROOT = r"C:\Gyeongnam-AI-SW-Competition-team_goyogoyo"
BUS_CSV = ROOT + r"\data\raw\changwon_bus_stops.csv"
BJD_CSV = ROOT + r"\data\raw\bjd_code.csv"

GUS = ["의창구", "성산구", "마산합포구", "마산회원구", "진해구"]


def load_dong_to_gu_mapping() -> tuple[dict[str, str], dict[str, set]]:
    """
    bjd_code.csv(국토교통부_법정동코드)에서 "경상남도 창원시" 소속 + 폐지여부=="존재"인
    행만 골라, 법정동명(마지막 토큰) -> 구 매핑을 만든다.

    Returns:
        (unique_dongs, ambiguous_dongs)
        - unique_dongs: {법정동명: 구} - 정확히 1개 구에만 대응하는 것만
        - ambiguous_dongs: {법정동명: {구, 구, ...}} - 2개 이상 구에 걸친 것
    """
    with open(BJD_CSV, encoding="cp949", newline="") as f:
        bjd_rows = list(csv.DictReader(f))

    active = [
        r for r in bjd_rows
        if "경상남도 창원시" in r["법정동명"] and r["폐지여부"] == "존재"
    ]

    dong_to_gu: dict[str, set] = defaultdict(set)
    for r in active:
        parts = r["법정동명"].split(" ")
        if len(parts) < 4:
            continue  # "경상남도 창원시", "경상남도 창원시 의창구" 같은 구 레벨 행 제외
        gu = parts[2]
        if gu not in GUS:
            continue
        dong_name = " ".join(parts[3:])
        dong_to_gu[dong_name].add(gu)

    unique_dongs = {k: next(iter(v)) for k, v in dong_to_gu.items() if len(v) == 1}
    ambiguous_dongs = {k: v for k, v in dong_to_gu.items() if len(v) > 1}
    return unique_dongs, ambiguous_dongs


def load_bus_stops() -> list[dict]:
    with open(BUS_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def has_valid_coord(row: dict) -> bool:
    try:
        lat, lon = float(row["위도"]), float(row["경도"])
    except (ValueError, KeyError):
        return False
    return 33 < lat < 39 and 124 < lon < 132  # 대한민국 영역 대략 범위


def classify_by_dong_name(
    bus_rows: list[dict], unique_dongs: dict[str, str], ambiguous_dongs: dict[str, set]
) -> dict:
    """
    1단계(법정동명) 매칭 결과를 반환한다.

    Returns:
        {
            "confirmed": {구: [row, ...], ...},
            "unmatched_blank": [row, ...],       # 동코드 자체가 빈 값
            "unmatched_unknown": [row, ...],     # 동코드는 있으나 법정동 목록에 없음
            "unmatched_ambiguous": [row, ...],   # 동코드가 2개 구에 걸친 모호한 동
        }
    """
    confirmed: dict[str, list] = {gu: [] for gu in GUS}
    unmatched_blank, unmatched_unknown, unmatched_ambiguous = [], [], []

    for row in bus_rows:
        dong = row["동코드"].strip()
        if not dong:
            unmatched_blank.append(row)
            continue
        if dong in ambiguous_dongs:
            unmatched_ambiguous.append(row)
            continue
        if dong not in unique_dongs:
            unmatched_unknown.append(row)
            continue
        confirmed[unique_dongs[dong]].append(row)

    return {
        "confirmed": confirmed,
        "unmatched_blank": unmatched_blank,
        "unmatched_unknown": unmatched_unknown,
        "unmatched_ambiguous": unmatched_ambiguous,
    }


def spatial_match(unmatched_rows: list[dict], boundary_geojson_path: str) -> dict[str, list]:
    """
    TODO: 공식 행정경계 GeoJSON을 확보한 뒤 구현.
    shapely로 5개 구 폴리곤을 읽어, unmatched_rows의 위경도가 정확히 1개 폴리곤
    안에 들어가면 그 구로 확정, 0개 또는 2개 이상이면 여전히 미분류로 남긴다.
    """
    raise NotImplementedError("공식 행정경계 데이터 확보 후 구현 예정")


def main() -> None:
    unique_dongs, ambiguous_dongs = load_dong_to_gu_mapping()
    print(
        f"[법정동 매핑표] 고유 법정동명 {len(unique_dongs) + len(ambiguous_dongs)}개 "
        f"(단일 구 대응 {len(unique_dongs)}개 / 모호 {len(ambiguous_dongs)}개)"
    )
    if ambiguous_dongs:
        print("  모호한 법정동명:", ambiguous_dongs)

    bus_rows = load_bus_stops()
    total = len(bus_rows)

    ids = [r["정류소아이디"] for r in bus_rows]
    assert len(ids) == len(set(ids)), "정류소아이디 중복 발견!"

    result = classify_by_dong_name(bus_rows, unique_dongs, ambiguous_dongs)
    confirmed_total = sum(len(v) for v in result["confirmed"].values())
    unmatched_all = (
        result["unmatched_blank"] + result["unmatched_unknown"] + result["unmatched_ambiguous"]
    )

    print(f"\n[버스정류장 CSV] 전체 {total}건, 정류소아이디 전부 고유 확인")
    print(f"\n=== 1단계(법정동명) 확정 매핑: {confirmed_total}/{total} ({confirmed_total/total*100:.1f}%) ===")
    for gu in GUS:
        print(f"  {gu}: {len(result['confirmed'][gu])}건")

    print(f"\n=== 미분류: {len(unmatched_all)}/{total} ({len(unmatched_all)/total*100:.1f}%) ===")
    print(f"  동코드 빈 값: {len(result['unmatched_blank'])}건")
    print(f"  동코드 있으나 법정동 목록에 없음: {len(result['unmatched_unknown'])}건")
    print(f"  2개 구에 걸친 모호한 동: {len(result['unmatched_ambiguous'])}건")

    spatial_candidates = [r for r in unmatched_all if has_valid_coord(r)]
    print(f"\n[2단계 공간 매칭 대상] 미분류 중 위경도 유효: {len(spatial_candidates)}건")
    print("  -> 공식 행정경계 GeoJSON을 data/raw/에 받은 뒤 spatial_match()를 구현하면 이어서 처리 가능")


if __name__ == "__main__":
    main()
