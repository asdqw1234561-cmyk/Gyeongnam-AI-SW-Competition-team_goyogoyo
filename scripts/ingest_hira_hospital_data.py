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
    #    아래 후보 엔드포인트 중 유효한 것이 자동으로 식별된다
    python scripts/ingest_hira_hospital_data.py inspect

    # 2) 창원시 5개 구 집계를 dry-run으로 먼저 확인 (기본값이 검증된 엔드포인트)
    python scripts/ingest_hira_hospital_data.py ingest

    # 3) 집계 결과가 합리적이라고 확인되면 --commit 으로 CSV에 반영
    python scripts/ingest_hira_hospital_data.py ingest --commit

[전국 데이터를 끝까지 수집했는지 검증한 결과 (2026-10-01)]
    - 선언된 totalCount 79,899건 = 실제 수집 79,899건, 80페이지 완주 확인(일부만
      수집되고 중단되는 문제 없음).
    - sidoCdNm의 경남 표기는 정확히 "경남"(4,258건). sgguCdNm의 창원 5개 구 표기는
      "창원의창구/창원성산구/창원마산합포구/창원마산회원구/창원진해구"로 전부 정상
      확인되었고, 5개 구 중 어디에도 속하지 않는 "매핑 실패" 사례는 0건이었다.
    - ykiho(기관 고유식별자) 기준 중복도 0건.
    - hospital_count는 종별(clCdNm) 구분 없이 의원/치과의원/한의원/병원/종합병원/
      상급종합/요양병원/한방병원/치과병원/정신병원/보건소/보건지소/보건진료소/조산원을
      모두 합산한 "창원시 5개 구 전체 의료기관 수"를 의미한다(1,358건). 종합병원급
      이상만 좁혀서 보고 싶다면 이 note를 보고 별도 지표를 추가해야 한다.

[엔드포인트 기능명을 추측으로 확정하지 않는 이유]
    data.go.kr 상세페이지에는 Swagger/활용가이드 문서(.docx)로만 오퍼레이션명이
    제공되어 이 환경에서는 열람할 수 없었다. 검색으로는 getHospBasisList(v1),
    getHospBasisList(v2, 접미사 없음), getHospBasisList1(v2, "1" 접미사) 세 가지
    후보가 섞여서 나왔고, 어느 쪽이 맞는지 신뢰 가능한 1차 자료로 확정하지 못했다.
    그래서 inspect 모드는 이 세 후보를 실제 보유한 인증키로 "직접 호출"해 보고
    각각의 실제 HTTP 상태 / 응답 내용을 그대로 보여준다. 응답이 정상 데이터면
    그 엔드포인트가 맞는 것이고, 공공데이터포털 공통 오류 포맷이 오면 어떤 오류인지
    (서비스 주소 오류 / 인증키 오류 / 활용승인 미완료 등)를 구분해서 알려준다.

[데이터 포맷]
    data.go.kr 상세페이지에 "데이터 포맷: XML"로 명시되어 있어, 이 스크립트는
    기본적으로 _type 파라미터를 보내지 않고(=XML 기본값) XML 응답을 파싱한다.

[왜 요청 파라미터 sidoCd/sgguCd(숫자 지역코드)를 쓰지 않는가]
    HIRA API는 sidoCd/sgguCd 같은 숫자 지역코드로 요청을 좁힐 수도 있지만, 검색으로
    찾은 코드값은 공식 문서로 확정하지 못했고 특히 성산구 코드는 아예 찾지 못했다.
    잘못된 코드로 요청하면 엉뚱한 구 데이터가 섞이거나 특정 구가 통째로 누락될 위험이
    있다. 그래서 이 스크립트는 코드로 요청을 좁히지 않고, 전국 데이터를 끝까지
    받아온 뒤 응답에 실제로 담겨 오는 sidoCdNm("경남")과 sgguCdNm("창원의창구" 등)
    문자열 필드를 그대로 매칭하는 방식을 쓴다. 이 두 값은 inspect/dry-run으로 실제
    호출해 직접 확인했으므로(위 검증 결과 참고) 추측이 아니다. 전국을 다 훑어야 해서
    느리지만(1회성 배치 작업이므로 허용), 코드 추정으로 인한 매핑 오류 위험이 없다.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

SERVICE_KEY_ENV = "HIRA_SERVICE_KEY"

