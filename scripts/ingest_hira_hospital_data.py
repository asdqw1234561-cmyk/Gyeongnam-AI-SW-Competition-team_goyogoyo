# 건강보험심사평가원(HIRA) 병원정보서비스 -> data/region_indicators.csv 반영 스크립트
"""
건강보험심사평가원(HIRA) 병원정보서비스 Open API에서 창원시 5개 구의 병원 수를
수집해 data/region_indicators.csv의 hospital_count 지표에 반영하는 1회성 수집 스크립트.

[무료 인증키 발급 절차]
    1. https://www.data.go.kr 회원가입 및 로그인
    2. https://www.data.go.kr/data/15001698/openapi.do 접속 -> "활용신청" 클릭
    3. 활용 목적을 간단히 작성하고 신청(개발계정은 보통 자동승인)
    4. 마이페이지 > 데이터활용 > Open API > 활용신청 현황에서
       "일반 인증키(Decoding)" 값을 복사
    5. 프로젝트 루트에 .env 파일을 만들고 아래 한 줄을 추가
           HIRA_SERVICE_KEY=발급받은_인증키
       (.env는 .gitignore에 이미 등록되어 있어 git에 올라가지 않는다)

[사용 순서]
    # 1) 실제 응답 구조를 먼저 눈으로 확인한다 (CSV는 전혀 건드리지 않음)
    python scripts/ingest_hira_hospital_data.py inspect

    # 2) inspect 결과에서 주소 필드명을 확인한 뒤 dry-run으로 집계만 확인
    python scripts/ingest_hira_hospital_data.py ingest --addr-field addr

    # 3) 집계 결과(구별 병원 수)가 합리적이라고 확인되면 --commit 으로 CSV에 반영
    python scripts/ingest_hira_hospital_data.py ingest --addr-field addr --commit

[검증되지 않은 부분 - 주의]
    - API_BASE(엔드포인트 URL)는 HIRA 병원정보서비스의 일반적인 명명 규칙을 따른
      "best effort" 값이며, 실제 인증키로 inspect를 실행하기 전까지는 확정이 아니다.
      404/오류가 나면 data.go.kr 상세페이지의 "OpenAPI활용가이드" 문서나 Swagger에서
      정확한 경로를 확인해 HIRA_API_URL 환경변수로 덮어쓰면 된다.
    - 응답의 주소 필드명(addr 등)도 inspect로 직접 확인 후 --addr-field 로 지정해야 한다.

[왜 시군구코드(sgguCd) 대신 주소 문자열 매칭을 쓰는가]
    HIRA API는 sidoCd/sgguCd 지역코드로도 조회할 수 있지만, 공식 문서 확인 없이 검색
    결과만으로 추정한 코드값을 쓰면 엉뚱한 구에 데이터가 매핑될 위험이 있다(특히 성산구
    코드는 조사 과정에서 확인하지 못했다). 이를 피하기 위해 이 스크립트는 응답에 포함된
    주소 문자열에서 "의창구" 같은 구 이름을 직접 매칭하는 방식만 사용한다. 전국 데이터를
    모두 훑어야 하므로 느리지만(1회성 배치 작업이므로 허용), 잘못된 코드로 인한 매핑
    오류 위험이 없다.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

SERVICE_KEY_ENV = "HIRA_SERVICE_KEY"
API_BASE = os.environ.get(
    "HIRA_API_URL",
    "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList1",
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_THIS_DIR)
INDICATORS_CSV = os.path.join(_ROOT_DIR, "data", "region_indicators.csv")

CHANGWON_DISTRICTS = {
    "CW-UICHANG": "의창구",
    "CW-SEONGSAN": "성산구",
    "CW-MASANHAPPO": "마산합포구",
    "CW-MASANHOEWON": "마산회원구",
    "CW-JINHAE": "진해구",
}


def _get_service_key() -> str:
    key = os.environ.get(SERVICE_KEY_ENV)
    if not key:
        raise SystemExit(
            f"{SERVICE_KEY_ENV} 환경변수가 없습니다. "
            f".env 파일에 {SERVICE_KEY_ENV}=발급받은_인증키 를 추가하세요."
        )
    return key


def _fetch_page(service_key: str, page_no: int, num_of_rows: int) -> requests.Response:
    params = {
        "serviceKey": service_key,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "_type": "json",
    }
    resp = requests.get(API_BASE, params=params, timeout=15)
    resp.raise_for_status()
    return resp


def cmd_inspect(args: argparse.Namespace) -> None:
    service_key = _get_service_key()
    resp = _fetch_page(service_key, page_no=1, num_of_rows=5)
    print("요청 URL:", resp.url)
    print("HTTP 상태코드:", resp.status_code)
    print("Content-Type:", resp.headers.get("Content-Type"))
    print("--- 응답 본문(앞부분) ---")
    print(resp.text[:3000])


def _iter_all_items(service_key: str, num_of_rows: int, max_pages: int):
    page_no = 1
    while page_no <= max_pages:
        resp = _fetch_page(service_key, page_no, num_of_rows)
        data = resp.json()
        try:
            body = data["response"]["body"]
        except (KeyError, TypeError):
            print("예상치 못한 응답 구조입니다. 'inspect' 명령으로 먼저 구조를 확인하세요.")
            print(data)
            return

        item_list = (body.get("items") or {}).get("item") or []
        if isinstance(item_list, dict):
            item_list = [item_list]
        if not item_list:
            break

        for item in item_list:
            yield item

        total_count = int(body.get("totalCount", 0) or 0)
        if page_no * num_of_rows >= total_count:
            break
        page_no += 1
        time.sleep(0.2)


def cmd_ingest(args: argparse.Namespace) -> None:
    service_key = _get_service_key()
    addr_field = args.addr_field

    counts = {region_id: 0 for region_id in CHANGWON_DISTRICTS}
    sample_shown = False
    total_seen = 0

    for item in _iter_all_items(service_key, args.num_of_rows, args.max_pages):
        total_seen += 1
        if not sample_shown:
            print("[표본 응답 1건]", item)
            sample_shown = True

        addr = str(item.get(addr_field, ""))
        if "창원" not in addr:
            continue
        for region_id, district_name in CHANGWON_DISTRICTS.items():
            if district_name in addr:
                counts[region_id] += 1
                break

    print(f"\n조회한 전체 레코드 수: {total_seen}")
    print("=== 창원시 구별 병원 수 집계 결과 ===")
    for region_id, district_name in CHANGWON_DISTRICTS.items():
        print(f"  {region_id} ({district_name}): {counts[region_id]}개")

    if not args.commit:
        print("\n--commit 옵션 없이 실행되어 CSV에는 반영하지 않았습니다 (dry-run).")
        return

    _write_hospital_counts_to_csv(
        counts, source=args.source_label, reference_date=args.reference_date
    )
    print("\ndata/region_indicators.csv 의 hospital_count 행을 갱신했습니다 (data_status=확보).")


def _write_hospital_counts_to_csv(counts: dict, source: str, reference_date: str) -> None:
    with open(INDICATORS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    updated = 0
    for row in rows:
        if row["indicator_code"] == "hospital_count" and row["region_id"] in counts:
            row["value"] = str(counts[row["region_id"]])
            row["source"] = source
            row["reference_date"] = reference_date
            row["data_status"] = "확보"
            row["note"] = (
                "HIRA 병원정보서비스 Open API 응답의 주소 필드에서 "
                "구 이름 문자열 매칭으로 집계"
            )
            updated += 1

    if updated != len(counts):
        raise SystemExit(
            f"hospital_count 행을 {updated}개만 찾았습니다(기대: {len(counts)}). "
            "data/region_indicators.csv 구조가 바뀌지 않았는지 확인하세요."
        )

    with open(INDICATORS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect", help="API 원본 응답을 그대로 출력(필드명 확인용, CSV 미변경)"
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_ingest = sub.add_parser("ingest", help="창원시 5개 구 병원 수 집계 후 CSV 반영")
    p_ingest.add_argument(
        "--addr-field", default="addr", help="응답에서 주소가 담긴 필드명 (inspect로 먼저 확인)"
    )
    p_ingest.add_argument("--num-of-rows", type=int, default=500)
    p_ingest.add_argument("--max-pages", type=int, default=300)
    p_ingest.add_argument(
        "--source-label",
        default="건강보험심사평가원 병원정보서비스 Open API (data.go.kr 15001698)",
    )
    p_ingest.add_argument("--reference-date", default=date.today().isoformat())
    p_ingest.add_argument(
        "--commit", action="store_true", help="집계 결과를 실제로 CSV에 반영"
    )
    p_ingest.set_defaults(func=cmd_ingest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
