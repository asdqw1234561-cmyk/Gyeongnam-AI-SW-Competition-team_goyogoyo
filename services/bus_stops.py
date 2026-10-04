# 창원시 버스정류장 위치 기반 조회 서비스
"""
scripts/ingest_bus_stop_counts.py가 data/region_indicators.csv의 bus_stop_count
(구별 집계)를 만들 때 쓰는 것과 **완전히 동일한 공간 판정 기준**으로, 원본
data/raw/changwon_bus_stops.csv(3,526건)에서 "창원시 5개 구 경계 내부"로 판정된
2,926건만 걸러낸 뒤, 특정 좌표 주변 반경 안의 정류장을 직선거리 기준으로 조회한다.

이 모듈은 구별 집계 지표(bus_stop_count)를 새로 계산하지 않는다 - 기존 지표는
data/region_indicators.csv에 그대로 남아 있고(analysis/scoring.py가 계속 사용),
여기서는 "관심 위치 주변에 정류장이 몇 개 있는가"라는 별개의 질문에만 답한다.

[집계 기준 재사용 - scripts/ingest_bus_stop_counts.py의 compute_counts()와 동일]
    1. 정류소아이디(원본 3,526건 전부 고유)를 집계 단위로 쓴다. 같은 정류소아이디를
       중복 집계하지 않고, 서로 다른 정류소아이디를 임의로 합치지도 않는다.
    2. SPATIAL_OVERRIDES(3건, 2m 단순화 GeoJSON 오차 보정 - 성산구로 확정)는
       scripts/ingest_bus_stop_counts.py의 값을 그대로 가져와 똑같이 적용한다.
    3. 나머지는 scripts/map_bus_stops_to_districts.py의 classify_by_spatial()
       (SGIS 행정경계 점-다각형 판정)로 소속 구를 정한다. 법정동명 매칭은 최종
       판정에 쓰지 않는다(공식 집계도 공간판정을 우선시했다).
    4. 좌표가 유효하지 않거나(has_valid_coord 실패) 창원시 5개 구 경계 밖으로
       판정되면(폴리곤 밖/경계선 위/2개 이상 폴리곤과 중첩) 제외한다 - 기존
       "경계 밖 600건 제외"와 동일한 기준.
    5. 법정동명과 공간판정이 끝까지 달랐던 나머지 8건(KNOWN_DATA_QUALITY_EXCEPTIONS)
       은 좌표를 임의로 고치지 않고 공간판정 결과 그대로 포함한다 - 기존과 동일하게
       "위치정보 불일치 가능성" 예외로 표시만 하고 집계에서 빼지 않는다.

    이 기준으로 다시 세면 의창구 831 / 성산구 452 / 마산합포구 760 / 마산회원구 365 /
    진해구 518, 합계 2,926건이어야 한다. verify_official_counts()가 원본 데이터로
    매번 다시 계산해서 이 값과 비교해 돌려준다 - 기대값에 맞추기 위해 레코드를
    임의로 추가/삭제하지 않는다. 숫자가 다르면 이 함수가 그 사실을 그대로 보고하며,
    tests/test_bus_stops.py가 이를 테스트 실패로 연결한다.

[원본 데이터 보호]
    data/raw/changwon_bus_stops.csv, data/raw/changwon_district_boundaries.geojson
    둘 다 이 모듈에서 전혀 쓰지(write) 않는다 - 읽기만 한다.

[주변 조회(find_nearby_bus_stops / count_nearby_by_radius) 거리]
    두 좌표 사이의 **직선거리**(하버사인 공식)다. 실제 도보거리나 대중교통
    이동시간이 아니므로 화면에도 반드시 "직선거리"로 표기한다 - services/convenience.py
    의 주변 편의점 조회(find_nearby_stores)와 같은 공식·같은 지구 반지름을 쓰고,
    프리셋 반경(300/500/1,000m)·최대 반경·최대 표시 개수도 전부 맞춰서, 버스정류장과
    편의점을 같은 검색 중심 좌표·반경으로 비교할 수 있게 한다.

[캐싱]
    원본 CSV(3,526행)에 대해 5개 구 폴리곤 point-in-polygon 판정을 매번 다시 하면
    Streamlit이 재실행될 때마다 비용이 든다. 두 원본 파일의 수정 시각을 캐시 키로
    써서, 파일이 바뀌지 않는 한 재계산하지 않는다(services/convenience.py의
    _load_store_coords()와 동일한 패턴) - 파일이 바뀌면 자동으로 다시 계산한다.
"""