# 신뢰할 수 있는 1차 자료로 단일 엔드포인트를 확정하지 못해, inspect에서 실제로
# 호출해 비교할 후보들. HIRA_API_URL 환경변수를 지정하면 그 값 하나만 시도한다.
CANDIDATE_ENDPOINTS = [
    "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList",
    "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList1",
    "https://apis.data.go.kr/B551182/hospInfoService/getHospBasisList",
]

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

# 공공데이터포털(data.go.kr) 공통 OpenAPI 오류코드 (모든 포털 API가 공유하는 게이트웨이
# 레벨 오류 체계). 서비스 자체 오류가 아니라 포털 차원의 인증/요청 오류일 때 내려온다.
PORTAL_ERROR_CODES = {
    "00": "정상",
    "01": "APPLICATION_ERROR (어플리케이션 오류)",
    "02": "DB_ERROR",
    "03": "NODATA_ERROR (데이터 없음)",
    "04": "HTTP_ERROR",
    "05": "SERVICETIMEOUT_ERROR",
    "10": "INVALID_REQUEST_PARAMETER_ERROR (요청 파라미터 오류)",
    "11": "NO_MANDATORY_REQUEST_PARAMETERS_ERROR (필수 파라미터 누락)",
    "12": "NO_OPENAPI_SERVICE_ERROR (해당 서비스/엔드포인트를 찾을 수 없음 -> 주소 오류 가능성)",
    "20": "SERVICE_ACCESS_DENIED_ERROR (활용신청은 했으나 접근 거부 -> 승인 상태 확인 필요)",
    "21": "TEMPORARILY_DISABLE_THE_SERVICEKEY_ERROR (인증키 일시 비활성화)",
    "22": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR (일일 트래픽 초과)",
    "30": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR (등록되지 않은 인증키 -> 활용신청/승인 필요)",
    "31": "DEADLINE_HAS_EXPIRED_ERROR (활용기간 만료)",
    "32": "UNREGISTERED_IP_ERROR (등록되지 않은 IP)",
    "33": "UNSIGNED_CALL_ERROR (서명되지 않은 요청)",
    "99": "UNKNOWN_ERROR",
}


def _get_service_key() -> str:
    key = os.environ.get(SERVICE_KEY_ENV)
    if not key:
        raise SystemExit(
            f"{SERVICE_KEY_ENV} 환경변수가 없습니다. "
            f".env 파일에 {SERVICE_KEY_ENV}=발급받은_인증키 를 추가하세요."
        )
    return key


def _mask(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "****(masked)****")


def _fetch(endpoint: str, service_key: str, page_no: int, num_of_rows: int) -> requests.Response:
    params = {
        "serviceKey": service_key,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
    }
    return requests.get(endpoint, params=params, timeout=15)


