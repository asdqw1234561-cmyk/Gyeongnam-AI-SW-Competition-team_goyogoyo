# 생활편의 데이터
"""
창원시 생활편의시설(현재: 편의점) 조회 서비스.

데이터는 scripts/collect_convenience_stores.py 로 수집한 CSV를 읽는다.
    - data/convenience/changwon_convenience_stores.csv : 업소 단위 목록
    - data/convenience/changwon_convenience_counts.csv : 구별 집계(대형마트는 "미확보")

CSV가 아직 없으면(수집 전) 예외를 내지 않고 모든 구를 data_status="미확보"로 돌려준다.
수치를 임의로 만들지 않는다.

반환 형식은 services/region_data.py 의 지표 형식(value / source / reference_date /
data_status / note)과 맞췄으므로 추천·점수 계산 쪽에서 그대로 섞어 쓸 수 있다.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(os.path.dirname(_THIS_DIR), "data", "convenience")

STORES_CSV = os.path.join(_DATA_DIR, "changwon_convenience_stores.csv")
COUNTS_CSV = os.path.join(_DATA_DIR, "changwon_convenience_counts.csv")

NOT_SECURED = "미확보"

DISTRICTS = {
    "CW-UICHANG": "의창구",
    "CW-SEONGSAN": "성산구",
    "CW-MASANHAPPO": "마산합포구",
    "CW-MASANHOEWON": "마산회원구",
    "CW-JINHAE": "진해구",
}

# facility_type -> region_indicators.csv 의 indicator_code
INDICATOR_CODES = {"편의점": "convenience_store_count", "대형마트": "mart_count"}


def _read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")


def _resolve_region_id(district: str) -> Optional[str]:
    """'CW-JINHAE', '진해구', '창원시 진해구', '진해' 모두 허용."""
    if not district:
        return None
    text = district.strip()
    if text in DISTRICTS:
        return text
    for region_id, name in DISTRICTS.items():
        if name in text or name.rstrip("구") == text:
            return region_id
    return None


def load_stores() -> pd.DataFrame:
    """수집된 편의점 업소 목록 전체(DataFrame). 수집 전이면 빈 DataFrame."""
    return _read_csv(STORES_CSV)


def is_data_available() -> bool:
    return os.path.exists(STORES_CSV) and os.path.exists(COUNTS_CSV)


def get_stores_by_district(district: str, keyword: str = "", limit: Optional[int] = None) -> list[dict]:
    """
    구 하나의 편의점 목록을 반환한다.

    Args:
        district: "의창구", "창원시 성산구", "CW-JINHAE" 등
        keyword: 상호명/주소에 포함될 문자열(선택). 예: "GS25", "상남동"
        limit: 최대 반환 개수(선택)
    """
    region_id = _resolve_region_id(district)
    df = load_stores()
    if region_id is None or df.empty:
        return []
    df = df[df["region_id"] == region_id]
    if keyword:
        mask = (
            df["facility_name"].str.contains(keyword, case=False, regex=False)
            | df["road_address"].str.contains(keyword, regex=False)
            | df["jibun_address"].str.contains(keyword, regex=False)
            | df["adong_name"].str.contains(keyword, regex=False)
        )
        df = df[mask]
    if limit:
        df = df.head(limit)
    return df.to_dict(orient="records")


def get_district_counts(facility_type: str = "편의점") -> list[dict]:
    """
    창원시 5개 구의 시설 수를 항상 5개 행으로 반환한다.

    반환 예:
        [{"region_id": "CW-UICHANG", "district": "의창구", "facility_type": "편의점",
          "indicator_code": "convenience_store_count", "value": 123 또는 None,
          "unit": "개", "source": ..., "reference_date": "202506",
          "collected_date": "2026-10-01", "data_status": "확보" | "미확보", "note": ...}, ...]
    """
    counts = _read_csv(COUNTS_CSV)
    results = []
    for region_id, district in DISTRICTS.items():
        row = None
        if not counts.empty:
            matched = counts[(counts["region_id"] == region_id)
                             & (counts["facility_type"] == facility_type)]
            if not matched.empty:
                row = matched.iloc[0]
        status = row["data_status"] if row is not None else NOT_SECURED
        value = int(row["count"]) if row is not None and status == "확보" and row["count"] else None
        results.append({
            "region_id": region_id,
            "district": district,
            "facility_type": facility_type,
            "indicator_code": INDICATOR_CODES.get(facility_type),
            "value": value,
            "unit": "개",
            "source": (row["source"] or None) if row is not None else None,
            "reference_date": (row["reference_ym"] or None) if row is not None else None,
            "collected_date": (row["collected_date"] or None) if row is not None else None,
            "data_status": status,
            "note": (row["note"] if row is not None else "수집 전 (scripts/collect_convenience_stores.py 실행 필요)"),
        })
    return results


def get_district_count(district: str, facility_type: str = "편의점") -> Optional[dict]:
    """구 하나의 시설 수 지표. 알 수 없는 구면 None."""
    region_id = _resolve_region_id(district)
    for row in get_district_counts(facility_type):
        if row["region_id"] == region_id:
            return row
    return None


if __name__ == "__main__":
    # 간단 확인용: python -m services.convenience
    for r in get_district_counts("편의점") + get_district_counts("대형마트"):
        shown = r["value"] if r["data_status"] == "확보" else NOT_SECURED
        print(f"{r['district']:<6} {r['facility_type']:<4} {shown}  (기준 {r['reference_date']})")