from __future__ import annotations

import csv
import os
import sys
from typing import Optional

import pandas as pd

from services.geo import GYEONGNAM_BBOX as _GYEONGNAM_BBOX
from services.geo import DISTRICTS, EARTH_RADIUS_M  # noqa: F401  (기존 공개 이름 유지)
from services.geo import haversine_m as _haversine_m
from services.geo import to_float as _to_float
from services.geo import to_int as _to_int

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_SCRIPTS_DIR = os.path.join(_PROJECT_ROOT, "scripts")

if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import ingest_bus_stop_counts as _ingest  # noqa: E402  (공식 집계 스크립트 - 기준치/보정값 재사용)
import map_bus_stops_to_districts as _m  # noqa: E402  (공간 판정 함수 재사용)

BUS_STOPS_CSV = _m.BUS_CSV
BOUNDARY_GEOJSON = _m.BOUNDARY_GEOJSON


BUS_STOP_SOURCE_LABEL = _ingest.BUS_SOURCE_LABEL
BUS_STOP_REFERENCE_DATE = _ingest.BUS_REFERENCE_DATE

# 창원시 외 경남 17개 시·군: scripts/ingest_gyeongnam_bus_stops.py가 국토교통부 전국 버스정류장 위치정보에서
# 자기 시·군 등록분 중 SGIS 경계 안으로 판정해 둔 목록. 창원시 5개 구는 위의 창원시 정류소 원본(공식 2,926건)을
# 그대로 쓰므로 두 출처는 지역이 겹치지 않는다(경계 근처 이중 집계 없음).
GYEONGNAM_STOPS_CSV = os.path.join(_PROJECT_ROOT, "data", "gyeongnam", "gyeongnam_bus_stops.csv")
GYEONGNAM_STOP_SOURCE_LABEL = "국토교통부 전국 버스정류장 위치정보 (data.go.kr 15067528) 및 통계청 SGIS 시군구 경계"
GYEONGNAM_STOP_REFERENCE_DATE = "2025-10-31 (버스정류장), 2025-06-30 (SGIS 행정경계)"

_QUALITY_EXCEPTION_IDS = {ex["정류소아이디"] for ex in _ingest.KNOWN_DATA_QUALITY_EXCEPTIONS}
_BOUNDARY_CORRECTION_IDS = set(_ingest.SPATIAL_OVERRIDES)

QUALITY_FLAG_LABELS = {
    "boundary_correction": "단순화 경계 오차 보정 적용(원본 SHP·법정동명 기준 재확정)",
    "location_mismatch_possible": "법정동명과 공간판정이 달라 위치정보 불일치 가능성이 있음",
}

# ------------------------------------------------------------------ 주변 조회 설정
# services/convenience.py의 find_nearby_stores()와 동일한 값을 써서, 버스정류장과
# 편의점을 같은 반경 기준으로 비교할 수 있게 한다.
DISTANCE_TYPE = "직선거리"
DISTANCE_NOTE = (
    "두 좌표 사이의 직선거리(하버사인 공식)입니다. "
    "실제 도보거리나 대중교통 이동시간이 아닙니다."
)
PRESET_RADII_M = (300, 500, 1000)
MAX_RADIUS_M = 5000
MAX_RESULTS_LIMIT = 100


_valid_stops_cache: dict = {"key": None, "df": None}


def _classify_stop_region(row: dict, polygons: dict) -> Optional[str]:
    """
    정류장 한 행을 region_id로 분류하거나(제외 대상이면 None) 돌려준다.
    scripts/ingest_bus_stop_counts.py의 compute_counts() 안쪽 분기와 완전히
    동일한 순서(보정 3건 우선 -> 좌표 유효성 -> 공간판정)로 처리해, 구별 집계
    지표(bus_stop_count)와 이 모듈의 주변 조회가 서로 다른 정류장 집합을 쓰는
    일이 없게 한다.
    """
    stop_id = row["정류소아이디"]
    if stop_id in _ingest.SPATIAL_OVERRIDES:
        gu_name = _ingest.SPATIAL_OVERRIDES[stop_id]
        return _m.GU_NAME_TO_REGION_ID[gu_name]

    valid, lat, lon = _m.has_valid_coord(row)
    if not valid:
        return None
    region_id, _reason = _m.classify_by_spatial(lat, lon, polygons)
    return region_id


def _quality_flag(stop_id: str) -> Optional[str]:
    if stop_id in _BOUNDARY_CORRECTION_IDS:
        return "boundary_correction"
    if stop_id in _QUALITY_EXCEPTION_IDS:
        return "location_mismatch_possible"
    return None


