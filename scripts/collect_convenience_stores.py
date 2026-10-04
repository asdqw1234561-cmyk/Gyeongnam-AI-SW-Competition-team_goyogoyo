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
    - 수집 완전성: 구마다 모든 페이지 성공 + totalCount 불변 + max_pages 안에 종료 +
      받은 레코드 수 == totalCount 일 때만 '확보'로 저장한다. 하나라도 어긋나면 그 구는
      '미확보'로 남기고, --save 시 기존 CSV를 덮어쓰지 않는다(--allow-partial 예외).
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secret_mask import mask_secret, request_error_kind  # noqa: E402

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
    try:
        resp = requests.get(url, params=query, timeout=20)
    except requests.RequestException as exc:
        # 예외 문자열에는 serviceKey가 든 요청 URL이 들어간다 - 종류만 남기고 원래 예외는 잇지 않는다.
        raise ApiError(request_error_kind(exc)) from None
    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code}: {mask_secret(resp.text[:300], service_key)}")
    try:
        data = resp.json()
    except ValueError:
        # data.go.kr 게이트웨이 오류(인증키 미등록 등)는 type=json이어도 XML로 온다.
        raise ApiError(f"JSON이 아닌 응답: {mask_secret(resp.text[:500], service_key)}") from None
    data = data.get("response", data)  # 일부 data.go.kr API는 response로 한 번 감싼다
    header = data.get("header") or {}
    code = str(header.get("resultCode", "00"))
    if code not in ("00", "03"):  # 03 = 데이터 없음(NODATA_ERROR)
        raise ApiError(mask_secret(f"resultCode={code} resultMsg={header.get('resultMsg')}", service_key))
    return data


def _items(data: dict) -> list[dict]:
    body = data.get("body") or {}
    items = body.get("items") or []
    if isinstance(items, dict):  # XML->JSON 변환형 응답 대비
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items


class DistrictFetch:
    """한 구의 페이지 조회 결과와 '빠짐없이 다 받았는지' 판정."""

    def __init__(self):
        self.items: list[dict] = []
        self.reference_yms: set[str] = set()
        self.total_count: int | None = None
        self.pages: int = 0
        self.complete: bool = False
        self.reason: str = ""


