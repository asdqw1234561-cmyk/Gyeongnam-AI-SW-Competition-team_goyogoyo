# 정착 후보군 구성 + Critic 점검
"""
점수 계산이 끝난 결과(score_result)를 받아 "가장 높은 구 하나"가 아니라 성격이 다른
정착 후보(최적·균형·대안)를 고르고, 그 후보 집합이 믿을 만한지 Critic이 점검한다.

[원칙]
- 새 점수 계산식을 만들지 않는다. analysis/scoring.py가 이미 계산한 축별 정규화 점수
  (component_scores[*]["normalized_score"])와 종합점수(total_score)만 읽는다. 순위와
  top_candidates도 바꾸지 않는다.
- LLM을 호출하지 않는다. 같은 입력이면 항상 같은 후보·같은 점검 결과가 나온다.
- 데이터가 없는 평가축(주거비·교육·직장 접근성)은 채우지 않고 "미확보"로 남긴다. 그 축이
  있어야 정의되는 후보(가성비 = 주거비 대비 생활여건)는 "산출 불가"로 표시한다.

[후보 역할]
    최적  : 승인된 가중치 종합점수 1위 (scoring 결과 그대로)
    균형  : 평가에 쓴 축 중 "가장 약한 축의 점수"가 가장 높은 구 (약점이 가장 작은 구, maximin)
    대안  : 최적 후보의 가장 약한 축에서 최적 후보보다 앞서는 구 중 그 축 점수가 가장 높은 구
    가성비: 주거비 데이터 미확보 -> 산출 불가

[Critic 점검]
    close_gap        1·2위 종합점수 차이가 CLOSE_GAP_POINTS 미만이면 순위가 불안정하다고 경고
    single_axis      최적 후보 종합점수의 SINGLE_AXIS_SHARE 이상이 한 축에서 나오면 경고
    revised          1차 대안 후보가 다른 구에 모든 축에서 뒤지면(지배됨) 지배되지 않는 구로 바꾸고 기록
    dominated        후보가 다른 구보다 모든 평가축에서 낮거나 같으면 경고
    concentration    산출된 후보 역할이 모두 한 구로 몰리면 경고
    granularity      후보 단위가 구(5개)라 같은 생활권 안의 쏠림은 판단할 수 없다는 한계(항상)
    coverage         6개 평가축 중 실제로 쓴 축 수, 미확보 축, 입력했지만 반영 못 한 조건
    ties             동점 구가 있으면 안내
"""

from __future__ import annotations

from analysis.scoring import CONDITION_TO_INDICATOR_CODE, VALID_SCORABLE_INDICATOR_CODES

# 이주자가 정착지를 볼 때의 독립 평가축. indicator_code가 None이면 대응 데이터 미확보.
EVALUATION_AXES: tuple[tuple[str, str | None], ...] = (
    ("주거비", None),
    ("교통", CONDITION_TO_INDICATOR_CODE["교통"]),
    ("의료", CONDITION_TO_INDICATOR_CODE["의료"]),
    ("교육", None),
    ("생활편의", CONDITION_TO_INDICATOR_CODE["생활편의(마트/편의점)"]),
    ("직장 접근성", None),
)
AXIS_BY_CODE: dict[str, str] = {code: axis for axis, code in EVALUATION_AXES if code}

AXIS_STATUS_USED = "평가 사용"
AXIS_STATUS_UNUSED = "확보 · 이번 평가 미사용"
AXIS_STATUS_MISSING = "미확보"

CLOSE_GAP_POINTS = 5.0       # 1·2위 종합점수 차이가 이보다 작으면 순위 불안정 경고
SINGLE_AXIS_SHARE = 0.7      # 최적 후보 종합점수 중 한 축 기여 비율이 이 이상이면 경고
STRENGTH_MAX_RANK = 2        # 축별 순위 1~2위 = 강점
WEAKNESS_MIN_RANK = 4        # 축별 순위 4~5위 = 약점

ROLE_LABELS = {"best": "최적", "balanced": "균형", "value": "가성비", "alternative": "대안"}
VALUE_UNAVAILABLE_REASON = "주거비(월세·전세) 데이터가 미확보라 비용 대비 생활여건을 계산할 수 없습니다."
GRANULARITY_NOTE = (
    "후보 단위가 창원시 5개 구라서, 같은 구 안의 특정 생활권(동네)에 후보가 몰리는지는 "
    "판단할 수 없습니다(행정동 단위 지표 미확보)."
)


