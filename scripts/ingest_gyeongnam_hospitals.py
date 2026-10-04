# HIRA 병원정보서비스 -> 경남 22개 비교 단위별 의료기관 수
"""
건강보험심사평가원 병원정보서비스 Open API(getHospBasisList)를 sidoCd=380000(경남)으로 조회해
sgguCdNm으로 22개 지역에 나누고 ykiho(요양기관기호)로 중복을 제거해 센다.
집계 대상·의미는 scripts/ingest_hira_hospital_data.py와 같다: 의원·치과의원·한의원·보건소 등 전 종별 합산
"의료기관 수"(종합병원 수가 아님).

sgguCdNm 표기(2026-10-04 실제 응답으로 확인): 창원시 구는 "창원의창구"처럼 붙여 쓰고, 나머지는 "진주시"·"고성군"처럼
scripts/gyeongnam_regions.py 이름과 같다. 대응하지 않는 표기는 추정하지 않고 제외해 건수를 남긴다.
키는 HIRA_SERVICE_KEY 환경변수(.env)에서만 읽고, 오류 출력에는 키·요청 URL을 남기지 않는다.

    python scripts/ingest_gyeongnam_hospitals.py [--write]
출력: data/gyeongnam/hospital_counts.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gyeongnam_regions as gn  # noqa: E402
import ingest_hira_hospital_data as hira  # noqa: E402  (엔드포인트·응답 파서 재사용)
from secret_mask import request_error_kind  # noqa: E402

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = PROJECT_ROOT / "data" / "gyeongnam" / "hospital_counts.csv"
SIDO_CD_GYEONGNAM = "380000"
PAGE_SIZE = 1000
SOURCE_LABEL = "건강보험심사평가원 병원정보서비스 Open API (data.go.kr 15001698)"


def region_id_for(sggu_name: str) -> str | None:
    if sggu_name.startswith("창원") and not sggu_name.startswith("창원시"):
        return gn.ID_BY_NAME.get(f"창원시 {sggu_name[2:]}")
    return gn.ID_BY_NAME.get(sggu_name)


def fetch_all(key: str, max_pages: int = 20) -> tuple[list[dict], int]:
    items: list[dict] = []
    total = None
    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(hira.CANDIDATE_ENDPOINTS[0], timeout=30, params={
                "serviceKey": key, "pageNo": page, "numOfRows": PAGE_SIZE, "sidoCd": SIDO_CD_GYEONGNAM})
        except requests.RequestException as exc:
            raise SystemExit(f"페이지 {page} 요청 실패: {request_error_kind(exc)}") from None
        envelope = hira._parse_envelope(resp.text)
        if envelope["kind"] != "success":
            raise SystemExit(f"페이지 {page} 정상 응답 아님: {envelope.get('kind')} "
                             f"{envelope.get('resultCode') or envelope.get('reasonCode') or ''}")
        total = envelope["totalCount"]
        items.extend(envelope["items"])
        if not envelope["items"] or page * PAGE_SIZE >= (total or 0):
            break
        time.sleep(0.2)
    return items, total or 0


def build_report(items: list[dict]) -> dict:
    by_region: dict[str, dict[str, dict]] = {rid: {} for rid in gn.REGION_IDS}
    unmapped = Counter()
    for item in items:
        if item.get("sidoCdNm") != "경남":
            unmapped[f"sidoCdNm={item.get('sidoCdNm')}"] += 1
            continue
        region_id = region_id_for(item.get("sgguCdNm", ""))
        if region_id is None:
            unmapped[f"sgguCdNm={item.get('sgguCdNm')}"] += 1
            continue
        by_region[region_id][item.get("ykiho") or f"no-ykiho-{len(by_region[region_id])}"] = item
    counts = {rid: len(v) for rid, v in by_region.items()}
    kinds = {rid: Counter(i.get("clCdNm", "") for i in v.values()) for rid, v in by_region.items()}
    return {"counts": counts, "kinds": kinds, "unmapped": dict(unmapped)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    key = os.environ.get(hira.SERVICE_KEY_ENV)
    if not key:
        raise SystemExit(f"{hira.SERVICE_KEY_ENV} 환경변수가 없습니다.")
    items, total = fetch_all(key)
    report = build_report(items)
    print(f"totalCount {total} / 받은 레코드 {len(items)}")
    if len(items) != total:
        raise SystemExit("받은 레코드 수가 totalCount와 다릅니다 - 저장하지 않습니다.")
    for rid in gn.REGION_IDS:
        print(f"  {gn.NAME_BY_ID[rid]:<10} {report['counts'][rid]:>5}")
    print(f"합계 {sum(report['counts'].values())} · 제외 {report['unmapped'] or '없음'}")
    if any(v == 0 for v in report["counts"].values()):
        raise SystemExit("의료기관이 0인 지역이 있습니다 - sgguCdNm 대응 확인 필요")
    if args.write:
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region_id", "region_name", "hospital_count", "kind_breakdown", "source", "reference_date"])
            for rid in gn.REGION_IDS:
                breakdown = "; ".join(f"{k}={v}" for k, v in report["kinds"][rid].most_common())
                writer.writerow([rid, gn.NAME_BY_ID[rid], report["counts"][rid], breakdown, SOURCE_LABEL,
                                 date.today().isoformat()])
        print(f"저장: {OUT_CSV.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