def _build_valid_stops() -> pd.DataFrame:
    """원본 CSV 전체를 다시 읽어 '창원시 5개 구 경계 내부로 판정된' 정류장만
    DataFrame으로 만든다(2,926건이어야 함). 원본 파일은 읽기만 한다."""
    if not (os.path.exists(BUS_STOPS_CSV) and os.path.exists(BOUNDARY_GEOJSON)):
        return pd.DataFrame()

    polygons, _meta = _m.load_district_polygons()
    with open(BUS_STOPS_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    records = []
    for row in rows:
        region_id = _classify_stop_region(row, polygons)
        if region_id is None:
            continue
        stop_id = row["정류소아이디"]
        records.append(
            {
                "stop_id": stop_id,
                "stop_name": row["정류소명"],
                "region_id": region_id,
                "district": DISTRICTS.get(region_id, region_id),
                "lat": float(row["위도"]),
                "lon": float(row["경도"]),
                "quality_flag": _quality_flag(stop_id),
            }
        )

    df = pd.DataFrame.from_records(records)
    if not df.empty and df["stop_id"].duplicated().any():
        # 원본이 전부 고유 정류소아이디라는 전제가 깨졌다는 뜻 - 조용히 넘어가지 않는다.
        raise RuntimeError("정류소아이디가 중복된 레코드가 있습니다 - 원본 CSV를 확인하세요.")
    return df


_search_stops_cache: dict = {"key": None, "df": None}


def _load_search_stops() -> pd.DataFrame:
    """반경 검색 대상: 창원시 공식 정류장(_load_valid_stops) + 창원시 외 경남 17개 시·군 정류장.
    공식 집계 검증(verify_official_counts)은 창원시 원본만 쓰며 이 함수와 무관하다."""
    changwon = _load_valid_stops()
    if not os.path.exists(GYEONGNAM_STOPS_CSV):
        return changwon
    key = (os.path.getmtime(GYEONGNAM_STOPS_CSV), id(changwon))
    if _search_stops_cache["key"] == key:
        return _search_stops_cache["df"]
    gn = pd.read_csv(GYEONGNAM_STOPS_CSV, dtype=str, encoding="utf-8-sig").fillna("")
    gn = gn[~gn["region_id"].str.startswith("CW-")]
    others = pd.DataFrame({
        "stop_id": gn["정류장번호"], "stop_name": gn["정류장명"], "region_id": gn["region_id"],
        "district": gn["region_name"], "lat": gn["위도"].astype(float), "lon": gn["경도"].astype(float),
        "quality_flag": None,
    })
    df = pd.concat([changwon, others], ignore_index=True) if not changwon.empty else others
    _search_stops_cache.update(key=key, df=df)
    return df


def _load_valid_stops() -> pd.DataFrame:
    """두 원본 파일의 수정 시각을 캐시 키로 써서, 파일이 바뀌지 않는 한
    point-in-polygon 재계산을 생략한다."""
    if not (os.path.exists(BUS_STOPS_CSV) and os.path.exists(BOUNDARY_GEOJSON)):
        return pd.DataFrame()
    key = (os.path.getmtime(BUS_STOPS_CSV), os.path.getmtime(BOUNDARY_GEOJSON))
    if _valid_stops_cache["key"] == key:
        return _valid_stops_cache["df"]

    df = _build_valid_stops()
    _valid_stops_cache.update(key=key, df=df)
    return df


def verify_official_counts() -> dict:
    """
    원본 데이터로 지금 다시 집계한 구별 건수가 scripts/ingest_bus_stop_counts.py의
    공식 집계(의창구 831/성산구 452/마산합포구 760/마산회원구 365/진해구 518,
    합계 2,926)와 정확히 일치하는지 확인한다. 절대 기대값에 맞추기 위해 레코드를
    추가/삭제하지 않는다 - 불일치가 있으면 그 사실을 그대로 돌려줄 뿐이다
    (호출 측이 테스트 실패 등으로 처리).
    """
    df = _load_valid_stops()
    actual_counts = df["region_id"].value_counts().to_dict() if not df.empty else {}
    expected_counts = dict(_ingest.EXPECTED_COUNTS)
    mismatches = {
        region_id: {"expected": expected, "actual": actual_counts.get(region_id, 0)}
        for region_id, expected in expected_counts.items()
        if actual_counts.get(region_id, 0) != expected
    }
    actual_total = int(len(df))
    return {
        "ok": not mismatches and actual_total == _ingest.EXPECTED_TOTAL,
        "expected_counts": expected_counts,
        "actual_counts": {rid: actual_counts.get(rid, 0) for rid in expected_counts},
        "expected_total": _ingest.EXPECTED_TOTAL,
        "actual_total": actual_total,
        "mismatches": mismatches,
        "boundary_correction_count": len(_BOUNDARY_CORRECTION_IDS),
        "quality_exception_count": len(_QUALITY_EXCEPTION_IDS),
    }


def _nearby_result(status: str, message: str, query: dict, **extra) -> dict:
    result = {
        "status": status,  # "ok" | "invalid_input" | "no_data"
        "message": message,
        "query": query,
        "distance_type": DISTANCE_TYPE,
        "distance_note": DISTANCE_NOTE,
        "total_count": 0,  # 반경 안의 전체 버스정류장 수
        "displayed_count": 0,  # stops 목록에 담긴 수(최대 표시 개수 이하)
        "stops": [],  # 가까운 순 정렬된 표시 목록
        "source": None,
        "reference_date": None,
        "warnings": [],
    }
    result.update(extra)
    return result


def find_nearby_bus_stops(lat, lon, radius_m=500, max_results=10) -> dict:
    """
    입력 위치에서 반경 radius_m 이내 버스정류장을 직선거리 가까운 순으로 조회한다.
    대상은 "창원시 5개 구 행정경계 내부로 판정된" 2,926건뿐이다(경계 밖 600건은
    bus_stop_count 지표와 동일하게 제외).

    Args:
        lat, lon: 검색 위치 위도·경도 (WGS84, 예: 35.2280, 128.6811)
        radius_m: 검색 반경(m). 300 / 500 / 1000 권장, 1~5000 허용
        max_results: 화면에 표시할 최대 개수(1~100)

    Returns (예외를 던지지 않음):
        {
          "status": "ok" | "invalid_input" | "no_data",
          "message": 안내 문구,
          "query": {"lat", "lon", "radius_m", "max_results"},
          "distance_type": "직선거리",
          "distance_note": "...실제 도보거리나 대중교통 이동시간이 아닙니다.",
          "total_count": 반경 내 전체 버스정류장 수,
          "displayed_count": 표시 목록 개수,
          "stops": [
            {"rank", "stop_id", "stop_name", "district", "region_id", "lat", "lon",
             "straight_distance_m", "quality_flag"}, ...
          ],
          "source", "reference_date", "warnings": [...]
        }
    """
    query = {"lat": lat, "lon": lon, "radius_m": radius_m, "max_results": max_results}

    lat_f, lon_f = _to_float(lat), _to_float(lon)
    if lat_f is None or lon_f is None:
        return _nearby_result("invalid_input", "위도·경도는 숫자로 입력해야 합니다.", query)
    if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
        return _nearby_result(
            "invalid_input",
            "위도는 -90~90, 경도는 -180~180 범위여야 합니다. 위도와 경도가 뒤바뀌지 않았는지 확인하세요.",
            query,
        )

    radius = _to_float(radius_m)
    if radius is None or not (0 < radius <= MAX_RADIUS_M):
        return _nearby_result("invalid_input", f"검색 반경은 0 초과 {MAX_RADIUS_M}m 이하로 입력해야 합니다.", query)

    limit = _to_int(max_results)
    if limit is None or not (1 <= limit <= MAX_RESULTS_LIMIT):
        return _nearby_result("invalid_input", f"최대 표시 개수는 1~{MAX_RESULTS_LIMIT} 사이 정수여야 합니다.", query)

    query = {"lat": lat_f, "lon": lon_f, "radius_m": radius, "max_results": limit}
    warnings: list[str] = []
    if not (
        _GYEONGNAM_BBOX["lat"][0] <= lat_f <= _GYEONGNAM_BBOX["lat"][1]
        and _GYEONGNAM_BBOX["lon"][0] <= lon_f <= _GYEONGNAM_BBOX["lon"][1]
    ):
        warnings.append(
            "입력 위치가 경상남도 범위를 벗어난 것으로 보입니다. "
            "이 조회는 경남 22개 지역 경계 내부로 판정된 정류장만 포함합니다."
        )

    stops_df = _load_search_stops()
    if stops_df.empty:
        return _nearby_result(
            "no_data",
            "버스정류장 데이터가 없습니다. data/raw/changwon_bus_stops.csv 및 "
            "data/raw/changwon_district_boundaries.geojson 파일을 확인하세요.",
            query,
            warnings=warnings,
        )

    distances = _haversine_m(lat_f, lon_f, stops_df["lat"].to_numpy(), stops_df["lon"].to_numpy())
    inside = stops_df.assign(_dist=distances)[distances <= radius]
    inside = inside.sort_values(["_dist", "stop_id"], kind="mergesort")

    shown = []
    for rank, (_, row) in enumerate(inside.head(limit).iterrows(), start=1):
        shown.append(
            {
                "rank": rank,
                "stop_id": row["stop_id"],
                "stop_name": row["stop_name"],
                "district": row["district"],
                "region_id": row["region_id"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "straight_distance_m": int(round(row["_dist"])),
                # pandas가 object/str 컬럼의 None을 내부적으로 NaN류 결측치로 바꿔버릴 수
                # 있어(Arrow 백엔드 문자열 dtype 등) pd.notna()로 명시적으로 None 복원.
                "quality_flag": row["quality_flag"] if pd.notna(row["quality_flag"]) else None,
            }
        )

    total = len(inside)
    message = (
        f"반경 {radius:g}m(직선거리) 안에 버스정류장 {total}개가 있습니다."
        if total
        else f"반경 {radius:g}m(직선거리) 안에 버스정류장이 없습니다."
    )
    if total > len(shown):
        message += f" 가까운 {len(shown)}개만 표시합니다."

    outside_changwon = bool(len(inside)) and not inside["region_id"].str.startswith("CW-").all()
    within_changwon = bool(len(inside)) and inside["region_id"].str.startswith("CW-").any()
    if outside_changwon and within_changwon:
        source = f"창원시: {BUS_STOP_SOURCE_LABEL} / 그 외 경남: {GYEONGNAM_STOP_SOURCE_LABEL}"
        reference_date = f"창원시: {BUS_STOP_REFERENCE_DATE} / 그 외 경남: {GYEONGNAM_STOP_REFERENCE_DATE}"
    elif outside_changwon:
        source, reference_date = GYEONGNAM_STOP_SOURCE_LABEL, GYEONGNAM_STOP_REFERENCE_DATE
    else:
        source, reference_date = BUS_STOP_SOURCE_LABEL, BUS_STOP_REFERENCE_DATE
    return _nearby_result(
        "ok",
        message,
        query,
        total_count=total,
        displayed_count=len(shown),
        stops=shown,
        source=source,
        reference_date=reference_date,
        warnings=warnings,
    )


def count_nearby_by_radius(lat, lon, radii=PRESET_RADII_M) -> dict:
    """
    300m / 500m / 1km 반경별 버스정류장 수를 한 번에 반환한다(직선거리 기준).

    반환 예: {"status": "ok", "distance_type": "직선거리", "counts": {300: 2, 500: 5, 1000: 14},
             "message": ..., "warnings": [...]}
    입력이 잘못됐거나 데이터가 없으면 counts의 값이 None이다(실제 0건과 구분됨).
    """
    counts, status, message, warnings = {}, "ok", "", []
    for radius in radii:
        result = find_nearby_bus_stops(lat, lon, radius_m=radius, max_results=1)
        if result["status"] != "ok":
            status, message, warnings = result["status"], result["message"], result["warnings"]
            counts = {r: None for r in radii}
            break
        counts[radius] = result["total_count"]
        warnings = result["warnings"]
    return {
        "status": status,
        "message": message or "반경별 버스정류장 수(직선거리 기준)",
        "distance_type": DISTANCE_TYPE,
        "distance_note": DISTANCE_NOTE,
        "counts": counts,
        "warnings": warnings,
    }


if __name__ == "__main__":
    # 간단 확인용: python -m services.bus_stops
    check = verify_official_counts()
    print("=== 공식 집계와의 일치 여부 ===")
    print(f"  일치: {check['ok']}")
    for region_id, expected in check["expected_counts"].items():
        actual = check["actual_counts"][region_id]
        mark = "OK" if actual == expected else "MISMATCH"
        print(f"  {DISTRICTS[region_id]}: 기대 {expected} / 실제 {actual} [{mark}]")
    print(f"  합계: 기대 {check['expected_total']} / 실제 {check['actual_total']}")

    example = find_nearby_bus_stops(35.2280, 128.6811, radius_m=500, max_results=5)
    print("\n=== 창원시청 부근 예시 위치(근사 좌표) 500m 조회 ===")
    print(f"  {example['message']}")
    for s in example["stops"]:
        print(f"  {s['rank']}. {s['stop_name']} ({s['district']}) - {s['straight_distance_m']}m")
