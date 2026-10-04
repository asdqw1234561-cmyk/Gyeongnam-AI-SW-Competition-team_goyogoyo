# data/gyeongnam/*.csv(경남 22개 지역 집계) -> 운영 지표 data/regions.csv, data/region_indicators.csv
"""
앱이 읽는 두 운영 CSV를 경남 22개 비교 단위로 다시 만든다. 값은 전부 data/gyeongnam/의 검증된 집계에서 그대로
옮기며 새로 계산하지 않는다. 확보하지 못한 지표(대중교통 소요시간·응급실·대형마트·월세·전세)는 22개 지역 모두
value를 비우고 data_status='미확보'로 둔다(0으로 채우지 않는다).

- 버스정류장 수: 22개 지역 모두 국토교통부 전국 버스정류장 위치정보 한 출처로 통일(창원도 같은 출처).
  창원시 위치 기반 탐색(pages/user.py)은 기존 창원시 정류소 원본(data/raw/changwon_bus_stops.csv)을 계속 쓴다.
- 인구(population): '인구 1만 명당' 비교의 분모. 점수 축이 아니다.
- 창원시 5개 구 region_name은 기존 짧은 이름(의창구 등)을 유지한다.

    python scripts/build_gyeongnam_indicators.py [--write]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gyeongnam_regions as gn  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GN_DIR = PROJECT_ROOT / "data" / "gyeongnam"
REGIONS_CSV = PROJECT_ROOT / "data" / "regions.csv"
INDICATORS_CSV = PROJECT_ROOT / "data" / "region_indicators.csv"

INDICATOR_COLUMNS = ["region_id", "region_name", "category", "indicator_code", "indicator_name", "value", "unit",
                     "source", "reference_date", "data_status", "note"]
NOT_SECURED_NOTE = "실제 공공데이터 확보 전"

# (category, indicator_code, indicator_name, unit, 집계 파일, 값 컬럼, note)
CONFIRMED = (
    ("교통", "bus_stop_count", "버스정류장 수", "개", "bus_stop_counts.csv", "bus_stop_count",
     "국토교통부 전국 버스정류장 위치정보에서 그 시·군이 직접 등록한 정류장 중 SGIS 시군구 경계 안에 있는 것을 "
     "정류장번호 기준으로 집계(다른 시·도/시·군 BIS가 등록한 광역노선 정류장은 중복 가능성이 있어 제외). "
     "버스정류장 개수는 대중교통 접근성의 참고 지표일 뿐 실제 이동·통근시간을 뜻하지 않음."),
    ("의료", "hospital_count", "의료기관 수", "개", "hospital_counts.csv", "hospital_count",
     "HIRA 병원정보서비스 Open API(sidoCd=경남)로 조회해 sgguCdNm으로 지역 배정, ykiho 기준 중복 제거. "
     "의원·치과의원·한의원·보건소 등 전 종별 합산이며 종합병원 수가 아님."),
    ("생활편의", "convenience_store_count", "편의점 수", "개", "convenience_counts.csv", "convenience_store_count",
     "상가(상권)정보에 등록된 업소 기준 집계(상권업종 소분류 '편의점', 코드 G20405). 등록 업소 수이므로 폐업 반영 지연이나 "
     "동일 주소 중복 등록 등으로 실제 영업 매장 수와 다를 수 있음."),
    ("인구", "population", "주민등록 인구", "명", "population.csv", "population",
     "행정안전부 주민등록 인구(행정동 합산). '인구 1만 명당 시설 수' 비교의 분모로만 쓰며 점수 축이 아님."),
)
NOT_SECURED = (
    ("교통", "transit_avg_time_to_citycenter_min", "대중교통 평균 소요시간(시청 기준)", "분"),
    ("의료", "emergency_hospital_count", "응급실 운영 병원 수", "개"),
    ("생활편의", "mart_count", "대형마트 수", "개"),
    ("주거비", "monthly_rent_avg", "월세 평균", "만원"),
    ("주거비", "jeonse_avg", "전세 평균", "만원"),
)
CHANGWON_SHORT_NAMES = {"CW-UICHANG": "의창구", "CW-SEONGSAN": "성산구", "CW-MASANHAPPO": "마산합포구",
                        "CW-MASANHOEWON": "마산회원구", "CW-JINHAE": "진해구"}


def display_name(region_id: str) -> str:
    return CHANGWON_SHORT_NAMES.get(region_id, gn.NAME_BY_ID[region_id])


def _read(name: str) -> dict[str, dict]:
    with open(GN_DIR / name, encoding="utf-8-sig", newline="") as f:
        rows = {r["region_id"]: r for r in csv.DictReader(f)}
    if list(rows) != list(gn.REGION_IDS):
        raise SystemExit(f"{name}: 22개 지역 순서·구성이 마스터와 다릅니다.")
    return rows


def build() -> tuple[list[dict], list[dict]]:
    regions = [{"region_id": rid, "region_name": display_name(rid),
                "city": "창원시" if rid.startswith("CW-") else gn.NAME_BY_ID[rid],
                "province": gn.PROVINCE, "region_type": gn.TYPE_BY_ID[rid],
                "description": "창원시 산하 일반구" if rid.startswith("CW-") else f"경상남도 {gn.TYPE_BY_ID[rid]}"}
               for rid in gn.REGION_IDS]
    sources = {spec[4]: _read(spec[4]) for spec in CONFIRMED}
    indicators = []
    for rid in gn.REGION_IDS:
        for category, code, name, unit, file_name, column, note in CONFIRMED:
            row = sources[file_name][rid]
            reference = row.get("reference_date") or (row.get("reference_ym", "") + " (기준년월)")
            indicators.append({"region_id": rid, "region_name": display_name(rid), "category": category,
                               "indicator_code": code, "indicator_name": name, "value": row[column], "unit": unit,
                               "source": row["source"], "reference_date": reference, "data_status": "확보",
                               "note": note})
        for category, code, name, unit in NOT_SECURED:
            indicators.append({"region_id": rid, "region_name": display_name(rid), "category": category,
                               "indicator_code": code, "indicator_name": name, "value": "", "unit": unit,
                               "source": "", "reference_date": "", "data_status": "미확보", "note": NOT_SECURED_NOTE})
    return regions, indicators


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    regions, indicators = build()
    print(f"지역 {len(regions)}개, 지표 {len(indicators)}행 "
          f"(확보 {sum(1 for i in indicators if i['data_status'] == '확보')}행)")
    if args.write:
        with open(REGIONS_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(regions[0]))
            writer.writeheader()
            writer.writerows(regions)
        with open(INDICATORS_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=INDICATOR_COLUMNS)
            writer.writeheader()
            writer.writerows(indicators)
        print(f"저장: {REGIONS_CSV.relative_to(PROJECT_ROOT)}, {INDICATORS_CSV.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
