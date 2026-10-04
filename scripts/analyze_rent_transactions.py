# 경남 22개 지역 임대차 거래 스냅샷 분석 (같은 유형끼리) (G4-A, 네트워크·앱 미사용)
"""
scripts/collect_rent_transactions.py 가 저장한 data/housing/changwon_rent_transactions.csv 만 읽어
주택유형 × 계약유형 × 구별 분포를 계산하고, 구 비교에 쓸 수 있는 기간을 판정한다. 점수는 만들지 않는다.

[원칙]
    - 주택유형끼리, 전세·월세끼리 합치지 않는다. 전세는 보증금, 월세는 월세 금액과 보증금을 각각 따로 본다.
    - 전세·월세를 하나의 금액으로 환산하지 않는다(전환율 가정 없음).
    - 통계는 표본 수(n)와 기간을 항상 같이 남긴다. MIN_SAMPLE_SIZE는 확정값이 아니라 설정값(--min-sample).
    - 분위수는 선형보간(정렬 후 (n-1)·q 위치) - numpy 기본값과 같은 결정적 방식.

[비교하는 기간]  기준 끝월 = manifest의 end_ym
    6개월 / 12개월 각각 × 신고 지연 제외(최근 LAG_MONTHS개월 빼고 그 앞 기간) 적용 여부 = 4가지

[대표성 점검]
    - 저가 월세: 월세 30만원 미만, 그리고 그중 보증금 6천만원 이하(주택임대차 신고 의무 기준 미만) 거래 비중
    - 아파트 vs 비아파트 분포 차이(창원시 전체 중앙값·P25·P75)
    - 구×유형×계약유형 칸별 표본 부족
    - 평균 순위 vs 중앙값 순위 변화(낮은 금액 = 1위)
    - 극단값: P75 + 3·IQR 초과 / P25 - 3·IQR 미만 건수(Tukey far-out)

    python scripts/analyze_rent_transactions.py                    # 결과를 화면에 출력
    python scripts/analyze_rent_transactions.py --min-sample 20 --write   # data/housing/rent_analysis.md, .json 저장
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOUSING_DIR = os.path.join(ROOT, "data", "housing")
sys.path.insert(0, ROOT)
from scripts.collect_rent_transactions import (DISTRICTS, HOUSING_TYPES, MANIFEST_JSON, REGION_TYPE_BY_NAME,
                                               TRANSACTIONS_CSV, month_range)  # noqa: E402

DEFAULT_MIN_SAMPLE_SIZE = 30      # 확정값 아님 - 실제 표본 수를 본 뒤 결정
LAG_MONTHS = 2                    # 신고(계약 후 30일 이내) 지연을 고려해 빼는 최근 개월 수
WINDOWS = ((6, False), (6, True), (12, False), (12, True))
REPORT_THRESHOLD_MONTHLY_RENT = 30    # 만원 - 주택임대차 신고 대상: 보증금 6천만원 초과 또는 월차임 30만원 초과
REPORT_THRESHOLD_DEPOSIT = 6000       # 만원
FAR_OUT_IQR = 3.0

REGION_ORDER = [name for _, name in DISTRICTS.values()]
# 같은 유형끼리 비교한다(점수 비교와 같은 원칙): 그룹마다 표본 충분성·순위를 따로 본다.
REGION_GROUPS: dict[str, list[str]] = {
    label: [name for name in REGION_ORDER if REGION_TYPE_BY_NAME[name] == region_type]
    for label, region_type in (("창원시 5개 구", "구"), ("경남 시 지역", "시"), ("경남 군 지역", "군"))
}
RENT_TYPE_LABELS = {"jeonse": "전세", "monthly": "월세"}
METRICS = (  # (계약유형, 금액 열, 표시 이름)
    ("jeonse", "deposit_manwon", "전세 보증금"),
    ("monthly", "monthly_rent_manwon", "월세 금액"),
    ("monthly", "deposit_manwon", "월세 보증금"),
)


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["deposit_manwon"] = int(r["deposit_manwon"])
        r["monthly_rent_manwon"] = int(r["monthly_rent_manwon"])
        r["exclusive_area_m2"] = float(r["exclusive_area_m2"]) if r.get("exclusive_area_m2") else None
    return rows


def quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("빈 값")
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def describe(values: list[float]) -> dict:
    """n·평균·중앙값·P25·P75·최소·최대·극단값 건수. 표본이 없으면 n=0만."""
    if not values:
        return {"n": 0}
    v = sorted(values)
    p25, p75 = quantile(v, 0.25), quantile(v, 0.75)
    iqr = p75 - p25
    return {
        "n": len(v), "mean": sum(v) / len(v), "median": quantile(v, 0.5), "p25": p25, "p75": p75,
        "min": v[0], "max": v[-1],
        "extreme_high": sum(1 for x in v if x > p75 + FAR_OUT_IQR * iqr),
        "extreme_low": sum(1 for x in v if x < p25 - FAR_OUT_IQR * iqr),
    }


def window_months(end_ym: str, length: int, exclude_lag: bool) -> list[str]:
    months = month_range(end_ym, length + (LAG_MONTHS if exclude_lag else 0))
    return months[:length]


def window_label(length: int, exclude_lag: bool) -> str:
    return f"{length}개월" + (f"(최근 {LAG_MONTHS}개월 제외)" if exclude_lag else "(제외 없음)")


def group_stats(rows: list[dict], months: list[str], region_names: list[str] | None = None) -> dict:
    """{(housing_type, metric_label): {지역 이름: describe()}} - region_names(기본 22개 전체) 순서로."""
    names = region_names or REGION_ORDER
    month_set = set(months)
    buckets: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["contract_ym"] not in month_set:
            continue
        for rent_type, column, label in METRICS:
            if r["rent_type"] == rent_type:
                buckets[(r["housing_type"], label)][r["region_name"]].append(r[column])
    return {key: {name: describe(by_region.get(name, [])) for name in names}
            for key, by_region in buckets.items()}


def rank(values: dict[str, float]) -> dict[str, int]:
    """낮은 금액 = 1위. 동률은 같은 순위."""
    return {k: 1 + sum(1 for x in values.values() if x < v) for k, v in values.items()}


def mean_vs_median_rank(stats_by_region: dict[str, dict]) -> dict | None:
    """비교 지역 모두 표본이 있을 때만 평균 순위와 중앙값 순위를 비교."""
    if any(s["n"] == 0 for s in stats_by_region.values()):
        return None
    by_mean = rank({k: s["mean"] for k, s in stats_by_region.items()})
    by_median = rank({k: s["median"] for k, s in stats_by_region.items()})
    return {"mean_rank": by_mean, "median_rank": by_median, "changed": by_mean != by_median}


def window_adequacy(stats: dict, min_sample: int) -> dict:
    """{(housing_type, metric): {"min_n", "short_regions", "comparable"}}"""
    out = {}
    for key, by_region in stats.items():
        short = [name for name, s in by_region.items() if s["n"] < min_sample]
        out[key] = {"min_n": min(s["n"] for s in by_region.values()), "short_regions": short,
                    "comparable": not short}
    return out


def low_price_monthly(rows: list[dict], months: list[str]) -> dict:
    """주택유형별 월세 거래 중 저가(월세 30만원 미만)와 신고 기준 미만(월세 ≤30만원·보증금 ≤6천만원) 비중."""
    month_set = set(months)
    out = {}
    for housing_type in HOUSING_TYPES:
        monthly = [r for r in rows if r["housing_type"] == housing_type and r["rent_type"] == "monthly"
                   and r["contract_ym"] in month_set]
        n = len(monthly)
        below_30 = sum(1 for r in monthly if r["monthly_rent_manwon"] < REPORT_THRESHOLD_MONTHLY_RENT)
        below_report = sum(1 for r in monthly if r["monthly_rent_manwon"] <= REPORT_THRESHOLD_MONTHLY_RENT
                           and r["deposit_manwon"] <= REPORT_THRESHOLD_DEPOSIT)
        out[housing_type] = {"n_monthly": n, "rent_under_30": below_30, "below_report_threshold": below_report,
                             "share_under_30": below_30 / n if n else None,
                             "share_below_report_threshold": below_report / n if n else None}
    return out


def citywide_by_type(rows: list[dict], months: list[str]) -> dict:
    """비교 그룹 전체(합) 주택유형별 분포 - 아파트 vs 비아파트 비교용(지역 비교에는 쓰지 않음)."""
    month_set = set(months)
    out = {}
    for housing_type in HOUSING_TYPES:
        for rent_type, column, label in METRICS:
            values = [r[column] for r in rows if r["housing_type"] == housing_type and r["rent_type"] == rent_type
                      and r["contract_ym"] in month_set]
            out[(housing_type, label)] = describe(values)
    return out


def analyze(rows: list[dict], end_ym: str, min_sample: int, region_names: list[str] | None = None) -> dict:
    """region_names(같은 유형 그룹)만 비교한다. 생략하면 22개 전체."""
    if region_names:
        rows = [r for r in rows if r["region_name"] in set(region_names)]
    windows = {}
    for length, exclude_lag in WINDOWS:
        months = window_months(end_ym, length, exclude_lag)
        stats = group_stats(rows, months, region_names)
        windows[window_label(length, exclude_lag)] = {
            "months": months, "stats": stats, "adequacy": window_adequacy(stats, min_sample),
            "rank_change": {key: mean_vs_median_rank(by_region) for key, by_region in stats.items()},
        }
    base = window_months(end_ym, 12, True)
    return {"end_ym": end_ym, "min_sample": min_sample, "windows": windows,
            "low_price_monthly": low_price_monthly(rows, base), "citywide": citywide_by_type(rows, base),
            "base_window": window_label(12, True)}


def recommend_window(result: dict) -> dict:
    """주택유형·지표별로 비교 지역 모두 n ≥ min_sample 인 가장 짧은 기간(지연 제외 기간 우선)."""
    preference = [window_label(6, True), window_label(12, True), window_label(6, False), window_label(12, False)]
    keys = sorted({k for w in result["windows"].values() for k in w["stats"]})
    out = {}
    for key in keys:
        chosen = next((label for label in preference
                       if result["windows"][label]["adequacy"].get(key, {}).get("comparable")), None)
        out[key] = chosen
    return out


def _fmt(x) -> str:
    return "-" if x is None else f"{x:,.0f}"


def to_markdown(result: dict, title: str = "경남 22개 지역") -> str:
    lines = [f"# {title} 임대차 실거래 분석 (G4-A)", "",
             f"- 기준 끝월: {result['end_ym']} · MIN_SAMPLE_SIZE(설정값, 미확정): {result['min_sample']} · 금액 단위: 만원",
             "- 주택유형·계약유형을 합치지 않았고 전세·월세를 환산하지 않았다. 점수 계산에는 쓰지 않는다.", ""]
    rec = recommend_window(result)
    lines += ["## 1. 비교 가능한 기간 (비교 지역 모두 n ≥ MIN_SAMPLE_SIZE)", "",
              "| 주택유형 | 지표 | " + " | ".join(result["windows"]) + " | 권장 |",
              "|---|---|" + "---|" * len(result["windows"]) + "---|"]
    for key in sorted(rec):
        ht, label = key
        cells = []
        for w in result["windows"].values():
            a = w["adequacy"].get(key)
            cells.append("-" if a is None else f"최소 n={a['min_n']}" + (" ✅" if a["comparable"] else f" ❌({', '.join(a['short_regions'])})"))
        lines.append(f"| {HOUSING_TYPES[ht]['label']} | {label} | " + " | ".join(cells) + f" | {rec[key] or '비교 불가'} |")
    base = result["windows"][result["base_window"]]
    lines += ["", f"## 2. 지역별 분포 — {result['base_window']} ({base['months'][0]}~{base['months'][-1]})", ""]
    for key in sorted(base["stats"]):
        ht, label = key
        lines += [f"### {HOUSING_TYPES[ht]['label']} · {label}", "",
                  "| 지역 | n | 평균 | 중앙값 | P25 | P75 | 최소 | 최대 | 극단값(고/저) |", "|---|---|---|---|---|---|---|---|---|"]
        for name, s in base["stats"][key].items():
            if s["n"] == 0:
                lines.append(f"| {name} | 0 | - | - | - | - | - | - | - |")
            else:
                lines.append(f"| {name} | {s['n']} | {_fmt(s['mean'])} | {_fmt(s['median'])} | {_fmt(s['p25'])} | "
                             f"{_fmt(s['p75'])} | {_fmt(s['min'])} | {_fmt(s['max'])} | {s['extreme_high']}/{s['extreme_low']} |")
        rc = base["rank_change"].get(key)
        if rc is None:
            lines.append("\n평균·중앙값 순위 비교: 표본 없는 지역이 있어 생략")
        else:
            order = lambda r: " < ".join(sorted(r, key=r.get))  # noqa: E731
            lines.append(f"\n평균 순위(저렴한 순): {order(rc['mean_rank'])} / 중앙값 순위: {order(rc['median_rank'])}"
                         f" → {'**순위 바뀜**' if rc['changed'] else '같음'}")
        lines.append("")
    lines += ["## 3. 저가 월세 대표성", "", "| 주택유형 | 월세 거래 n | 월세 30만원 미만 | 신고 기준 미만(월세≤30·보증금≤6,000) |",
              "|---|---|---|---|"]
    for ht, s in result["low_price_monthly"].items():
        pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"  # noqa: E731
        lines.append(f"| {HOUSING_TYPES[ht]['label']} | {s['n_monthly']} | {s['rent_under_30']} ({pct(s['share_under_30'])}) | "
                     f"{s['below_report_threshold']} ({pct(s['share_below_report_threshold'])}) |")
    lines += ["", "## 4. 아파트 vs 비아파트 (이 그룹 전체, 지역 비교용 아님)", "",
              "| 주택유형 | 지표 | n | 중앙값 | P25 | P75 |", "|---|---|---|---|---|---|"]
    for (ht, label), s in result["citywide"].items():
        if s["n"]:
            lines.append(f"| {HOUSING_TYPES[ht]['label']} | {label} | {s['n']} | {_fmt(s['median'])} | {_fmt(s['p25'])} | {_fmt(s['p75'])} |")
        else:
            lines.append(f"| {HOUSING_TYPES[ht]['label']} | {label} | 0 | - | - | - |")
    return "\n".join(lines) + "\n"


def _jsonable(result: dict) -> dict:
    def conv(obj):
        if isinstance(obj, dict):
            return {(" / ".join(k) if isinstance(k, tuple) else k): conv(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [conv(x) for x in obj]
        return obj
    return conv(result)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-sample", type=int, default=DEFAULT_MIN_SAMPLE_SIZE)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    tx_path, manifest_path = os.path.join(HOUSING_DIR, TRANSACTIONS_CSV), os.path.join(HOUSING_DIR, MANIFEST_JSON)
    if not os.path.exists(tx_path):
        sys.exit("data/housing/ 에 수집 결과가 없습니다. 먼저 collect_rent_transactions.py collect --save 를 실행하세요.")
    with open(manifest_path, encoding="utf-8") as f:
        end_ym = json.load(f)["end_ym"]
    rows = load_rows(tx_path)
    results = {label: analyze(rows, end_ym, args.min_sample, names) for label, names in REGION_GROUPS.items()}
    report = "\n".join(to_markdown(result, label) for label, result in results.items())
    print(report)
    if args.write:
        with open(os.path.join(HOUSING_DIR, "rent_analysis.md"), "w", encoding="utf-8") as f:
            f.write(report)
        with open(os.path.join(HOUSING_DIR, "rent_analysis.json"), "w", encoding="utf-8") as f:
            json.dump({label: _jsonable(r) for label, r in results.items()}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
