"""
추천 결과 지도용 데이터 (표시 전용).

결과 화면에서 "추천 후보 지역이 대략 어디인지"를 보여주기 위해, 이미 계산된 점수 결과
(analysis.scoring)와 후보 역할(analysis.candidates)을 SGIS 시군구 경계
(data/raw/gyeongnam_boundaries.geojson)에 붙인다.

- 새 점수·순위·후보를 만들지 않는다. 넘겨받은 결과를 그대로 지도 모양으로 바꾸기만 한다.
- 후보 단위는 시·군·구 전체다. 지도에서 칠한 영역은 "이 행정구역 전체"라는 뜻이고,
  그 안의 특정 동네·주거 단지를 추천한 것이 아니다(행정동 단위 지표 미확보).
- 경계는 화면 표시용으로만 단순화한다(원본 GeoJSON은 수정하지 않는다).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOUNDARY_GEOJSON = PROJECT_ROOT / "data" / "raw" / "gyeongnam_boundaries.geojson"
BOUNDARY_SOURCE = "통계청 SGIS 시군구 경계"

# 화면 표시용 단순화 허용오차(도 단위, 약 200m). 지역 위치를 가늠하는 용도라 충분하고,
# 지도 데이터 크기를 줄여 화면이 빨리 뜨게 한다.
DISPLAY_SIMPLIFY_TOLERANCE = 0.002

ROLE_ORDER = ("best", "balanced", "alternative", "value")
ROLE_COLORS = {
    "best": "#2a78d6",         # 최적 - 파랑
    "balanced": "#2e9e5b",     # 균형 - 초록
    "alternative": "#e08a1e",  # 대안 - 주황
    "value": "#8e5bd0",        # 가성비 - 보라 (현재 주거비 미확보로 산출되지 않음)
}
OTHER_COLOR = "#9aa3ad"        # 비교했지만 후보 역할이 없는 지역


# 이보다 작은 섬 조각(도² 단위, 약 0.1km²)은 화면에서 뺀다 - 통영·거제 등 해안 지역의
# 수백 개 작은 섬이 지도 데이터 대부분을 차지하기 때문이다. 가장 큰 조각은 항상 남긴다.
DISPLAY_MIN_PART_AREA = 1e-5


def _drop_tiny_parts(geom):
    from shapely.geometry import MultiPolygon

    if geom.geom_type != "MultiPolygon":
        return geom
    parts = sorted(geom.geoms, key=lambda p: p.area, reverse=True)
    kept = [parts[0]] + [p for p in parts[1:] if p.area >= DISPLAY_MIN_PART_AREA]
    return kept[0] if len(kept) == 1 else MultiPolygon(kept)


@lru_cache(maxsize=1)
def _load_boundaries() -> dict[str, dict]:
    """region_id -> {"region_name", "region_type", "base_date", "geometry"(단순화), "label_point"(lat, lon)}"""
    if not BOUNDARY_GEOJSON.exists():
        return {}
    from shapely.geometry import mapping, shape

    data = json.loads(BOUNDARY_GEOJSON.read_text(encoding="utf-8"))
    boundaries: dict[str, dict] = {}
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        region_id = props.get("region_id")
        if not region_id or not feature.get("geometry"):
            continue
        geom = shape(feature["geometry"])
        simplified = _drop_tiny_parts(geom).simplify(DISPLAY_SIMPLIFY_TOLERANCE, preserve_topology=True)
        if simplified.is_empty:
            simplified = geom
        # 라벨은 다각형 "안쪽"에 찍는다(중심점은 해안·만 모양 지역에서 바다에 떨어질 수 있음).
        point = geom.representative_point()
        boundaries[region_id] = {
            "region_name": props.get("region_name") or region_id,
            "region_type": props.get("region_type"),
            "base_date": props.get("sgis_base_date"),
            "geometry": mapping(simplified),
            "label_point": (point.y, point.x),
        }
    return boundaries


def boundary_base_date() -> str | None:
    """경계 기준일(YYYYMMDD). 경계 파일이 없으면 None."""
    for item in _load_boundaries().values():
        if item.get("base_date"):
            return item["base_date"]
    return None


def _roles_by_region(candidate_set: dict | None) -> dict[str, list[dict]]:
    roles: dict[str, list[dict]] = {}
    for role in (candidate_set or {}).get("roles", []):
        if role.get("status") == "ok" and role.get("region_id"):
            roles.setdefault(role["region_id"], []).append(role)
    for items in roles.values():
        items.sort(key=lambda r: ROLE_ORDER.index(r["role"]) if r["role"] in ROLE_ORDER else 99)
    return roles


def build_candidate_map_regions(result: dict, candidate_set: dict | None) -> dict:
    """
    지도에 그릴 지역 목록을 만든다. 점수·순위·역할은 넘겨받은 값 그대로다.

    Returns:
        {
          "regions": [{
              "region_id", "region_name", "rank", "total_score",
              "roles": [role_label, ...],        # 맡은 후보 역할(없으면 [])
              "primary_role": "best"|...|None,   # 색을 정하는 대표 역할(최적 > 균형 > 대안)
              "color": "#rrggbb",
              "geometry": GeoJSON geometry dict,
              "label_point": (lat, lon),
          }, ...],                                # 후보 역할이 있는 지역이 뒤쪽(위에 그려짐)
          "missing": [region_name, ...],          # 경계가 없어 그리지 못한 지역
          "bounds": [[south, west], [north, east]] | None,
        }
    """
    boundaries = _load_boundaries()
    roles_by_region = _roles_by_region(candidate_set)
    regions, missing = [], []
    for row in result.get("region_scores", []):
        boundary = boundaries.get(row["region_id"])
        if boundary is None:
            missing.append(row["region_name"])
            continue
        roles = roles_by_region.get(row["region_id"], [])
        primary = roles[0]["role"] if roles else None
        regions.append({
            "region_id": row["region_id"],
            "region_name": row["region_name"],
            "rank": row["rank"],
            "total_score": row["total_score"],
            "roles": [r["role_label"] for r in roles],
            "primary_role": primary,
            "color": ROLE_COLORS.get(primary, OTHER_COLOR),
            "geometry": boundary["geometry"],
            "label_point": boundary["label_point"],
        })
    # 후보 지역을 나중에 그려 테두리가 다른 지역에 가려지지 않게 한다.
    regions.sort(key=lambda r: (r["primary_role"] is not None, -r["rank"]))
    return {"regions": regions, "missing": missing, "bounds": _bounds(regions)}


def map_component_key(map_data: dict, focus: str | None, scope_type: str) -> str:
    """화면 지도 컴포넌트 키. 비교 범위·관심 지역·후보 역할(지역별 대표 역할)이 바뀌면 키가 바뀌어
    지도를 새로 그린다 - 피드백으로 후보가 바뀐 뒤 이전 후보 색이 남지 않게 한다. 같은 상태면 같은 키."""
    signature = "|".join([scope_type or "-", focus or "-"] + sorted(
        f"{r['region_id']}:{r['primary_role']}" for r in map_data.get("regions", []) if r.get("primary_role")))
    return "candidate_map_" + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]


def _bounds(regions: list[dict]) -> list[list[float]] | None:
    if not regions:
        return None
    from shapely.geometry import shape

    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for region in regions:
        x0, y0, x1, y1 = shape(region["geometry"]).bounds
        minx, miny, maxx, maxy = min(minx, x0), min(miny, y0), max(maxx, x1), max(maxy, y1)
    return [[miny, minx], [maxy, maxx]]