def _call_with_retry(params: dict, service_key: str, retries: int, retry_wait: float) -> dict:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _call("storeListInDong", params, service_key)
        except (ApiError, requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(retry_wait * (attempt + 1))
    raise ApiError(f"pageNo={params.get('pageNo')} {retries + 1}회 시도 실패: {last_exc}")


def fetch_district(service_key: str, signgu_cd: str, inds_scls_cd: str | None,
                   num_of_rows: int, max_pages: int,
                   retries: int = 2, retry_wait: float = 1.0) -> DistrictFetch:
    """
    한 구의 상가업소를 모든 페이지에 걸쳐 받는다. 예외를 던지지 않고 결과에 complete/reason을 남긴다.

    complete=True 조건(모두 만족해야 '확보'):
      - 모든 페이지 호출 성공(재시도 포함)
      - 응답에 totalCount가 있고 페이지마다 값이 바뀌지 않음
      - max_pages 안에 끝남
      - 실제로 받은 레코드 수 == totalCount
    """
    result = DistrictFetch()
    params = {"divId": "signguCd", "key": signgu_cd, "numOfRows": num_of_rows}
    if inds_scls_cd:
        params["indsSclsCd"] = inds_scls_cd

    page_no = 1
    while True:
        if page_no > max_pages:
            result.reason = (f"max_pages({max_pages}) 도달: {len(result.items)}/"
                             f"{result.total_count}건만 조회")
            return result
        try:
            data = _call_with_retry({**params, "pageNo": page_no}, service_key,
                                    retries, retry_wait)
        except ApiError as exc:
            result.reason = f"페이지 조회 실패({len(result.items)}건 받은 뒤 중단): {exc}"[:300]
            return result

        result.pages = page_no
        header = data.get("header") or {}
        if header.get("stdrYm"):
            result.reference_yms.add(str(header["stdrYm"]))

        raw_total = (data.get("body") or {}).get("totalCount")
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            if str(header.get("resultCode")) == "03":  # 데이터 없음
                total = 0
            else:
                result.reason = f"pageNo={page_no} 응답에 totalCount가 없어 완전성 검증 불가"
                return result
        if result.total_count is None:
            result.total_count = total
        elif total != result.total_count:
            result.reason = (f"조회 중 totalCount 변경({result.total_count} -> {total}), "
                             "데이터 갱신 중일 수 있어 재수집 필요")
            return result

        items = _items(data)
        result.items.extend(items)

        if len(result.items) >= result.total_count:
            break
        if not items:
            result.reason = (f"pageNo={page_no}가 비어 있음: {len(result.items)}/"
                             f"{result.total_count}건만 조회")
            return result
        page_no += 1
        time.sleep(0.1)

    if len(result.items) != result.total_count:
        result.reason = f"받은 레코드 {len(result.items)}건 != totalCount {result.total_count}건"
        return result
    result.complete = True
    return result


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
            num_of_rows: int, max_pages: int,
            retries: int = 2, retry_wait: float = 1.0) -> tuple[list[dict], list[dict]]:
    """
    5개 구를 수집해 (업소 행 목록, 구별 집계 행 목록)을 반환한다.

    한 구라도 페이지가 빠졌으면(fetch_district().complete == False) 그 구는
    count를 비우고 data_status="미확보"로 남기며, 일부만 받은 업소는 목록에 넣지 않는다.
    """
    collected = date.today().isoformat()
    stores: list[dict] = []
    counts: list[dict] = []
    seen_ids: set[str] = set()

    for signgu_cd, (region_id, district) in CHANGWON_DISTRICTS.items():
        print(f"- {district}({signgu_cd}) 조회 중...")
        fetched = fetch_district(service_key, signgu_cd, inds_scls_cd, num_of_rows,
                                 max_pages, retries, retry_wait)
        if not fetched.complete:
            print(f"  [미확보] {district}: {fetched.reason}")
            counts.append(_count_row(region_id, district, type_name, None, NOT_SECURED,
                                     collected, f"수집 불완전: {fetched.reason}"[:300]))
            continue

        scanned = len(fetched.items)
        mismatched = 0
        district_rows: list[dict] = []
        for item in fetched.items:
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

        reference_ym = ",".join(sorted(fetched.reference_yms)) or NOT_SECURED
        for item in district_rows:
            stores.append(_to_store_row(item, region_id, district, reference_ym, collected))

        note = (f"조회 레코드 {scanned}건(totalCount 일치, {fetched.pages}페이지) 중 "
                f"indsSclsNm='{type_name}' 필터")
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
                             args.num_of_rows, args.max_pages, args.retries)
    incomplete = [row["district"] for row in counts if row["data_status"] != "확보"]

    print("\n=== 창원시 구별 편의점 수 ===")
    for row in counts:
        value = row["count"] if row["data_status"] == "확보" else NOT_SECURED
        print(f"  {row['district']:<6} {value}")
    by_district = Counter(s["district"] for s in stores)
    print(f"  합계: {sum(by_district.values())}개")

    if not args.save:
        print("\n--save 없이 실행되어 파일은 만들지 않았습니다 (dry-run).")
        return
    if incomplete and not args.allow_partial:
        print(f"\n[저장 안 함] 수집이 불완전한 구가 있습니다: {', '.join(incomplete)}")
        print("기존 CSV를 그대로 두었습니다. 잠시 후 다시 실행하세요.")
        print("불완전한 구를 '미확보'로 표시한 채 저장하려면 --allow-partial 을 붙이세요.")
        sys.exit(2)
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
    p.add_argument("--retries", type=int, default=2, help="페이지 호출 실패 시 재시도 횟수")
    p.add_argument("--allow-partial", action="store_true",
                   help="불완전한 구가 있어도 저장(해당 구는 미확보, 업소 목록 제외)")
    p.set_defaults(func=cmd_collect)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ApiError as exc:
        sys.exit(f"API 오류: {exc}")


if __name__ == "__main__":
    main()
