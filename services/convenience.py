# 생활편의 데이터
"""
창원시 생활편의시설(현재: 편의점) 조회 서비스.

데이터는 scripts/collect_convenience_stores.py 로 수집한 CSV를 읽는다.
    - data/convenience/changwon_convenience_stores.csv : 업소 단위 목록
    - data/convenience/changwon_convenience_counts.csv : 구별 집계(대형마트는 "미확보")

CSV가 아직 없으면(수집 전) 예외를 내지 않고 모든 구를 data_status="미확보"로 돌려준다.
수치를 임의로 만들지 않는다.

반환 형식은 services/region_data.py 의 지표 형식(value / source / reference_date /
data_status / note)과 맞췄으므로 추천·점수 계산 쪽에서 그대로 섞어 쓸 수 있다.

주변 편의점 조회(find_nearby_stores / count_nearby_by_radius)의 거리는 두 좌표 사이의
**직선거리**(하버사인 공식)다. 실제 도보거리나 이동시간이 아니므로 화면에도 반드시
"직선거리"로 표기한다.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from services.geo import GYEONGNAM_BBOX as _GYEONGNAM_BBOX
from services.geo import DISTRICTS, EARTH_RADIUS_M  # noqa: F401  (기존 공개 이름 유지)
from services.geo import haversine_m as _haversine_m
from services.geo import to_float as _to_float
from services.geo import to_int as _to_int

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(os.path.dirname(_THIS_DIR), "data", "convenience")

STORES_CSV = os.path.join(_DATA_DIR, "changwon_convenience_stores.csv")
COUNTS_CSV = os.path.join(_DATA_DIR, "changwon_convenience_counts.csv")
# 창원시 외 경남 17개 시·군 편의점(scripts/ingest_gyeongnam_convenience.py, 같은 상가정보 출처·기준년월).
# 반경 검색에서만 창원시 목록에 더한다(창원시 행은 위 원본을 그대로 쓰고 여기서는 제외 - 중복 없음).
GYEONGNAM_STORES_CSV = os.path.join(os.path.dirname(_DATA_DIR), "gyeongnam", "convenience_stores.csv")

NOT_SECURED = "미확보"


# facility_type -> region_indicators.csv 의 indicator_code
INDICATOR_CODES = {"편의점": "convenience_store_count", "대형마트": "mart_count"}


def _read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")


def _resolve_region_id(district: str) -> Optional[str]:
    """'CW-JINHAE', '진해구', '창원시 진해구', '진해' 모두 허용."""
    if not district:
        return None
    text = district.strip()
    if text in DISTRICTS:
        return text
    for region_id, name in DISTRICTS.items():
        if name in text or name.rstrip("구") == text:
            return region_id
    return None


def load_stores() -> pd.DataFrame:
    """수집된 편의점 업소 목록 전체(DataFrame). 수집 전이면 빈 DataFrame."""
    return _read_csv(STORES_CSV)


def is_data_available() -> bool:
    return os.path.exists(STORES_CSV) and os.path.exists(COUNTS_CSV)


def get_stores_by_district(district: str, keyword: str = "", limit: Optional[int] = None) -> list[dict]:
    """
    구 하나의 편의점 목록을 반환한다.

    Args:
        district: "의창구", "창원시 성산구", "CW-JINHAE" 등
        keyword: 상호명/주소에 포함될 문자열(선택). 예: "GS25", "상남동"
        limit: 최대 반환 개수(선택)
    """
    region_id = _resolve_region_id(district)
    df = load_stores()
    if region_id is None or df.empty:
        return []
    df = df[df["region_id"] == region_id]
    if keyword:
        mask = (
            df["facility_name"].str.contains(keyword, case=False, regex=False)
            | df["road_address"].str.contains(keyword, regex=False)
            | df["jibun_address"].str.contains(keyword, regex=False)
            | df["adong_name"].str.contains(keyword, regex=False)
        )
        df = df[mask]
    if limit:
        df = df.head(limit)
    return df.to_dict(orient="records")


def get_district_counts(facility_type: str = "편의점") -> list[dict]:
    """
    창원시 5개 구의 시설 수를 항상 5개 행으로 반환한다.

    반환 예:
        [{"region_id": "CW-UICHANG", "district": "의창구", "facility_type": "편의점",
          "indicator_code": "convenience_store_count", "value": 123 또는 None,
          "unit": "개", "source": ..., "reference_date": "202506",
          "collected_date": "2026-10-01", "data_status": "확보" | "미확보", "note": ...}, ...]
    """
    counts = _read_csv(COUNTS_CSV)
    results = []
    for region_id, district in DISTRICTS.items():
        row = None
        if not counts.empty:
            matched = counts[(counts["region_id"] == region_id)
                             & (counts["facility_type"] == facility_type)]
            if not matched.empty:
                row = matched.iloc[0]
        status = row["data_status"] if row is not None else NOT_SECURED
        value = int(row["count"]) if row is not None and status == "확보" and row["count"] else None
        results.append({
            "region_id": region_id,
            "district": district,
            "facility_type": facility_type,
            "indicator_code": INDICATOR_CODES.get(facility_type),
            "value": value,
            "unit": "개",
            "source": (row["source"] or None) if row is not None else None,
            "reference_date": (row["reference_ym"] or None) if row is not None else None,
            "collected_date": (row["collected_date"] or None) if row is not None else None,
            "data_status": status,
            "note": (row["note"] if row is not None else "수집 전 (scripts/collect_convenience_stores.py 실행 필요)"),
        })
    return results


def get_district_count(district: str, facility_type: str = "편의점") -> Optional[dict]:
    """구 하나의 시설 수 지표. 알 수 없는 구면 None."""
    region_id = _resolve_region_id(district)
    for row in get_district_counts(facility_type):
        if row["region_id"] == region_id:
            return row
    return None


# ------------------------------------------------------------------ 주변 편의점 조회
DISTANCE_TYPE = "직선거리"
DISTANCE_NOTE = (
    "두 좌표 사이의 직선거리(하버사인 공식)입니다. "
    "실제 도보거리나 대중교통 이동시간이 아닙니다."
)
PRESET_RADII_M = (300, 500, 1000)
MAX_RADIUS_M = 5000
MAX_RESULTS_LIMIT = 100


_coords_cache: dict = {"key": None, "df": None, "skipped": 0}


def _load_store_coords() -> tuple[pd.DataFrame, int]:
    """좌표가 유효한 업소만 (lat/lon float) 반환. 파일이 바뀌면 다시 읽는다."""
    if not os.path.exists(STORES_CSV):
        return pd.DataFrame(), 0
    has_gn = os.path.exists(GYEONGNAM_STORES_CSV)
    key = (STORES_CSV, os.path.getmtime(STORES_CSV), has_gn and os.path.getmtime(GYEONGNAM_STORES_CSV))
    if _coords_cache["key"] == key:
        return _coords_cache["df"], _coords_cache["skipped"]

    df = _read_csv(STORES_CSV)
    if has_gn:
        gn = _read_csv(GYEONGNAM_STORES_CSV)
        df = pd.concat([df, gn[~gn["region_id"].str.startswith("CW-")]], ignore_index=True)
    if df.empty or "lat" not in df.columns or "lon" not in df.columns:
        return pd.DataFrame(), len(df)
    lat = pd.to_numeric(df["lat"], errors="coerce")
    lon = pd.to_numeric(df["lon"], errors="coerce")
    valid = lat.between(-90, 90) & lon.between(-180, 180)
    clean = df[valid].copy()
    clean["lat_f"] = lat[valid].astype(float)
    clean["lon_f"] = lon[valid].astype(float)
    skipped = int((~valid).sum())

    _coords_cache.update(key=key, df=clean, skipped=skipped)
    return clean, skipped


def _nearby_result(status: str, message: str, query: dict, **extra) -> dict:
    result = {
        "status": status,              # "ok" | "invalid_input" | "no_data"
        "message": message,
        "query": query,
        "distance_type": DISTANCE_TYPE,
        "distance_note": DISTANCE_NOTE,
        "total_count": 0,              # 반경 안의 전체 편의점 수
        "displayed_count": 0,          # stores 목록에 담긴 수(최대 표시 개수 이하)
        "stores": [],                  # 가까운 순 정렬된 표시 목록
        "source": None,
        "reference_date": None,
        "warnings": [],
    }
    result.update(extra)
    return result


def find_nearby_stores(lat, lon, radius_m=500, max_results=10) -> dict:
    """
    입력 위치에서 반경 radius_m 이내 편의점을 직선거리 가까운 순으로 조회한다.

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
          "total_count": 반경 내 전체 편의점 수,
          "displayed_count": 표시 목록 개수,
          "stores": [
            {"rank", "facility_name", "branch_name", "district", "road_address",
             "jibun_address", "lat", "lon", "straight_distance_m"}, ...
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
            "invalid_input", "위도는 -90~90, 경도는 -180~180 범위여야 합니다. "
            "위도와 경도가 뒤바뀌지 않았는지 확인하세요.", query)

    radius = _to_float(radius_m)
    if radius is None or not (0 < radius <= MAX_RADIUS_M):
        return _nearby_result(
            "invalid_input", f"검색 반경은 0 초과 {MAX_RADIUS_M}m 이하로 입력해야 합니다.", query)

    limit = _to_int(max_results)
    if limit is None or not (1 <= limit <= MAX_RESULTS_LIMIT):
        return _nearby_result(
            "invalid_input", f"최대 표시 개수는 1~{MAX_RESULTS_LIMIT} 사이 정수여야 합니다.", query)

    query = {"lat": lat_f, "lon": lon_f, "radius_m": radius, "max_results": limit}
    warnings = []
    if not (_GYEONGNAM_BBOX["lat"][0] <= lat_f <= _GYEONGNAM_BBOX["lat"][1]
            and _GYEONGNAM_BBOX["lon"][0] <= lon_f <= _GYEONGNAM_BBOX["lon"][1]):
        warnings.append("입력 위치가 경상남도 범위를 벗어난 것으로 보입니다. "
                        "수집 데이터는 경남 22개 지역만 포함합니다.")

    stores_df, skipped = _load_store_coords()
    if skipped:
        warnings.append(f"좌표가 없거나 잘못된 업소 {skipped}건은 계산에서 제외했습니다.")
    if stores_df.empty:
        return _nearby_result(
            "no_data", "편의점 데이터가 없습니다. scripts/collect_convenience_stores.py 로 "
            "먼저 수집하세요.", query, warnings=warnings)

    distances = _haversine_m(lat_f, lon_f, stores_df["lat_f"].to_numpy(),
                             stores_df["lon_f"].to_numpy())
    inside = stores_df.assign(_dist=distances)[distances <= radius]
    inside = inside.sort_values(["_dist", "facility_name"], kind="mergesort")

    shown = []
    for rank, (_, row) in enumerate(inside.head(limit).iterrows(), start=1):
        shown.append({
            "rank": rank,
            "facility_name": row["facility_name"],
            "branch_name": row.get("branch_name", ""),
            "district": row.get("district", ""),
            "road_address": row.get("road_address", ""),
            "jibun_address": row.get("jibun_address", ""),
            "lat": float(row["lat_f"]),
            "lon": float(row["lon_f"]),
            "straight_distance_m": int(round(row["_dist"])),
        })

    total = len(inside)
    message = (f"반경 {radius:g}m(직선거리) 안에 편의점 {total}개가 있습니다."
               if total else f"반경 {radius:g}m(직선거리) 안에 편의점이 없습니다.")
    if total > len(shown):
        message += f" 가까운 {len(shown)}개만 표시합니다."

    first = stores_df.iloc[0]
    return _nearby_result(
        "ok", message, query,
        total_count=total,
        displayed_count=len(shown),
        stores=shown,
        source=first.get("source") or None,
        reference_date=first.get("reference_ym") or None,
        warnings=warnings,
    )


