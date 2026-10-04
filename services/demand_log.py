"""
익명 수요 기록 - 이주 희망자가 어떤 조건을 찾았는지 지자체 화면에서 모아 보기 위한 기록.

[무엇을 남기나]
- 사용자가 결과 화면에서 "익명 통계 제공에 동의"를 체크했을 때만 저장한다(app.py).
- 구조화된 선택값만 남긴다: 날짜(일 단위), 비교 범위, 고른 생활조건, 승인된 가중치,
  점수에 반영하지 못한 조건(데이터 미확보 항목, 주거비 예산 구간, 직장/학교 '구' 단위,
  자가용 여부), 피드백으로 바꾼 방향, 후보로 뽑힌 지역.
- 남기지 않는 것: '추가 요청사항' 등 자유 문장, 지도 좌표, 위치 질문 문장, 이름·연락처.
  사용자를 특정할 수 있는 값이 섞일 수 있기 때문이다.

[어디에]
- 로컬 파일 `data/demand/requests.jsonl`(git 제외, .gitignore). 환경변수 DEMAND_LOG_PATH로 바꿀 수 있다.
- 한 세션(브라우저 탭)은 익명 세션 ID 하나로 묶고, 상태가 바뀔 때마다 한 줄을 덧붙인다.
  읽을 때는 세션마다 마지막 줄만 쓴다(같은 사람이 여러 번 세어지지 않게).

[하지 않는 것]
- 예시·가짜 기록을 만들지 않는다. 기록이 없으면 지자체 화면은 "아직 없음"으로 보여준다.
- 점수·후보를 새로 계산하지 않는다. 이미 계산된 결과에서 값을 옮겨 적기만 한다.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "demand" / "requests.jsonl"
SCHEMA_VERSION = 1

# 최초 입력에서 기록해도 되는 값(전부 선택지에서 고른 값). 자유 문장인 '추가 요청사항'은 넣지 않는다.
RECORDED_INPUT_FIELDS = ("희망지역", "중요 생활조건", "직장/학교 위치", "주거비 예산", "자가용 보유 여부", "원하는 후보 개수")
UNSCORED_INPUT_FIELDS = ("주거비 예산", "직장/학교 위치")


def log_path() -> Path:
    return Path(os.environ.get("DEMAND_LOG_PATH") or DEFAULT_LOG_PATH)


def build_record(
    session_id: str,
    initial_input: dict,
    result: dict,
    candidate_set: dict | None,
    feedback_history: list[dict] | None = None,
    today: date | None = None,
) -> dict:
    """화면에 이미 있는 값만 옮겨 익명 기록 한 건을 만든다(새 계산 없음, 자유 문장·좌표 없음)."""
    inp = initial_input or {}
    roles = [
        {"role": r["role_label"], "region": r["region_name"]}
        for r in (candidate_set or {}).get("roles", [])
        if r.get("status") == "ok"
    ]
    directions = []
    for entry in feedback_history or []:
        if not entry.get("approved"):
            continue
        for target in entry.get("targets") or []:
            directions.append({"axis": target.get("axis"), "direction": target.get("direction")})
    return {
        "v": SCHEMA_VERSION,
        "session": session_id,
        "date": (today or date.today()).isoformat(),
        "scope": inp.get("희망지역") or "창원시 5개 구",
        "conditions": list(inp.get("중요 생활조건") or []),
        "unscored_conditions": [e["condition"] for e in result.get("excluded_conditions", []) if e.get("condition")],
        "unscored_inputs": {k: inp[k] for k in UNSCORED_INPUT_FIELDS if inp.get(k)},
        "has_car": inp.get("자가용 보유 여부") or None,
        "weights": {uc["indicator_name"]: round(uc["weight"] * 100, 1) for uc in result.get("used_conditions", [])},
        "feedback_directions": directions,
        "feedback_count": sum(1 for e in feedback_history or [] if e.get("approved")),
        "candidates": roles,
    }


def append_record(record: dict, path: Path | None = None) -> Path:
    target = Path(path or log_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target


def load_records(path: Path | None = None) -> list[dict]:
    """세션마다 마지막 기록만 돌려준다. 깨진 줄은 건너뛴다. 파일이 없으면 []."""
    target = Path(path or log_path())
    if not target.exists():
        return []
    latest: dict[str, dict] = {}
    with target.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("session"):
                latest[record["session"]] = record
    return list(latest.values())


def _share(counter: Counter, total: int) -> list[dict]:
    return [{"항목": k, "건수": n, "비율(%)": round(n / total * 100, 1)} for k, n in counter.most_common()]


def summarize(records: list[dict]) -> dict:
    """지자체 화면용 집계. 모든 수치는 기록된 세션 수 기준이다."""
    total = len(records)
    if total == 0:
        return {"total": 0}
    scopes = Counter(r.get("scope") for r in records)
    conditions = Counter(c for r in records for c in r.get("conditions") or [])
    no_condition = sum(1 for r in records if not r.get("conditions"))
    unmet = Counter(c for r in records for c in r.get("unscored_conditions") or [])
    for r in records:
        for field in (r.get("unscored_inputs") or {}):
            unmet[field] += 1
    budget = Counter((r.get("unscored_inputs") or {}).get("주거비 예산") for r in records)
    budget.pop(None, None)
    workplace = Counter((r.get("unscored_inputs") or {}).get("직장/학교 위치") for r in records)
    workplace.pop(None, None)
    raised = Counter(d["axis"] for r in records for d in r.get("feedback_directions") or []
                     if d.get("direction") == "increase" and d.get("axis"))
    lowered = Counter(d["axis"] for r in records for d in r.get("feedback_directions") or []
                      if d.get("direction") == "decrease" and d.get("axis"))
    candidate_rows: Counter = Counter()
    for r in records:
        for c in r.get("candidates") or []:
            candidate_rows[(r.get("scope"), c["region"], c["role"])] += 1
    dates = sorted(r.get("date") for r in records if r.get("date"))
    return {
        "total": total,
        "period": (dates[0], dates[-1]) if dates else None,
        "scopes": _share(scopes, total),
        "conditions": _share(conditions, total),
        "no_condition": no_condition,
        "unmet": _share(unmet, total),
        "budget": _share(budget, total),
        "workplace": _share(workplace, total),
        "feedback_sessions": sum(1 for r in records if r.get("feedback_count")),
        "raised": _share(raised, total),
        "lowered": _share(lowered, total),
        "candidates": [
            {"비교 범위": scope, "지역": region, "역할": role, "건수": n}
            for (scope, region, role), n in candidate_rows.most_common()
        ],
    }
