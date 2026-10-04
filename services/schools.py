# 교육시설 수 참고정보 (추천 점수에 쓰지 않음)
"""
경남 비교 지역별 초·중·고등학교 수를 "참고정보"로 읽는다 (G5-B, DEC-19 / OPEN-9 (b)).

- 원본은 scripts/ingest_gyeongnam_schools.py가 data/raw/전국초중등학교위치표준데이터.csv에서
  22개 지역으로 집계해 둔 data/gyeongnam/school_counts.csv다(창원 5개 구 값은 G5-A 검증값과 같다).
  여기서는 그 결과를 읽기만 하고, 화면이 고른 비교 범위의 지역만 돌려준다.
- 이 값은 analysis/scoring.py·analysis/candidates.py에 들어가지 않는다. 추천 점수·후보·Critic·
  피드백 가중치와 무관하며, 교육 평가축은 점수 기준으로 계속 "미확보"다.
- 파일이 없거나 형식이 맞지 않으면 예외 대신 status="미확보"를 돌려준다(추천 기능은 그대로 동작).
"""

from __future__ import annotations

import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHOOL_COUNTS_CSV = PROJECT_ROOT / "data" / "gyeongnam" / "school_counts.csv"

REGION_ORDER = ("CW-UICHANG", "CW-SEONGSAN", "CW-MASANHAPPO", "CW-MASANHOEWON", "CW-JINHAE")  # 기본 범위(창원시 5개 구)
COUNT_FIELDS = (
    "elementary_school_count", "middle_school_count", "high_school_count",
    "school_count", "branch_school_count",
)

SCHOOL_REFERENCE_TITLE = "교육시설 수 참고정보 (추천 점수에 사용하지 않음)"
SCHOOL_REFERENCE_LIMITATIONS: tuple[str, ...] = (
    "학교 수는 학군 수준이나 교육의 질을 뜻하지 않습니다.",
    "학생 수·인구·면적을 보정하지 않은 단순 개수입니다.",
    "통학구역·배정학교를 반영하지 않습니다.",
    "읍·면 지역(예: 의창구 동읍·북면, 마산회원구 내서읍)의 학교도 포함됩니다.",
    "특정 주소에서 실제로 가까운 학교 수나 통학 거리·실제 접근성을 뜻하지 않습니다.",
    "추천 점수·후보 선정에는 쓰지 않으며, 점수 기준의 교육 평가축은 계속 '미확보'입니다.",
)


def _unavailable(reason: str) -> dict:
    return {"status": "미확보", "reason": reason, "rows": [], "source": None, "reference_date": None}


def load_school_reference(path: Path | None = None, region_ids: list[str] | tuple[str, ...] | None = None) -> dict:
    """지역별 학교 수 참고정보(region_ids 순서, 기본은 창원시 5개 구). 실패해도 예외를 던지지 않는다."""
    path = SCHOOL_COUNTS_CSV if path is None else path
    wanted = tuple(region_ids) if region_ids else REGION_ORDER
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            raw_rows = list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return _unavailable(f"교육시설 수 파일을 읽지 못했습니다({type(exc).__name__}).")

    by_region = {row.get("region_id"): row for row in raw_rows}
    if len(by_region) != len(raw_rows) or not set(wanted) <= set(by_region):
        return _unavailable("교육시설 수 파일에 비교 지역이 빠짐없이(중복 없이) 들어 있지 않습니다.")

    rows = []
    for region_id in wanted:
        raw = by_region[region_id]
        try:
            counts = {field: int(raw[field]) for field in COUNT_FIELDS}
        except (KeyError, TypeError, ValueError):
            return _unavailable("교육시설 수 파일의 값 형식이 올바르지 않습니다.")
        level_sum = counts["elementary_school_count"] + counts["middle_school_count"] + counts["high_school_count"]
        if level_sum != counts["school_count"]:
            return _unavailable("교육시설 수 파일의 학교급별 합계가 총 학교 수와 다릅니다.")
        rows.append({"region_id": region_id, "region_name": raw.get("region_name", region_id), **counts})

    sources = {by_region[rid].get("source") for rid in wanted}
    dates = {by_region[rid].get("reference_date") for rid in wanted}
    return {
        "status": "확보",
        "reason": None,
        "rows": rows,
        "source": sources.pop() if len(sources) == 1 else ", ".join(sorted(s for s in sources if s)),
        "reference_date": dates.pop() if len(dates) == 1 else ", ".join(sorted(d for d in dates if d)),
    }
