# 국토교통부 전월세 실거래가 API -> 창원시 5개 구 임대차 거래 수집·정규화 (G4-A, 앱 미연결)
"""
국토교통부 전월세 실거래가 OpenAPI 4종에서 창원시 5개 구의 임대차 거래를 받아 주택유형별로
정규화한 스냅샷 CSV를 만든다. 주거비 점수는 만들지 않는다 - 분석은
scripts/analyze_rent_transactions.py, 앱(app.py·Agent)은 이 데이터를 아직 읽지 않는다.

[API - 공식 기술문서(data.go.kr 각 데이터 페이지의 "…기술문서.hwp", 2026-10-02 확인) 기준]
    아파트      15126474  RTMSDataSvcAptRent/getRTMSDataSvcAptRent    전용면적 excluUseAr, 단지 aptNm·aptSeq
    오피스텔    15126475  RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent  전용면적 excluUseAr, 단지 offiNm
                          (기술문서 본문의 URL은 'ggetRTMSDataSvcOffiRent'로 오타 - 실제 호출로 확인 필요)
    연립다세대  15126473  RTMSDataSvcRHRent/getRTMSDataSvcRHRent      전용면적 excluUseAr, houseType(연립/다세대), mhouseNm
    단독/다가구 15126472  RTMSDataSvcSHRent/getRTMSDataSvcSHRent      전용면적 없음 - totalFloorAr(연면적, 건물 전체)만 있음
    공통: 요청 LAWD_CD(법정동코드 앞 5자리)·DEAL_YMD(계약년월), 응답 XML, 금액(deposit·monthlyRent) 단위 만원
          (예: "50,000"처럼 쉼표 포함), 동·호 미제공, 결과코드 "000"=정상.

[정규화 규칙]
    - region_id: LAWD_CD 48121/48123/48125/48127/48129 -> 기존 region_id 5개(1:1). 응답 sggCd가 요청 코드와
      다르면 그 행은 버리고 경고(코드 오매핑 방지).
    - rent_type: 기술문서는 contractType을 "계약구분"이라고만 하고 값 정의가 없다. 기술문서 응답 예시의 전세 거래가
      monthlyRent=0 이므로 **monthlyRent == 0 -> jeonse, > 0 -> monthly(반전세 포함)** 로 판정하되,
      실제 응답의 contractType 값 분포와 monthlyRent==0 비율을 manifest에 남겨 사람이 확인한다(inspect 명령).
    - exclusive_area_m2: 단독/다가구는 비워 둔다(연면적을 전용면적처럼 쓰지 않음). 연면적은 total_floor_area_m2에 따로.
    - 금액은 만원 정수. 숫자로 읽을 수 없으면 그 행을 버리고 사유를 센다(값을 추정하지 않음).
    - 동·호·도로명 상세는 저장하지 않는다.

[인증키]
    .env 에 MOLIT_SERVICE_KEY=발급받은_인증키(Decoding) - 없으면 HIRA_SERVICE_KEY 를 쓴다(data.go.kr 키는 계정 단위,
    단 API마다 활용신청 필요). 키와 키가 들어간 요청 URL은 절대 출력하지 않는다.

[사용 순서]
    python scripts/collect_rent_transactions.py inspect                      # 유형별 1회 호출, 응답 구조·contractType 값 확인
    python scripts/collect_rent_transactions.py collect --end 202609         # 수집 + 요약만 출력(파일 미생성)
    python scripts/collect_rent_transactions.py collect --end 202609 --save  # data/housing/ 에 저장
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

API_ROOT = "https://apis.data.go.kr/1613000"
SERVICE_KEY_ENVS = ("MOLIT_SERVICE_KEY", "HIRA_SERVICE_KEY")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "housing")
TRANSACTIONS_CSV = "changwon_rent_transactions.csv"
MANIFEST_JSON = "collection_manifest.json"
PAGE_SIZE = 1000

DISTRICTS: dict[str, tuple[str, str]] = {   # LAWD_CD -> (region_id, 구 이름)
    "48121": ("CW-UICHANG", "의창구"),
    "48123": ("CW-SEONGSAN", "성산구"),
    "48125": ("CW-MASANHAPPO", "마산합포구"),
    "48127": ("CW-MASANHOEWON", "마산회원구"),
    "48129": ("CW-JINHAE", "진해구"),
}

HOUSING_TYPES: dict[str, dict] = {
    "apartment": {"label": "아파트", "operation": "RTMSDataSvcAptRent/getRTMSDataSvcAptRent", "dataset": "15126474",
                  "name_field": "aptNm", "complex_field": "aptSeq"},
    "officetel": {"label": "오피스텔", "operation": "RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent", "dataset": "15126475",
                  "name_field": "offiNm", "complex_field": None},
    "row_multi": {"label": "연립다세대", "operation": "RTMSDataSvcRHRent/getRTMSDataSvcRHRent", "dataset": "15126473",
                  "name_field": "mhouseNm", "complex_field": None},
    "detached_multi": {"label": "단독/다가구", "operation": "RTMSDataSvcSHRent/getRTMSDataSvcSHRent", "dataset": "15126472",
                       "name_field": None, "complex_field": None},
}

COLUMNS = [
    "region_id", "region_name", "sgg_cd", "legal_dong", "contract_ym", "contract_date",
    "housing_type", "housing_subtype", "rent_type", "deposit_manwon", "monthly_rent_manwon",
    "exclusive_area_m2", "total_floor_area_m2", "building_year", "complex_key",
    "contract_type_raw", "renewal_right_used", "source",
]


class ApiError(RuntimeError):
    """결과코드 오류. 메시지에 요청 URL·키를 넣지 않는다."""


def _service_key() -> str:
    for env in SERVICE_KEY_ENVS:
        if os.environ.get(env):
            return os.environ[env]
    sys.exit(f"인증키가 없습니다. .env 에 {SERVICE_KEY_ENVS[0]}=발급받은_인증키 를 추가하세요.")


def source_label(housing_type: str) -> str:
    info = HOUSING_TYPES[housing_type]
    return f"국토교통부_{info['label']} 전월세 실거래가 자료 (data.go.kr {info['dataset']})"


def parse_response(content: bytes) -> tuple[int, list[dict]]:
    """XML 응답 -> (totalCount, item dict 목록). 결과코드가 정상이 아니면 ApiError."""
    root = ET.fromstring(content)
    code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    if code not in ("000", "00"):
        msg = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or "").strip()
        raise ApiError(f"resultCode {code} {msg}")
    total = int((root.findtext(".//totalCount") or "0").strip() or 0)
    items = [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]
    return total, items


def _call(housing_type: str, lawd_cd: str, deal_ymd: str, page: int, key: str, timeout: float) -> tuple[int, list[dict]]:
    url = f"{API_ROOT}/{HOUSING_TYPES[housing_type]['operation']}"
    params = {"serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd, "pageNo": page, "numOfRows": PAGE_SIZE}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:  # 예외 문자열에는 키가 든 URL이 들어갈 수 있어 유형만 남긴다
        raise ApiError(f"네트워크 오류({type(exc).__name__})") from None
    if resp.status_code != 200 and b"<" not in resp.content[:50]:
        raise ApiError(f"HTTP {resp.status_code}")
    return parse_response(resp.content)


def fetch_month(housing_type: str, lawd_cd: str, deal_ymd: str, key: str,
                retries: int = 2, retry_wait: float = 1.0, timeout: float = 30) -> list[dict]:
    """한 유형·구·월의 전체 페이지. 받은 수가 totalCount와 다르면 ApiError(불완전 수집을 확보로 두지 않음)."""
    items: list[dict] = []
    page, total = 1, None
    while True:
        for attempt in range(retries + 1):
            try:
                page_total, page_items = _call(housing_type, lawd_cd, deal_ymd, page, key, timeout)
                break
            except ApiError as exc:
                if attempt == retries or str(exc).startswith(("resultCode 30", "resultCode 20", "resultCode 22")):
                    raise
                time.sleep(retry_wait * (attempt + 1))
        if total is None:
            total = page_total
        elif page_total != total:
            raise ApiError(f"수집 중 totalCount 변경 {total}->{page_total}")
        items.extend(page_items)
        if not page_items or len(items) >= total:
            break
        page += 1
        time.sleep(0.05)
    if len(items) != total:
        raise ApiError(f"받은 건수 {len(items)} != totalCount {total}")
    return items


def _to_int_manwon(value: str) -> int | None:
    text = (value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(round(number)) if number >= 0 else None


def _to_float(value: str) -> float | None:
    try:
        return float((value or "").replace(",", "").strip())
    except ValueError:
        return None


def normalize_item(item: dict, housing_type: str, lawd_cd: str) -> tuple[dict | None, str | None]:
    """API item 1건 -> 정규화 행. 쓸 수 없으면 (None, 사유)."""
    if item.get("sggCd") and item["sggCd"] != lawd_cd:
        return None, "sggCd 불일치"
    deposit = _to_int_manwon(item.get("deposit", ""))
    monthly = _to_int_manwon(item.get("monthlyRent", ""))
    if deposit is None or monthly is None:
        return None, "금액 해석 불가"
    try:
        year, month, day = int(item["dealYear"]), int(item["dealMonth"]), int(item.get("dealDay") or 0)
    except (KeyError, ValueError):
        return None, "계약일 해석 불가"
    region_id, region_name = DISTRICTS[lawd_cd]
    info = HOUSING_TYPES[housing_type]
    name = item.get(info["name_field"], "") if info["name_field"] else ""
    complex_key = item.get(info["complex_field"]) if info["complex_field"] else ""
    if not complex_key and name:
        complex_key = f"{item.get('umdNm', '')}|{name}|{item.get('jibun', '')}"
    building_year = _to_float(item.get("buildYear", ""))
    return {
        "region_id": region_id,
        "region_name": region_name,
        "sgg_cd": lawd_cd,
        "legal_dong": item.get("umdNm", ""),
        "contract_ym": f"{year:04d}{month:02d}",
        "contract_date": f"{year:04d}-{month:02d}-{day:02d}" if day else "",
        "housing_type": housing_type,
        "housing_subtype": item.get("houseType", "") or info["label"],
        "rent_type": "jeonse" if monthly == 0 else "monthly",
        "deposit_manwon": deposit,
        "monthly_rent_manwon": monthly,
        "exclusive_area_m2": _to_float(item.get("excluUseAr", "")) if housing_type != "detached_multi" else None,
        "total_floor_area_m2": _to_float(item.get("totalFloorAr", "")) if housing_type == "detached_multi" else None,
        "building_year": int(building_year) if building_year else None,
        "complex_key": complex_key or "",
        "contract_type_raw": item.get("contractType", ""),
        "renewal_right_used": item.get("useRRRight", ""),
        "source": source_label(housing_type),
    }, None


def month_range(end_ym: str, months: int) -> list[str]:
    """end_ym을 포함해 과거로 months개월(오래된 순)."""
    year, month = int(end_ym[:4]), int(end_ym[4:])
    out = []
    for _ in range(months):
        out.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(out)


def collect(key: str, months: list[str], housing_types: list[str], **fetch_kwargs) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    manifest = {"collected_at": datetime.now().isoformat(timespec="seconds"), "months": months,
                "housing_types": housing_types, "cells": [], "dropped": Counter(),
                "contract_type_values": {}, "sources": {t: source_label(t) for t in housing_types}}
    for housing_type in housing_types:
        contract_types: Counter = Counter()
        for lawd_cd, (region_id, region_name) in DISTRICTS.items():
            for ym in months:
                cell = {"housing_type": housing_type, "region_id": region_id, "contract_ym": ym}
                try:
                    items = fetch_month(housing_type, lawd_cd, ym, key, **fetch_kwargs)
                except ApiError as exc:
                    cell.update(status="미확보", error=str(exc))
                    manifest["cells"].append(cell)
                    print(f"  [미확보] {HOUSING_TYPES[housing_type]['label']} {region_name} {ym}: {exc}")
                    if str(exc).startswith(("resultCode 30", "resultCode 20")):
                        raise  # 키·승인 문제는 나머지도 전부 실패하므로 즉시 중단
                    continue
                kept = 0
                for item in items:
                    contract_types[item.get("contractType", "")] += 1
                    row, reason = normalize_item(item, housing_type, lawd_cd)
                    if row is None:
                        manifest["dropped"][reason] += 1
                        continue
                    rows.append(row)
                    kept += 1
                cell.update(status="확보", received=len(items), kept=kept)
                manifest["cells"].append(cell)
                time.sleep(0.05)
        manifest["contract_type_values"][housing_type] = dict(contract_types)
    manifest["dropped"] = dict(manifest["dropped"])
    return rows, manifest


def _summary(rows: list[dict], manifest: dict) -> str:
    count = Counter((r["housing_type"], r["region_name"], r["rent_type"]) for r in rows)
    lines = [f"기간 {manifest['months'][0]}~{manifest['months'][-1]}, 정규화 {len(rows)}건, 제외 {manifest['dropped']}"]
    for t in manifest["housing_types"]:
        parts = [f"{name} 전세 {count[(t, name, 'jeonse')]}/월세 {count[(t, name, 'monthly')]}"
                 for _, name in DISTRICTS.values()]
        lines.append(f"  {HOUSING_TYPES[t]['label']}: " + ", ".join(parts))
        lines.append(f"    contractType 값 분포: {manifest['contract_type_values'].get(t)}")
    missing = [c for c in manifest["cells"] if c["status"] != "확보"]
    lines.append(f"미확보 칸 {len(missing)}개")
    return "\n".join(lines)


def cmd_inspect(args) -> None:
    key = _service_key()
    for housing_type in args.types:
        try:
            total, items = parse_response(requests.get(
                f"{API_ROOT}/{HOUSING_TYPES[housing_type]['operation']}",
                params={"serviceKey": key, "LAWD_CD": "48123", "DEAL_YMD": args.month, "pageNo": 1, "numOfRows": 5},
                timeout=30).content)
        except (ApiError, requests.RequestException) as exc:
            print(f"{HOUSING_TYPES[housing_type]['label']}: 실패 - {exc if isinstance(exc, ApiError) else type(exc).__name__}")
            continue
        fields = sorted({k for it in items for k in it})
        print(f"{HOUSING_TYPES[housing_type]['label']}: 성산구 {args.month} totalCount={total} 필드={fields}")
        for it in items[:3]:
            print("   ", {k: it.get(k) for k in ("contractType", "deposit", "monthlyRent", "excluUseAr", "totalFloorAr", "houseType")})


def cmd_collect(args) -> None:
    key = _service_key()
    months = month_range(args.end, args.months)
    rows, manifest = collect(key, months, args.types)
    manifest["end_ym"] = args.end
    print(_summary(rows, manifest))
    if not args.save:
        print("(dry-run: --save 를 붙이면 data/housing/ 에 저장)")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, TRANSACTIONS_CSV), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with open(os.path.join(OUT_DIR, MANIFEST_JSON), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"저장: data/housing/{TRANSACTIONS_CSV}, data/housing/{MANIFEST_JSON}")


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    types_arg = {"nargs": "+", "choices": list(HOUSING_TYPES), "default": list(HOUSING_TYPES)}
    p = sub.add_parser("inspect")
    p.add_argument("--month", default=datetime.now().strftime("%Y%m"))
    p.add_argument("--types", **types_arg)
    p = sub.add_parser("collect")
    p.add_argument("--end", required=True, help="마지막 계약년월 YYYYMM(포함)")
    p.add_argument("--months", type=int, default=14, help="수집 개월 수(기본 14 = 12개월 + 신고 지연 제외용 2개월)")
    p.add_argument("--types", **types_arg)
    p.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    {"inspect": cmd_inspect, "collect": cmd_collect}[args.cmd](args)


if __name__ == "__main__":
    main()
