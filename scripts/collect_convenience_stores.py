# 소상공인시장진흥공단 상가(상권)정보 API -> 창원시 5개 구 편의점 수집 스크립트
"""
소상공인시장진흥공단_상가(상권)정보_API(data.go.kr 15012005)의 storeListInDong
오퍼레이션으로 창원시 5개 구의 상가업소를 조회한 뒤, 상권업종 소분류명이 "편의점"인
업소만 골라 CSV로 저장하고 구별 개수를 집계한다.

[인증키 발급]
    1. https://www.data.go.kr 로그인
    2. https://www.data.go.kr/data/15012005/openapi.do -> "활용신청"
    3. 마이페이지 > 데이터활용 > Open API > 활용신청 현황에서 "일반 인증키(Decoding)" 복사
    4. 프로젝트 루트의 .env 파일에 한 줄 추가 (.env는 .gitignore에 등록되어 있음)
           SBIZ_SERVICE_KEY=발급받은_인증키
       data.go.kr 인증키는 계정 단위이므로 HIRA_SERVICE_KEY와 값이 같아도 된다.
       단, 이 API에 대해 별도로 "활용신청"은 해야 한다.

[사용 순서]
    # 1) 응답 구조 확인 (파일을 전혀 만들지 않음)
    python scripts/collect_convenience_stores.py inspect

    # 2) 수집 + 집계 결과만 화면에 출력 (dry-run, 파일 미생성)
    python scripts/collect_convenience_stores.py collect

    # 3) 결과가 합리적이면 CSV로 저장
    python scripts/collect_convenience_stores.py collect --save

[설계 메모 - 검증된 것 / 검증 안 된 것]
    - 시군구코드(48121 의창구, 48123 성산구, 48125 마산합포구, 48127 마산회원구,
      48129 진해구)는 행정표준코드 기준값이다. 추가로, 응답의 signguNm이 기대한 구
      이름과 다르면 해당 레코드를 집계에서 제외하고 경고를 출력한다(코드 오매핑 방지).
    - "편의점" 업종 소분류코드(indsSclsCd)는 공식 문서로 확인하지 못했다. 그래서 기본
      동작은 업종 필터 없이 구 전체 상가를 받아 indsSclsNm == "편의점" 으로 걸러낸다.
      (요청 수는 늘지만 코드 추정으로 인한 누락 위험이 없다.)
      `codes` 명령으로 실제 코드를 확인한 뒤 --inds-scls-cd 로 넘기면 서버에서 걸러져
      훨씬 빨라진다.
    - 기준일은 응답 header의 stdrYm(기준년월)을 사용한다. 응답에 없으면 "미확보"로 둔다.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

SERVICE_KEY_ENV = "SBIZ_SERVICE_KEY"
API_ROOT = os.environ.get("SBIZ_API_ROOT", "https://apis.data.go.kr/B553077/api/open/sdsc2")
SOURCE_LABEL = "소상공인시장진흥공단_상가(상권)정보_API (data.go.kr 15012005)"
FACILITY_TYPE = "편의점"
NOT_SECURED = "미확보"

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_THIS_DIR)
OUT_DIR = os.path.join(_ROOT_DIR, "data", "convenience")
STORES_CSV = os.path.join(OUT_DIR, "changwon_convenience_stores.csv")
COUNTS_CSV = os.path.join(OUT_DIR, "changwon_convenience_counts.csv")

# signguCd -> (region_id, 구 이름). region_id는 data/regions.csv와 동일하게 맞춘다.
CHANGWON_DISTRICTS = {
    "48121": ("CW-UICHANG", "의창구"),
    "48123": ("CW-SEONGSAN", "성산구"),
    "48125": ("CW-MASANHAPPO", "마산합포구"),
    "48127": ("CW-MASANHOEWON", "마산회원구"),
    "48129": ("CW-JINHAE", "진해구"),
}

STORE_COLUMNS = [
    "region_id", "district", "facility_name", "branch_name", "facility_type",
    "road_address", "jibun_address", "adong_name", "lon", "lat",
    "bizes_id", "source", "reference_ym", "collected_date",
]
COUNT_COLUMNS = [
    "region_id", "district", "facility_type", "count", "unit",
    "source", "reference_ym", "collected_date", "data_status", "note",
]


class ApiError(RuntimeError):
    pass


def _get_service_key() -> str:
    key = os.environ.get(SERVICE_KEY_ENV)
    if not key:
        raise SystemExit(
            f"{SERVICE_KEY_ENV} 환경변수가 없습니다. "
            f".env 파일에 {SERVICE_KEY_ENV}=발급받은_인증키 를 추가하세요."
        )
    return key


def _call(operation: str, params: dict, service_key: str) -> dict:
    """API를 호출해 JSON(dict)을 반환한다. 인증 오류 등 비정상 응답은 ApiError."""
    url = f"{API_ROOT}/{operation}"
    query = {"serviceKey": service_key, "type": "json", **params}
    resp = requests.get(url, params=query, timeout=20)
    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError:
        # data.go.kr 게이트웨이 오류(인증키 미등록 등)는 type=json이어도 XML로 온다.
        raise ApiError(f"JSON이 아닌 응답: {resp.text[:500]}")
    data = data.get("response", data)  # 일부 data.go.kr API는 response로 한 번 감싼다
    header = data.get("header") or {}
    code = str(header.get("resultCode", "00"))
    if code not in ("00", "03"):  # 03 = 데이터 없음(NODATA_ERROR)
        raise ApiError(f"resultCode={code} resultMsg={header.get('resultMsg')}")
    return data


def _items(data: dict) -> list[dict]:
    body = data.get("body") or {}
    items = body.get("items") or []
    if isinstance(items, dict):  # XML->JSON 변환형 응답 대비
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items


def iter_district_stores(service_key: str, signgu_cd: str, inds_scls_cd: str | None,
                         num_of_rows: int, max_pages: int):
    """한 구의 상가업소를 페이지 단위로 모두 순회한다. (header, item) 튜플을 yield."""
    params = {"divId": "signguCd", "key": signgu_cd, "numOfRows": num_of_rows}
    if inds_scls_cd:
        params["indsSclsCd"] = inds_scls_cd
    page_no = 1
    while page_no <= max_pages:
        data = _call("storeListInDong", {**params, "pageNo": page_no}, service_key)
        items = _items(data)
        header = data.get("header") or {}
        for item in items:
            yield header, item
        total = int((data.get("body") or {}).get("totalCount") or 0)
        if not items or page_no * num_of_rows >= total:
            return
        page_no += 1
        time.sleep(0.1)
    print(f"  [경고] {signgu_cd}: max_pages({max_pages})에 도달해 일부만 조회했을 수 있습니다.")


def _to_store_row(item: dict, region_id: str, district: str,
                  reference_ym: str, collected: str) -> dict:
    return {
        "region_id": region_id,
        "district": district,
        "facility_name": item.get("bizesNm", ""),
        "branch_name": item.get("brchNm", "") or "",
        "facility_type": item.get("indsSclsNm", ""),
        "road_address": item.get("rdnmAdr", "") or "",
        "jibun_address": item.get("lnoAdr", "") or "",
        "adong_name": item.get("adongNm", "") or "",
        "lon": item.get("lon", ""),
        "lat": item.get("lat", ""),
        "bizes_id": item.get("bizesId", ""),
        "source": SOURCE_LABEL,
        "reference_ym": reference_ym,
        "collected_date": collected,
    }


def collect(service_key: str, inds_scls_cd: str | None, type_name: str,
            num_of_rows: int, max_pages: int) -> tuple[list[dict], list[dict]]:
    """5개 구를 수집해 (업소 행 목록, 구별 집계 행 목록)을 반환한다."""
    collected = date.today().isoformat()
    stores: list[dict] = []
    counts: list[dict] = []
    seen_ids: set[str] = set()

    for signgu_cd, (region_id, district) in CHANGWON_DISTRICTS.items():
        print(f"- {district}({signgu_cd}) 조회 중...")
        reference_yms: set[str] = set()
        scanned = mismatched = 0
        district_rows: list[dict] = []
        try:
            for header, item in iter_district_stores(
                service_key, signgu_cd, inds_scls_cd, num_of_rows, max_pages
            ):
                scanned += 1
                if header.get("stdrYm"):
                    reference_yms.add(str(header["stdrYm"]))
                if item.get("indsSclsNm") != type_name:
                    continue
                signgu_nm = str(item.get("signguNm", ""))
                if district not in signgu_nm:
                    mismatched += 1
                    continue
                bizes_id = str(item.get("bizesId", ""))
                if bizes_id and bizes_id in seen_ids:
                    continue
                seen_ids.add(bizes_id)
                district_rows.append(item)
        except (ApiError, requests.RequestException) as exc:
            print(f"  [실패] {district}: {exc}")
            counts.append(_count_row(region_id, district, type_name, None,
                                     NOT_SECURED, collected, f"API 호출 실패: {exc}"[:200]))
            continue

        reference_ym = ",".join(sorted(reference_yms)) or NOT_SECURED
        for item in district_rows:
            stores.append(_to_store_row(item, region_id, district, reference_ym, collected))

        note = f"조회 레코드 {scanned}건 중 indsSclsNm='{type_name}' 필터"
        if mismatched:
            note += f", signguNm 불일치 {mismatched}건 제외"
        counts.append(_count_row(region_id, district, type_name, len(district_rows),
                                 reference_ym, collected, note))
        print(f"  조회 {scanned}건 -> {type_name} {len(district_rows)}개 (기준년월 {reference_ym})")

    return stores, counts


def _count_row(region_id, district, type_name, count, reference_ym, collected, note) -> dict:
    return {
        "region_id": region_id,
        "district": district,
        "facility_type": type_name,
        "count": "" if count is None else count,
        "unit": "개",
        "source": SOURCE_LABEL,
        "reference_ym": reference_ym,
        "collected_date": collected,
        "data_status": NOT_SECURED if count is None else "확보",
        "note": note,
    }


def _mart_not_secured_rows() -> list[dict]:
    """대형마트는 이번 범위에서 수집하지 않았으므로 미확보로 명시한다."""
    return [
        {
            "region_id": region_id, "district": district, "facility_type": "대형마트",
            "count": "", "unit": "개", "source": "", "reference_ym": "",
            "collected_date": "", "data_status": NOT_SECURED,
            "note": "이번 단계 수집 범위 아님(편의점 우선 수집)",
        }
        for region_id, district in CHANGWON_DISTRICTS.values()
    ]


def _write_csv(path: str, columns: list[str], rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # utf-8-sig: 엑셀에서 열어도 한글이 깨지지 않게 BOM 포함
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- commands
def cmd_inspect(args: argparse.Namespace) -> None:
    key = _get_service_key()
    signgu_cd = args.signgu_cd
    data = _call("storeListInDong",
                 {"divId": "signguCd", "key": signgu_cd, "numOfRows": 3, "pageNo": 1}, key)
    print("header:", data.get("header"))
    body = data.get("body") or {}
    print("totalCount:", body.get("totalCount"))
    for item in _items(data):
        print("item:", item)


def cmd_codes(args: argparse.Namespace) -> None:
    """업종 코드 조회 API로 이름에 키워드가 들어간 소분류 코드를 찾는다."""
    key = _get_service_key()
    keyword = args.keyword
    for lcls in _items(_call("largeUpjongList", {}, key)):
        lcd = lcls.get("indsLclsCd")
        for mcls in _items(_call("middleUpjongList", {"indsLclsCd": lcd}, key)):
            mcd = mcls.get("indsMclsCd")
            for scls in _items(_call("smallUpjongList",
                                     {"indsLclsCd": lcd, "indsMclsCd": mcd}, key)):
                if keyword in str(scls.get("indsSclsNm", "")):
                    print(f"{lcd} {lcls.get('indsLclsNm')} > {mcd} {mcls.get('indsMclsNm')}"
                          f" > {scls.get('indsSclsCd')} {scls.get('indsSclsNm')}")


def cmd_collect(args: argparse.Namespace) -> None:
    key = _get_service_key()
    stores, counts = collect(key, args.inds_scls_cd, args.type_name,
                             args.num_of_rows, args.max_pages)

    print("\n=== 창원시 구별 편의점 수 ===")
    for row in counts:
        value = row["count"] if row["data_status"] == "확보" else NOT_SECURED
        print(f"  {row['district']:<6} {value}")
    by_district = Counter(s["district"] for s in stores)
    print(f"  합계: {sum(by_district.values())}개")

    if not args.save:
        print("\n--save 없이 실행되어 파일은 만들지 않았습니다 (dry-run).")
        return
    _write_csv(STORES_CSV, STORE_COLUMNS, stores)
    _write_csv(COUNTS_CSV, COUNT_COLUMNS, counts + _mart_not_secured_rows())
    print(f"\n저장 완료:\n  {STORES_CSV}\n  {COUNTS_CSV}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="창원시 편의점 공공데이터 수집")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="원본 응답 확인(파일 미생성)")
    p.add_argument("--signgu-cd", default="48121", help="시군구코드 (기본: 의창구 48121)")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("codes", help="업종 소분류 코드 찾기")
    p.add_argument("--keyword", default="편의점")
    p.set_defaults(func=cmd_codes)

    p = sub.add_parser("collect", help="5개 구 수집 및 집계")
    p.add_argument("--save", action="store_true", help="CSV로 저장")
    p.add_argument("--inds-scls-cd", default=None,
                   help="업종 소분류코드(codes 명령으로 확인). 지정하면 서버에서 필터링해 빨라짐")
    p.add_argument("--type-name", default=FACILITY_TYPE, help="indsSclsNm 일치 기준 (기본: 편의점)")
    p.add_argument("--num-of-rows", type=int, default=1000)
    p.add_argument("--max-pages", type=int, default=200)
    p.set_defaults(func=cmd_collect)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ApiError as exc:
        sys.exit(f"API 오류: {exc}")


if __name__ == "__main__":
    main()
