# SGIS 전국 시군구 경계(원본, 미커밋) -> 창원시 5개 구만 추출한 소형 GeoJSON
"""
data/raw/sgis_boundarie/2. 경계/2. 2025년 2분기 기준 시군구 경계/bnd_sigungu_00_2025_2Q.shp
(통계청 SGIS 전국 시군구 경계, 252개 레코드, 97MB, EPSG:5179)에서 창원시 5개 구만 골라
EPSG:4326(위경도)으로 변환해 data/raw/changwon_district_boundaries.geojson에 저장한다.

SGIS 원본은 전국 단위라 .gitignore로 커밋 대상에서 제외했고, 이 스크립트가 만드는
결과 GeoJSON(5개 피처, 수백 KB)만 커밋한다.

[좌표계]
    원본: EPSG:5179 (Korea 2000 / Unified CS, 미터 단위, TM도법)
    출력: EPSG:4326 (WGS84 위경도) - 버스정류장 CSV의 위도/경도와 맞추기 위함

[SGIS 코드 주의사항]
    SIGUNGU_CD(38111~38115)는 "SGIS 시군구코드" 체계다. HIRA Open API의 sgguCd나
    버스정류장 CSV의 "관할관청" 컬럼은 전혀 다른 코드 체계이므로 절대 섞어 쓰지 않는다.
    이 매핑은 SGIS 원본 파일을 직접 열어 SIGUNGU_NM(구 이름) 속성으로 확인한 값이며,
    검색으로 추정한 값이 아니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import shapefile
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SGIS_SIGUNGU_SHP = (
    PROJECT_ROOT
    / "data" / "raw" / "sgis_boundarie" / "2. 경계"
    / "2. 2025년 2분기 기준 시군구 경계" / "bnd_sigungu_00_2025_2Q.shp"
)
OUTPUT_GEOJSON = PROJECT_ROOT / "data" / "raw" / "changwon_district_boundaries.geojson"

# SGIS 시군구코드(SIGUNGU_CD) -> 기존 services/region_data.py의 region_id.
# bnd_sigungu_00_2025_2Q.shp를 직접 읽어 SIGUNGU_NM="창원시 OO구"로 확인한 값.
SGIS_SIGUNGU_CD_TO_REGION_ID = {
    "38111": "CW-UICHANG",
    "38112": "CW-SEONGSAN",
    "38113": "CW-MASANHAPPO",
    "38114": "CW-MASANHOEWON",
    "38115": "CW-JINHAE",
}


def main() -> None:
    if not SGIS_SIGUNGU_SHP.exists():
        raise SystemExit(f"SGIS 원본을 찾을 수 없습니다: {SGIS_SIGUNGU_SHP}")

    sf = shapefile.Reader(str(SGIS_SIGUNGU_SHP), encoding="utf-8")
    transformer = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)

    features = []
    found_codes = set()
    for sr in sf.iterShapeRecords():
        code = sr.record["SIGUNGU_CD"]
        if code not in SGIS_SIGUNGU_CD_TO_REGION_ID:
            continue
        found_codes.add(code)

        geom_5179 = shape(sr.shape.__geo_interface__)
        # 해안선 디테일로 원본 정밀도가 과도하게 커서(피처당 수백KB~MB) 10m 허용오차로
        # 단순화한다. 버스정류장 점-다각형 판정 목적에는 충분한 정밀도이며, 위상
        # (구멍/자기교차 없음)은 preserve_topology=True로 보존한다.
        geom_5179 = geom_5179.simplify(10, preserve_topology=True)
        geom_4326 = transform(transformer.transform, geom_5179)

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "region_id": SGIS_SIGUNGU_CD_TO_REGION_ID[code],
                    "region_name": sr.record["SIGUNGU_NM"],
                    "sgis_sigungu_cd": code,
                    "sgis_base_date": sr.record["BASE_DATE"],
                },
                "geometry": mapping(geom_4326),
            }
        )

    missing = set(SGIS_SIGUNGU_CD_TO_REGION_ID) - found_codes
    if missing:
        raise SystemExit(f"SGIS 원본에서 다음 SIGUNGU_CD를 찾지 못했습니다: {sorted(missing)}")

    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    OUTPUT_GEOJSON.write_text(
        json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"저장 완료: {OUTPUT_GEOJSON}")
    print(f"피처 수: {len(features)}개 (기대: 5개)")
    for f in features:
        p = f["properties"]
        print(f"  {p['region_id']}: {p['region_name']} (SGIS {p['sgis_sigungu_cd']}, 기준일 {p['sgis_base_date']})")


if __name__ == "__main__":
    main()