def _axis_ranks(normalized: dict[str, dict[str, float]], codes: list[str]) -> dict[str, dict[str, int]]:
    """{region_id: {code: 5개 구 중 순위(동점은 같은 순위)}}"""
    ranks: dict[str, dict[str, int]] = {rid: {} for rid in normalized}
    for code in codes:
        values = {rid: round(scores[code], 6) for rid, scores in normalized.items()}
        for rid, value in values.items():
            ranks[rid][code] = 1 + sum(1 for v in values.values() if v > value)
    return ranks


def _dominators(rid: str, normalized: dict[str, dict[str, float]], codes: list[str]) -> list[str]:
    """rid보다 모든 축에서 높거나 같고, 한 축 이상에서 높은 구 목록."""
    mine = normalized[rid]
    result = []
    for other, theirs in normalized.items():
        if other == rid:
            continue
        if all(theirs[c] >= mine[c] for c in codes) and any(theirs[c] > mine[c] for c in codes):
            result.append(other)
    return result


def _role_entry(role: str, row: dict, codes: list[str], ranks: dict[str, dict[str, int]], reason: str) -> dict:
    rid = row["region_id"]
    axis_profile = [
        {
            "axis": AXIS_BY_CODE[code],
            "indicator_code": code,
            "raw_value": row["component_scores"][code]["raw_value"],
            "normalized_score": row["component_scores"][code]["normalized_score"],
            "axis_rank": ranks[rid][code],
        }
        for code in codes
    ]
    return {
        "role": role,
        "role_label": ROLE_LABELS[role],
        "status": "ok",
        "region_id": rid,
        "region_name": row["region_name"],
        "rank": row["rank"],
        "total_score": row["total_score"],
        "reason": reason,
        "axis_profile": axis_profile,
        "strengths": [p["axis"] for p in axis_profile if p["axis_rank"] <= STRENGTH_MAX_RANK],
        "weaknesses": [p["axis"] for p in axis_profile if p["axis_rank"] >= WEAKNESS_MIN_RANK],
    }


def _unavailable(role: str, reason: str) -> dict:
    return {"role": role, "role_label": ROLE_LABELS[role], "status": "unavailable", "reason": reason}


def _axes_status(used_codes: list[str]) -> list[dict]:
    axes = []
    for axis, code in EVALUATION_AXES:
        if code is None:
            status = AXIS_STATUS_MISSING
        elif code in used_codes:
            status = AXIS_STATUS_USED
        else:
            status = AXIS_STATUS_UNUSED
        axes.append({"axis": axis, "indicator_code": code, "status": status})
    return axes