def count_nearby_by_radius(lat, lon, radii=PRESET_RADII_M) -> dict:
    """
    300m / 500m / 1km 반경별 편의점 수를 한 번에 반환한다(직선거리 기준).

    반환 예: {"status": "ok", "distance_type": "직선거리", "counts": {300: 2, 500: 5, 1000: 14},
             "message": ..., "warnings": [...]}
    입력이 잘못됐거나 데이터가 없으면 counts의 값이 None이다.
    """
    counts, status, message, warnings = {}, "ok", "", []
    for radius in radii:
        result = find_nearby_stores(lat, lon, radius_m=radius, max_results=1)
        if result["status"] != "ok":
            status, message, warnings = result["status"], result["message"], result["warnings"]
            counts = {r: None for r in radii}
            break
        counts[radius] = result["total_count"]
        warnings = result["warnings"]
    return {
        "status": status,
        "message": message or "반경별 편의점 수(직선거리 기준)",
        "distance_type": DISTANCE_TYPE,
        "distance_note": DISTANCE_NOTE,
        "counts": counts,
        "warnings": warnings,
    }


if __name__ == "__main__":
    # 간단 확인용: python -m services.convenience
    for r in get_district_counts("편의점") + get_district_counts("대형마트"):
        shown = r["value"] if r["data_status"] == "확보" else NOT_SECURED
        print(f"{r['district']:<6} {r['facility_type']:<4} {shown}  (기준 {r['reference_date']})")
