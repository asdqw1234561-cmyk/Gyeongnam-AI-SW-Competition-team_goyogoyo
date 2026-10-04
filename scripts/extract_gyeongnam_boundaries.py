# SGIS 전국 시군구 경계(원본, 미커밋) -> 경남 22개 비교 단위 경계 GeoJSON
"""
data/raw/sgis_boundarie/.../bnd_sigungu_00_2025_2Q.shp(통계청 SGIS, EPSG:5179)에서
scripts/gyeongnam_regions.py의 22개 지역(창원시 5개 구 + 17개 시·군)을 골라 EPSG:4326으로 바꿔
data/raw/gyeongnam_boundaries.geojson에 저장한다. 단순화 허용오차·좌표 변환은
scripts/extract_changwon_district_boundaries.py와 같다(2m, 위상 보존).

    python scripts/extract_gyeongnam_boundaries.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import shapefile
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_changwon_district_boundaries as changwon  # noqa: E402  (원본 경로 재사용)
import gyeongnam_regions as gn  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_GEOJSON = PROJECT_ROOT / "data" / "raw" / "gyeongnam_boundaries.geojson"


def main() -> None:
    if not changwon.SGIS_SIGUNGU_SHP.exists():
        raise SystemExit(f"SGIS 원본을 찾을 수 없습니다: {changwon.SGIS_SIGUNGU_SHP}")
    sf = shapefile.Reader(str(changwon.SGIS_SIGUNGU_SHP), encoding="utf-8")
    transformer = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)

    features = []
    for sr in sf.iterShapeRecords():
        code = sr.record["SIGUNGU_CD"]
        region_id = gn.ID_BY_SGIS.get(code)
        if region_id is None:
            continue
        if sr.record["SIGUNGU_NM"] != gn.NAME_BY_ID[region_id]:
            raise SystemExit(f"SGIS 이름 불일치: {code} {sr.record['SIGUNGU_NM']} != {gn.NAME_BY_ID[region_id]}")
        geom = shape(sr.shape.__geo_interface__).simplify(2, preserve_topology=True)
        features.append({
            "type": "Feature",
            "properties": {
                "region_id": region_id,
                "region_name": sr.record["SIGUNGU_NM"],
                "region_type": gn.TYPE_BY_ID[region_id],
                "sgis_sigungu_cd": code,
                "sgis_base_date": sr.record["BASE_DATE"],
            },
            "geometry": mapping(transform(transformer.transform, geom)),
        })

    missing = set(gn.REGION_IDS) - {f["properties"]["region_id"] for f in features}
    if missing:
        raise SystemExit(f"SGIS 원본에서 찾지 못한 지역: {sorted(missing)}")
    features.sort(key=lambda f: gn.REGION_IDS.index(f["properties"]["region_id"]))
    OUTPUT_GEOJSON.write_text(json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"저장 완료: {OUTPUT_GEOJSON} ({len(features)}개 지역, {OUTPUT_GEOJSON.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