def build_candidate_set(score_result: dict, unscored_inputs: list[str] | None = None) -> dict:
    """
    Args:
        score_result: analysis.scoring.compute_region_scores*() 반환값.
        unscored_inputs: 사용자가 입력했지만 대응 데이터가 없어 점수에 반영하지 못한 항목
            이름(예: ["주거비 예산", "직장/학교 위치"]). coverage 점검 문구에만 쓴다.

    Returns:
        {"status": "ok" | "unavailable",
         "axes": [{"axis", "indicator_code", "status"}],      # 6개 평가축 확보·사용 상태
         "roles": [{"role", "role_label", "status", "reason", ...}],   # 최적·균형·가성비·대안
         "pareto": [구 이름, ...],          # 어느 구에도 모든 축에서 뒤지지 않는 구(축 2개 이상일 때)
         "critic": {"checks": [{"code", "level": "warning"|"info", "message"}], "warning_count"}}
    """
    used_codes = [
        uc["indicator_code"] for uc in score_result.get("used_conditions", [])
        if uc.get("indicator_code") in VALID_SCORABLE_INDICATOR_CODES
    ]
    axes = _axes_status(used_codes)
    if score_result.get("status") != "ok" or not score_result.get("region_scores") or not used_codes:
        return {"status": "unavailable", "axes": axes, "roles": [], "pareto": [],
                "critic": {"checks": [], "warning_count": 0},
                "message": score_result.get("message") or "비교 가능한 점수 결과가 없습니다."}

    rows = score_result["region_scores"]  # 이미 종합점수 내림차순(동점은 region_id 순)
    row_by_id = {r["region_id"]: r for r in rows}
    normalized = {
        r["region_id"]: {c: r["component_scores"][c]["normalized_score"] for c in used_codes} for r in rows
    }
    ranks = _axis_ranks(normalized, used_codes)
    multi_axis = len(used_codes) >= 2

    # ---- 후보 역할 -------------------------------------------------------
    best = rows[0]
    roles = [_role_entry("best", best, used_codes, ranks, "승인된 가중치 종합점수 1위")]

    balanced_row = None
    if multi_axis:
        balanced_row = max(
            rows,
            key=lambda r: (min(normalized[r["region_id"]].values()), r["total_score"], -rows.index(r)),
        )
        weakest = min(normalized[balanced_row["region_id"]].values())
        reason = f"평가축 중 가장 약한 축도 {weakest:.1f}점으로, 약점이 가장 작은 구"
        if balanced_row["region_id"] == best["region_id"]:
            reason += " (최적 후보와 같은 구)"
        roles.append(_role_entry("balanced", balanced_row, used_codes, ranks, reason))
    else:
        roles.append(_unavailable("balanced", "평가축이 1개라 축 사이의 균형을 따질 수 없습니다."))

    roles.append(_unavailable("value", VALUE_UNAVAILABLE_REASON))

    if multi_axis:
        best_scores = normalized[best["region_id"]]
        weak_code = min(used_codes, key=lambda c: best_scores[c])  # 동점이면 used_codes 순서
        better = [r for r in rows if normalized[r["region_id"]][weak_code] > best_scores[weak_code]]
        taken = {best["region_id"], balanced_row["region_id"] if balanced_row else None}
        pool = [r for r in better if r["region_id"] not in taken] or better

        def _pick(cands: list[dict]) -> dict:
            return max(cands, key=lambda r: (normalized[r["region_id"]][weak_code], r["total_score"], -rows.index(r)))

        if pool:
            alt = _pick(pool)
            # Critic 수정: 1차 대안이 다른 구에 모든 축에서 뒤지면(지배됨) 대안으로 권할 이유가 없으므로,
            # 약한 축에서 앞서면서 지배되지 않는 구로 바꾸고 그 사실을 기록한다.
            revised_from = None
            alt_doms = _dominators(alt["region_id"], normalized, used_codes)
            if alt_doms:
                undominated = [r for r in better if not _dominators(r["region_id"], normalized, used_codes)]
                if undominated:
                    revised_from = (alt, alt_doms)
                    alt = _pick(undominated)
            reason = (
                f"최적 후보({best['region_name']})의 가장 약한 축인 {AXIS_BY_CODE[weak_code]}"
                f"({best_scores[weak_code]:.1f}점)에서 {normalized[alt['region_id']][weak_code]:.1f}점으로 앞서는 구"
            )
            same_as = [r["role_label"] for r in roles if r["status"] == "ok" and r["region_id"] == alt["region_id"]]
            if same_as:
                reason += f" ({'·'.join(same_as)} 후보와 같은 구)"
            entry = _role_entry("alternative", alt, used_codes, ranks, reason)
            if revised_from:
                first, doms = revised_from
                entry["revised_from"] = {
                    "region_id": first["region_id"], "region_name": first["region_name"],
                    "dominated_by": [row_by_id[d]["region_name"] for d in doms],
                }
            roles.append(entry)
        else:
            roles.append(_unavailable(
                "alternative",
                f"최적 후보({best['region_name']})가 가장 약한 축({AXIS_BY_CODE[weak_code]})에서도 "
                "다른 구보다 낮지 않아 보완해 줄 대안이 없습니다.",
            ))
    else:
        roles.append(_unavailable("alternative", "평가축이 1개라 다른 축을 보완하는 대안을 정할 수 없습니다."))

    pareto = (
        [r["region_name"] for r in rows if not _dominators(r["region_id"], normalized, used_codes)]
        if multi_axis else []
    )

    # ---- Critic -----------------------------------------------------------
    checks: list[dict] = []

    if len(rows) >= 2:
        gap = rows[0]["total_score"] - rows[1]["total_score"]
        if gap < CLOSE_GAP_POINTS:
            checks.append({"code": "close_gap", "level": "warning",
                           "facts": {"first": rows[0]["region_name"], "second": rows[1]["region_name"]}, "message": (
                f"1위 {rows[0]['region_name']}와 2위 {rows[1]['region_name']}의 종합점수 차이가 {gap:.1f}점으로 "
                f"{CLOSE_GAP_POINTS:g}점 미만입니다. 가중치를 조금만 바꿔도 순위가 바뀔 수 있습니다.")})

    if multi_axis and best["total_score"] > 0:
        contrib = {c: best["component_scores"][c]["weighted_score"] for c in used_codes}
        top_code = max(contrib, key=contrib.get)
        share = contrib[top_code] / best["total_score"]
        if share >= SINGLE_AXIS_SHARE:
            checks.append({"code": "single_axis", "level": "warning",
                           "facts": {"region": best["region_name"], "axis": AXIS_BY_CODE[top_code]}, "message": (
                f"최적 후보 {best['region_name']}의 종합점수 {best['total_score']:.1f}점 중 {share * 100:.0f}%가 "
                f"{AXIS_BY_CODE[top_code]} 한 축에서 나옵니다. 다른 축이 중요하다면 균형·대안 후보를 함께 보세요.")})

    for r in roles:
        if r.get("revised_from"):
            first = r["revised_from"]
            checks.append({"code": "revised", "level": "info",
                           "facts": {"role": r["role_label"], "from": first["region_name"], "to": r["region_name"],
                                     "dominated_by": first["dominated_by"]}, "message": (
                f"Critic 수정: 처음 고른 {r['role_label']} 후보 {first['region_name']}는 "
                f"{', '.join(first['dominated_by'])}보다 평가에 쓴 모든 축에서 낮거나 같아 "
                f"{r['region_name']}(으)로 바꿨습니다.")})

    if multi_axis:
        role_ids = [r["region_id"] for r in roles if r["status"] == "ok"]
        for rid in dict.fromkeys(role_ids + [r["region_id"] for r in score_result.get("top_candidates", [])]):
            doms = _dominators(rid, normalized, used_codes)
            if doms:
                names = ", ".join(row_by_id[d]["region_name"] for d in doms)
                checks.append({"code": "dominated", "level": "warning",
                               "facts": {"region": row_by_id[rid]["region_name"],
                                         "dominated_by": [row_by_id[d]["region_name"] for d in doms]}, "message": (
                    f"{row_by_id[rid]['region_name']}는 {names}보다 평가에 쓴 모든 축에서 낮거나 같습니다.")})

    ok_roles = [r for r in roles if r["status"] == "ok"]
    if len(ok_roles) >= 2 and len({r["region_id"] for r in ok_roles}) == 1:
        checks.append({"code": "concentration", "level": "warning",
                       "facts": {"region": ok_roles[0]["region_name"], "roles": [r["role_label"] for r in ok_roles]},
                       "message": (
            f"산출된 후보 역할({', '.join(r['role_label'] for r in ok_roles)})이 모두 "
            f"{ok_roles[0]['region_name']} 한 곳으로 몰렸습니다. 서로 다른 성격의 후보를 비교하기 어렵습니다.")})

    checks.append({"code": "granularity", "level": "info", "facts": {}, "message": GRANULARITY_NOTE})

    missing_axes = [a["axis"] for a in axes if a["status"] == AXIS_STATUS_MISSING]
    coverage_msg = (
        f"6개 평가축 중 {len(used_codes)}개({', '.join(AXIS_BY_CODE[c] for c in used_codes)})만으로 비교했습니다. "
        f"미확보 축: {', '.join(missing_axes)} - 이 축들은 추정하지 않고 비워 두었습니다."
    )
    not_reflected = list(unscored_inputs or []) + [
        e["condition"] for e in score_result.get("excluded_conditions", []) if e.get("condition")
    ]
    coverage_facts = {"used_axes": [AXIS_BY_CODE[c] for c in used_codes], "missing_axes": missing_axes,
                      "not_reflected": list(dict.fromkeys(not_reflected))}
    if not_reflected:
        checks.append({"code": "coverage", "level": "warning", "facts": coverage_facts, "message": (
            coverage_msg + f" 입력하셨지만 반영하지 못한 조건: {', '.join(dict.fromkeys(not_reflected))}.")})
    else:
        checks.append({"code": "coverage", "level": "info", "facts": coverage_facts, "message": coverage_msg})

    tied_names = [r["region_name"] for r in rows if r.get("tied")]
    if tied_names:
        checks.append({"code": "ties", "level": "info", "facts": {"regions": tied_names},
                       "message": f"종합점수가 같은 구가 있습니다: {', '.join(tied_names)}."})

    return {
        "status": "ok",
        "axes": axes,
        "roles": roles,
        "pareto": pareto,
        "critic": {"checks": checks, "warning_count": sum(1 for c in checks if c["level"] == "warning")},
    }
