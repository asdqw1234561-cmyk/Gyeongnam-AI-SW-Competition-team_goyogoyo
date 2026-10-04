# 소상공인시장진흥공단 상가(상권)정보 -> 경남 22개 비교 단위별 편의점 등록 업소 수
"""
scripts/collect_convenience_stores.py의 페이지 수집·완전성 판정(fetch_district)을 그대로 쓰고, 대상만 22개 지역
(signguCd = scripts/gyeongnam_regions.py의 lawd_cd)으로 넓힌다.

- 업종 소분류코드 G20405(편의점)로 서버 필터. 코드는 2026-10-04 실제 응답(의창구, indsSclsNm='편의점' 행의
  indsSclsCd)에서 확인했고, 받은 행도 indsSclsNm == '편의점'인지 다시 확인한다.
- 한 지역이라도 페이지가 빠지면(totalCount 불일치 등) 그 지역은 '미확보'로 두고 저장하지 않는다(추정 없음).
- '등록 업소 수'이며 실제 영업 매장 수와 다를 수 있다(기존 편의점 지표와 같은 의미).
키는 SBIZ_SERVICE_KEY, 없으면 HIRA_SERVICE_KEY(같은 data.go.kr 계정 키)를 환경변수에서만 읽는다.

    python scripts/ingest_gyeongnam_convenience.py [--write]
출력: data/gyeongnam/convenience_counts.csv, data/gyeongnam/convenience_stores.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_convenience_stores as conv  # noqa: E402  (fetch_district·키 마스킹·출처 재사용)
import gyeongnam_regions as gn  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "gyeongnam"
COUNTS_CSV = OUT_DIR / "convenience_counts.csv"
STORES_CSV = OUT_DIR / "convenience_stores.csv"
CONVENIENCE_SCLS_CD = "G20405"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    key = os.environ.get(conv.SERVICE_KEY_ENV) or os.environ.get("HIRA_SERVICE_KEY")
    if not key:
        raise SystemExit("SBIZ_SERVICE_KEY 또는 HIRA_SERVICE_KEY 환경변수가 없습니다.")

    collected = date.today().isoformat()
    counts, stores, incomplete = {}, [], []
    seen: set[str] = set()
    reference_yms: set[str] = set()
    for region_id, name, _type, _sgis, lawd in gn.REGIONS:
        fetched = conv.fetch_district(key, lawd, CONVENIENCE_SCLS_CD, num_of_rows=1000, max_pages=50)
        if not fetched.complete:
            incomplete.append(f"{name}: {fetched.reason}")
            print(f"  [미확보] {name}: {fetched.reason}")
            continue
        district_token = name.split()[-1]
        rows = [i for i in fetched.items if i.get("indsSclsNm") == conv.FACILITY_TYPE
                and district_token in str(i.get("signguNm", ""))]
        rows = [i for i in rows if not (str(i.get("bizesId", "")) in seen or seen.add(str(i.get("bizesId", ""))))]
        counts[region_id] = len(rows)
        reference_yms |= fetched.reference_yms
        for item in rows:
            stores.append(conv._to_store_row(item, region_id, name, ",".join(sorted(fetched.reference_yms)), collected))
        print(f"  {name:<10} {len(rows):>4} (받은 {len(fetched.items)}건, {fetched.pages}페이지)")

    if incomplete:
        raise SystemExit(f"수집이 불완전한 지역이 있어 저장하지 않습니다: {incomplete}")
    if counts.get("CW-UICHANG") != 199:
        print(f"[참고] 의창구 {counts.get('CW-UICHANG')} - 2026-10-01 이름 기준 집계 199와 다름(기준년월 차이 가능)")
    print(f"합계 {sum(counts.values())} · 기준년월 {sorted(reference_yms)}")
    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(COUNTS_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region_id", "region_name", "convenience_store_count", "source", "reference_ym", "collected_date"])
            for rid in gn.REGION_IDS:
                writer.writerow([rid, gn.NAME_BY_ID[rid], counts[rid], conv.SOURCE_LABEL,
                                 ",".join(sorted(reference_yms)), collected])
        with open(STORES_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=conv.STORE_COLUMNS)
            writer.writeheader()
            writer.writerows(stores)
        print(f"저장: {COUNTS_CSV.relative_to(PROJECT_ROOT)}, {STORES_CSV.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