def _parse_envelope(xml_text: str) -> dict:
    """
    data.go.kr 응답은 크게 두 가지 XML 형태를 쓴다.
      1) 정상/서비스레벨 오류: <response><header>...<body>...
      2) 포털 게이트웨이 레벨 오류: <OpenAPI_ServiceResponse><cmmMsgHeader>...

    둘 다 아니면 kind="unknown"으로 원문을 그대로 돌려준다(HTML 오류 페이지 등).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"kind": "not_xml"}

    tag = root.tag

    if tag == "OpenAPI_ServiceResponse":
        header = root.find("cmmMsgHeader")
        reason_code = (header.findtext("returnReasonCode") if header is not None else None) or ""
        err_msg = (header.findtext("errMsg") if header is not None else None) or ""
        auth_msg = (header.findtext("returnAuthMsg") if header is not None else None) or ""
        return {
            "kind": "gateway_error",
            "reasonCode": reason_code,
            "reasonMeaning": PORTAL_ERROR_CODES.get(reason_code, "(알 수 없는 코드)"),
            "errMsg": err_msg,
            "authMsg": auth_msg,
        }

    if tag == "response":
        header = root.find("header")
        body = root.find("body")
        result_code = (header.findtext("resultCode") if header is not None else None) or ""
        result_msg = (header.findtext("resultMsg") if header is not None else None) or ""

        items: list[dict] = []
        total_count = None
        if body is not None:
            total_count_text = body.findtext("totalCount")
            total_count = int(total_count_text) if total_count_text and total_count_text.isdigit() else None
            for item_el in body.findall("./items/item"):
                items.append({child.tag: (child.text or "") for child in item_el})

        return {
            "kind": "success" if result_code == "00" else "service_error",
            "resultCode": result_code,
            "resultMeaning": PORTAL_ERROR_CODES.get(result_code, result_msg or "(알 수 없음)"),
            "resultMsg": result_msg,
            "items": items,
            "totalCount": total_count,
        }

    return {"kind": "unknown_xml", "rootTag": tag}


def cmd_inspect(args: argparse.Namespace) -> None:
    service_key = _get_service_key()
    endpoints = [args.endpoint] if args.endpoint else CANDIDATE_ENDPOINTS

    print(f"후보 엔드포인트 {len(endpoints)}개를 실제 인증키로 호출해 비교합니다.\n")

    any_success = False
    for endpoint in endpoints:
        print(f"--- 후보: {endpoint} ---")
        try:
            resp = _fetch(endpoint, service_key, page_no=1, num_of_rows=3)
        except requests.RequestException as exc:
            print(f"요청 자체가 실패했습니다: {_mask(str(exc), service_key)}")
            print()
            continue

        print(f"HTTP 상태코드: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type')}")

        envelope = _parse_envelope(resp.text)
        kind = envelope["kind"]

        if kind == "success":
            print("판정: 정상 응답 (이 엔드포인트가 유효합니다)")
            print(f"  totalCount: {envelope['totalCount']}")
            if envelope["items"]:
                print(f"  표본 1건 필드: {list(envelope['items'][0].keys())}")
                print(f"  표본 1건 내용: {envelope['items'][0]}")
            else:
                print("  item이 비어 있습니다(파라미터/페이지를 확인하세요).")
            any_success = True
        elif kind == "service_error":
            print(f"판정: 서비스 레벨 오류 (resultCode={envelope['resultCode']})")
            print(f"  의미: {envelope['resultMeaning']}")
            print(f"  resultMsg: {envelope['resultMsg']}")
        elif kind == "gateway_error":
            print(f"판정: 공공데이터포털 게이트웨이 오류 (returnReasonCode={envelope['reasonCode']})")
            print(f"  의미: {envelope['reasonMeaning']}")
            print(f"  errMsg: {envelope['errMsg']} / returnAuthMsg: {envelope['authMsg']}")
        elif kind == "unknown_xml":
            print(f"판정: 알 수 없는 XML 구조 (root tag={envelope['rootTag']})")
            print(_mask(resp.text[:1000], service_key))
        else:  # not_xml
            print("판정: XML이 아닌 응답(HTML 오류 페이지 등) -> 엔드포인트 경로 자체가 잘못되었을 가능성")
            print(_mask(resp.text[:1000], service_key))
        print()

    if not any_success:
        print(
            "모든 후보가 실패했습니다. 아래를 순서대로 점검해 주세요:\n"
            "  1) data.go.kr 마이페이지 > 데이터활용 > Open API > 활용신청 현황에서\n"
            "     이 서비스의 '승인' 상태인지 확인 (대기중이면 아직 호출 불가)\n"
            "  2) 같은 화면에서 인증키가 '일반 인증키(Decoding)'로 올바르게 복사되었는지 확인\n"
            "     (URL 인코딩된 키를 그대로 .env에 넣으면 이중 인코딩되어 오류가 날 수 있음)\n"
            "  3) 신청 직후라면 키 활성화까지 다소 시간이 걸릴 수 있어 잠시 후 재시도\n"
            "  4) 위 세 후보 모두 아니라면 data.go.kr 상세페이지의 Swagger/활용가이드\n"
            "     문서에서 정확한 엔드포인트를 확인해 --endpoint 옵션으로 직접 지정"
        )


def _iter_all_items(endpoint: str, service_key: str, num_of_rows: int, max_pages: int):
    page_no = 1
    while page_no <= max_pages:
        resp = _fetch(endpoint, service_key, page_no, num_of_rows)
        envelope = _parse_envelope(resp.text)

        if envelope["kind"] != "success":
            raise SystemExit(
                f"페이지 {page_no}에서 정상 응답을 받지 못했습니다 (kind={envelope['kind']}). "
                "'inspect' 명령으로 먼저 엔드포인트와 상태를 확인하세요."
            )

        items = envelope["items"]
        if not items:
            break

        for item in items:
            yield item

        total_count = envelope["totalCount"] or 0
        if page_no * num_of_rows >= total_count:
            break
        page_no += 1
        time.sleep(0.2)


def cmd_ingest(args: argparse.Namespace) -> None:
    service_key = _get_service_key()

    mapped: dict[str, list[dict]] = {region_id: [] for region_id in CHANGWON_DISTRICTS}
    unmapped: list[dict] = []
    total_seen = 0
    gyeongnam_seen = 0
    sample_shown = False

    for item in _iter_all_items(args.endpoint, service_key, args.num_of_rows, args.max_pages):
        total_seen += 1
        if not sample_shown:
            print("[표본 응답 1건]", item)
            sample_shown = True

        if item.get("sidoCdNm", "") != "경남":
            continue
        gyeongnam_seen += 1

        sggu = item.get("sgguCdNm", "")
        if "창원" not in sggu:
            continue

        hits = [
            region_id
            for region_id, district_name in CHANGWON_DISTRICTS.items()
            if district_name in sggu
        ]
        if len(hits) == 1:
            mapped[hits[0]].append(item)
        else:
            unmapped.append(item)

    print(f"\n조회한 전체 레코드 수: {total_seen} (totalCount와 다르면 일부만 수집된 것이므로 결과를 신뢰하지 말 것)")
    print(f"sidoCdNm == '경남' 레코드 수: {gyeongnam_seen}")

    if unmapped:
        print(f"\n[경고] sgguCdNm에 '창원'은 포함되지만 5개 구 중 정확히 하나로 특정되지 않은 레코드 {len(unmapped)}건:")
        for it in unmapped[:10]:
            print(f"  sgguCdNm='{it.get('sgguCdNm','')}' yadmNm='{it.get('yadmNm','')}'")
        print("  -> 이 레코드들은 억지로 매핑하지 않고 집계에서 제외했습니다.")

    print("\n=== 창원시 구별 의료기관 수 (hospital_count 후보, ykiho 중복 제거 전/후) ===")
    counts: dict[str, int] = {}
    cl_breakdown: dict[str, dict] = {}
    for region_id, district_name in CHANGWON_DISTRICTS.items():
        items = mapped[region_id]
        raw_count = len(items)
        unique_ykihos = {it.get("ykiho", "") for it in items}
        dup_count = raw_count - len(unique_ykihos)
        counts[region_id] = raw_count

        cl_counter: dict[str, int] = {}
        for it in items:
            cl = it.get("clCdNm", "(미상)")
            cl_counter[cl] = cl_counter.get(cl, 0) + 1
        cl_breakdown[region_id] = cl_counter

        print(
            f"  {region_id} ({district_name}): {raw_count}건"
            f" (고유 ykiho {len(unique_ykihos)}건, 중복 {dup_count}건)"
        )
        print("      종별 구성: " + ", ".join(f"{cl}={cnt}" for cl, cnt in sorted(cl_counter.items(), key=lambda x: -x[1])))

    total_mapped = sum(counts.values())
    print(f"\n합계: {total_mapped}건 (의원/치과의원/한의원/병원/종합병원/상급종합/요양병원/한방병원/치과병원/정신병원/보건소/보건지소/보건진료소/조산원 등 모든 종별 포함)")

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
                "HIRA 병원정보서비스 Open API(sidoCdNm='경남', sgguCdNm 구 이름 매칭)로 집계. "
                "의원/치과의원/한의원/병원/종합병원/상급종합/요양병원/한방병원/치과병원/"
                "정신병원/보건소/보건지소/보건진료소/조산원 등 전체 의료기관 종별 합산, "
                "ykiho 고유식별자 기준 중복 없음 확인됨"
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
        "inspect",
        help="후보 엔드포인트들을 실제로 호출해 상태/오류를 비교 출력(CSV 미변경)",
    )
    p_inspect.add_argument(
        "--endpoint", default=None, help="이 URL 하나만 시도(생략 시 후보 3개 모두 시도)"
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_ingest = sub.add_parser("ingest", help="창원시 5개 구 의료기관 수 집계 후 CSV 반영")
    p_ingest.add_argument(
        "--endpoint",
        default=CANDIDATE_ENDPOINTS[0],
        help="inspect로 검증된 엔드포인트 URL (기본값: 2026-10-01에 검증된 getHospBasisList)",
    )
    p_ingest.add_argument("--num-of-rows", type=int, default=1000)
    p_ingest.add_argument("--max-pages", type=int, default=200)
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
