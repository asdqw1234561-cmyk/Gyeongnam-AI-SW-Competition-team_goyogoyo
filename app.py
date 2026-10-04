# Streamlit 실행 진입점
import json
import uuid

import pandas as pd
import streamlit as st

from agent import llm
from agent.ollama_agent import interpret_weight_feedback, plan_followup_questions
from agent.planner import TOOL_LABELS, run_agent_plan
from agent.planner import OLLAMA_MODEL as PLANNER_MODEL
from agent.planner_loop import REVIEW_TOOL_LABELS, explain_candidates
from analysis import feedback, scoring
from analysis.candidates import build_candidate_set, strength_max_rank, weakness_min_rank
from services.region_data import REGION_SCOPES, get_all_regions, is_supported_region, region_type_for
from services import demand_log
from services.region_map import BOUNDARY_SOURCE, boundary_base_date, build_candidate_map_regions
from services.schools import SCHOOL_REFERENCE_LIMITATIONS, SCHOOL_REFERENCE_TITLE, load_school_reference

MAX_CANDIDATE_COUNT = 5  # 가장 작은 비교 범위(창원시 5개 구) 수와 같게 맞춤

# 피드백(가중치 직접 조정) 슬라이더의 session_state 키와 표시 라벨.
# 키 집합은 analysis.scoring.VALID_SCORABLE_INDICATOR_CODES와 항상 일치해야 한다.
FEEDBACK_SLIDER_KEYS = {
    "bus_stop_count": "fb_weight_bus_stop_count",
    "hospital_count": "fb_weight_hospital_count",
    "convenience_store_count": "fb_weight_convenience_store_count",
}
FEEDBACK_INDICATOR_LABELS = {
    "bus_stop_count": "교통 (버스정류장 수)",
    "hospital_count": "의료 (의료기관 수)",
    "convenience_store_count": "생활편의 (편의점 수)",
}
CHANGWON_DISTRICT_NAMES = ["의창구", "성산구", "마산합포구", "마산회원구", "진해구"]
GYEONGNAM_CITY_COUNTY_NAMES = ["진주시", "통영시", "사천시", "김해시", "밀양시", "거제시", "양산시", "의령군", "함안군",
                               "창녕군", "고성군", "남해군", "하동군", "산청군", "함양군", "거창군", "합천군"]

# 최초 입력 폼 선택지. 희망지역은 비교 범위(같은 유형끼리: 창원시 5개 구 / 경남 시 / 경남 군) 중에서만 고르게 해 미지원 지역
# 입력 자체가 생기지 않게 한다. 직장/학교 위치·주거비 예산은 아직 대응 지표(이동시간,
# 실거래 주거비)가 없어 점수 계산에 쓰지 않고 참고용으로만 저장한다 - "선택 안 함"은
# 기존 자유입력의 빈 값과 같게 ""로 저장한다.
NOT_SELECTED = "선택 안 함"
REGION_OPTIONS = list(REGION_SCOPES)
WORKPLACE_OPTIONS = [NOT_SELECTED] + [f"창원시 {d}" for d in CHANGWON_DISTRICT_NAMES] + [
    f"경남 {name}" for name in GYEONGNAM_CITY_COUNTY_NAMES] + [
    "경남 외 지역",
    "재택근무·해당 없음",
]
HOUSING_BUDGET_OPTIONS = [
    NOT_SELECTED,
    "월세 30만원 미만",
    "월세 30~50만원",
    "월세 50~70만원",
    "월세 70만원 이상",
    "전세 희망",
    "매매 희망",
]
REFERENCE_ONLY_HELP = "현재 점수 계산에는 반영되지 않고 참고용으로만 저장됩니다(대응하는 실제 데이터 미확보)."


def _option_index(options: list[str], previous: str | None, empty_value: str | None = None) -> int:
    """이전 입력값을 선택지 위치로 되돌린다. 빈 값은 empty_value(예: "선택 안 함")로,
    선택지에 없는 예전 자유입력 값은 첫 항목으로 본다."""
    value = previous or empty_value
    return options.index(value) if value in options else 0

# 화면에 표시할 간결한 주의사항. CSV의 note 컬럼(검증 정보 전체, 예외 ID 목록 등)은
# data/region_indicators.csv에 그대로 보존되며, 여기서는 화면용 짧은 문구로만 대체한다.
SHORT_NOTES = {
    "hospital_count": "의원·치과의원·한의원 등이 포함된 등록 의료기관 수입니다.",
    "bus_stop_count": "공식 행정경계 내부 좌표 기준 집계이며, 버스정류장 개수는 실제 통근시간을 의미하지 않습니다.",
    "convenience_store_count": "상가정보에 등록된 업소 기준 집계로, 동일 주소 중복 등록 등으로 실제 영업 매장 수와 다를 수 있습니다.",
}


REFERENCE_INDICATOR_ORDER = ["hospital_count", "bus_stop_count", "convenience_store_count"]

UNAVAILABLE_DATA_NOTICE = (
    "실제 대중교통 소요시간, 월세·전세 가격, 응급실 운영 병원 수, 대형마트 수, "
    "교육·안전·자연환경·문화시설 지표는 아직 확보되지 않아 추천 계산에 사용되지 않습니다. "
    "(지역별 초·중·고 학교 수는 바로 아래 '교육시설 수 참고정보'로만 보여 주며 점수에는 쓰지 않습니다.)"
)

RELATIVE_SCORE_CAVEAT = (
    "이 점수는 시설 수를 인구 1만 명당으로 바꿔 같은 유형 지역끼리 비교한 상대 점수이며, 면적·실제 거리·"
    "이동시간은 반영하지 않았습니다(100점 = 해당 지표에서 비교 지역 중 인구 1만 명당 값이 가장 높다는 뜻일 뿐, "
    "완벽한 정주환경을 의미하지 않습니다)."
)


def _render_school_reference() -> None:
    """구별 초·중·고 학교 수 참고정보. 추천 점수·후보·Critic과 무관하게 읽기만 한다(DEC-19)."""
    reference = load_school_reference(region_ids=[r["region_id"] for r in _scope_regions()])
    with st.expander(f"📚 {SCHOOL_REFERENCE_TITLE}"):
        if reference["status"] != "확보":
            st.info(f"교육시설 수: 미확보 - {reference['reason']} 추천 결과에는 영향이 없습니다.")
            return
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "구": row["region_name"],
                        "초등학교": row["elementary_school_count"],
                        "중학교": row["middle_school_count"],
                        "고등학교": row["high_school_count"],
                        "총 학교 수": row["school_count"],
                        "분교(총 학교 수에 포함)": row["branch_school_count"],
                    }
                    for row in reference["rows"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(f"출처: {reference['source']} · 기준일 {reference['reference_date']} · 운영 중인 학교만 집계")
        for limitation in SCHOOL_REFERENCE_LIMITATIONS:
            st.caption(f"· {limitation}")


def _unscored_inputs(initial_input: dict) -> list[str]:
    """입력은 받았지만 대응 데이터가 없어 점수에 반영하지 못한 항목 이름(Critic 커버리지 점검용)."""
    return [name for name in ("직장/학교 위치", "주거비 예산") if (initial_input or {}).get(name)]


def render_agent_execution_log() -> None:
    """
    '🤖 Agent가 실제로 한 일' 접기 영역의 계획·도구 실행 기록을 렌더링한다.
    agent.planner.run_agent_plan()의 반환값(st.session_state.agent_execution_log)을
    그대로 보여줄 뿐, 여기서 새로 판단하거나 숫자를 만들지 않는다.

    "AI가 계획한 작업"(planned_tool_calls, 검증 전 원본)과 "Python이 실제 실행한
    작업"(executed_tool_calls, run_agent_plan()이 실제로 호출한 도구만) 을 항상
    분리해서 보여준다 - 호출하지 않은 도구를 호출한 것처럼 표시하지 않기 위함이다.
    """
    log = st.session_state.agent_execution_log
    if not log:
        st.info("실행 기록이 없습니다.")
        return

    if log["mode"] == "ai_planned":
        st.success("✅ AI가 계획한 작업을 Python이 검증한 뒤 그대로 실행했습니다.")
    elif log["planner_error"]:
        st.warning(f"⚠️ AI 분석 계획 호출에 실패해 기본 분석 절차로 진행했습니다: {log['planner_error']}")
    else:
        st.warning("⚠️ AI가 제안한 계획이 검증을 통과하지 못해 기본 분석 절차로 진행했습니다.")

    if log.get("goals"):
        st.markdown("**AI가 식별한 분석 목표**")
        for g in log["goals"]:
            st.caption(f"· {g}")

    weights_source_label = "사용자가 승인한 가중치" if log["weights_source"] == "user_confirmed" else "조건별 동일 가중치(고정 규칙)"
    st.markdown(f"**분석에 사용한 가중치** ({weights_source_label})")
    if log["approved_weights"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {"지표": FEEDBACK_INDICATOR_LABELS.get(c, c), "가중치": f"{w:.1f}%"}
                    for c, w in log["approved_weights"].items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("승인된 가중치가 없습니다(계산 가능한 조건이 선택되지 않았습니다).")

    if log.get("unsupported_requests"):
        st.markdown("**AI가 '지원 불가'로 분류한 요청**")
        for item in log["unsupported_requests"]:
            st.caption(f"· {item['request']} - {item['reason']}")

    if log.get("planned_tool_calls"):
        expander_title = (
            "AI가 제안한 작업 계획 (검증 후 그대로 실행됨)"
            if log["mode"] == "ai_planned"
            else "AI가 제안했지만 검증에 실패해 사용하지 않은 계획"
        )
        with st.expander(expander_title):
            for call in log["planned_tool_calls"]:
                tool_name = call.get("tool") if isinstance(call, dict) else None
                reason = call.get("reason", "") if isinstance(call, dict) else ""
                st.caption(f"· {TOOL_LABELS.get(tool_name, tool_name)} — {reason}")
    elif log["mode"] == "fallback_default" and log["planner_error"]:
        st.caption("AI 계획 자체를 호출하지 못해 제안된 계획이 없습니다.")

    st.markdown("**Python이 실제로 실행한 작업**")
    for i, entry in enumerate(log["executed_tool_calls"], start=1):
        label = TOOL_LABELS.get(entry["tool"], entry["tool"])
        status_icon = "✅" if entry["executed"] else "❌"
        st.markdown(f"{status_icon} **{i}. {label}**")
        if entry.get("reason"):
            st.caption(entry["reason"])

        if entry["tool"] == "get_available_indicators" and entry.get("result"):
            avail_text = ", ".join(
                f"{FEEDBACK_INDICATOR_LABELS.get(c, c)}({'확보' if ok else '미확보'})"
                for c, ok in entry["result"].items()
            )
            st.caption(f"조회 결과: {avail_text}")
        elif entry["tool"] == "get_region_indicators":
            confirmed = entry.get("confirmed_indicator_codes") or []
            requested = entry.get("indicator_codes") or []
            missing = [c for c in requested if c not in confirmed]
            if confirmed:
                st.caption(f"조회 성공: {', '.join(FEEDBACK_INDICATOR_LABELS.get(c, c) for c in confirmed)}")
            if missing:
                st.caption(f"미확보로 제외: {', '.join(FEEDBACK_INDICATOR_LABELS.get(c, c) for c in missing)}")
        elif entry["tool"] == "calculate_region_scores" and entry.get("result"):
            st.caption(f"점수 계산 실행 결과 상태: {entry['result'].get('status', '-')}")

        if entry.get("error"):
            st.caption(f"⚠️ 오류: {entry['error']}")

    if log.get("notes"):
        st.markdown("**Python의 보완/안내**")
        for n in log["notes"]:
            st.caption(f"· {n}")

    _render_review_steps(log)


_REVIEW_ACTION_LABELS = {"answer": "설명 작성", "call_tools": "추가 도구 요청", "error": "판단 실패",
                         "deterministic": "계산 결과로 기본 설명 작성", "paraphrase": "AI가 문장 다듬기 → 검증"}


def _render_review_steps(log: dict) -> None:
    """점수 계산 후 AI가 결과를 보고 판단한 과정(관찰 -> 판단 -> 행동). 표시 전용."""
    steps = log.get("agent_steps") or []
    st.markdown("**결과 확인 후 AI 판단 (관찰 → 판단 → 행동)**")
    if not steps:
        st.caption("없음 — AI 계획으로 계산하지 않아 결과 검토 단계를 건너뛰었습니다."
                   if log["mode"] != "ai_planned" else "없음")
        return
    for step in steps:
        round_label = f"{step['round']}회차" if isinstance(step["round"], int) else f"{step['round']} 단계"
        line = f"· {round_label}: {_REVIEW_ACTION_LABELS.get(step['action'], step['action'])}"
        if step.get("reason"):
            line += f" — {step['reason']}"
        st.caption(line)
        if step.get("executed_tools"):
            st.caption("  → 실행: " + ", ".join(REVIEW_TOOL_LABELS.get(t, t) for t in step["executed_tools"]))
        if step.get("note"):
            st.caption(f"  → {step['note']}")


def _render_final_answer(final: dict, heading: str) -> None:
    """검증을 통과한 AI 설명 또는 Python 요약. 최초 추천과 피드백 재평가가 같은 표시 방식을 쓴다."""
    st.markdown(heading)
    if final["source"] == "ai_paraphrase":
        st.markdown(final["text"])
        st.caption("✅ 계산으로 확정된 사실을 AI가 읽기 쉽게 다듬은 문장입니다. "
                   "없는 사실·숫자·후보가 끼어들지 않았는지 검증을 통과했습니다.")
        with st.expander("검증 기준이 된 기본 설명 보기"):
            st.markdown(final["deterministic_text"])
        return
    if final["source"] == "deterministic":
        st.markdown(final["text"])
        reason = f" AI가 다듬은 문장은 검증을 통과하지 못해 쓰지 않았습니다({final['rejected_reason']})."             if final.get("rejected_reason") else ""
        st.caption(f"📋 계산으로 확정된 사실(후보 역할·강점·약점·평가 항목·한계)만으로 작성한 설명입니다.{reason}")
        return
    if final["source"] == "ai_verified":
        st.success("AI가 계산 결과와 Agent가 확정한 후보·Critic 결과를 보고 작성한 설명입니다 · "
                   "설명 속 숫자와 후보 역할을 실제 결과와 대조해 검증했습니다")
        st.write(final["text"])
    else:
        reason = f" (AI 설명을 쓰지 않은 이유: {final['rejected_reason']})" if final.get("rejected_reason") else ""
        st.info(f"📋 계산 결과 요약 · Python이 실제 계산 결과로 작성했습니다{reason}")
        st.text(final["text"])


AXIS_SHORT_LABELS = {"bus_stop_count": "교통", "hospital_count": "의료", "convenience_store_count": "생활편의"}
ROLE_ICONS = {"best": "👉", "balanced": "⚖️", "alternative": "🔀", "value": "💰"}
ROLE_GUIDE = (
    "최적 = 지금 비율로 종합점수 1위 · 균형 = 가장 약한 항목도 비교적 괜찮은 곳 · "
    "대안 = 최적 후보가 약한 항목에서 앞서는 곳"
)


def _weights_text(result: dict | None) -> str:
    return " · ".join(
        f"{AXIS_SHORT_LABELS.get(uc['indicator_code'], uc['indicator_name'])} {uc['weight'] * 100:.0f}%"
        for uc in (result or {}).get("used_conditions", [])
    ) or "없음"


def _current_view_result() -> dict | None:
    """화면 맨 위에 보여줄 '지금 적용 중인 결과' - 승인된 피드백 결과가 있으면 그것, 없으면 최초 추천."""
    applied = st.session_state.feedback_recommendation
    if applied is not None and applied.get("status") == "ok":
        return applied
    return st.session_state.initial_recommendation


def _scope_label() -> str:
    """지금 비교 중인 범위 이름(희망지역에서 고른 값). 예전 입력은 창원시 5개 구로 본다."""
    text = st.session_state.initial_input.get("희망지역", "")
    return text if text in REGION_SCOPES else "창원시 5개 구"


def _scope_regions() -> list[dict]:
    """피드백 재평가가 최초 추천과 같은 비교 범위(같은 유형 지역)만 쓰도록 넘기는 지역 목록."""
    return get_all_regions(region_type=region_type_for(st.session_state.initial_input.get("희망지역", "")))


def _compare_value(component: dict) -> float:
    """비교에 쓴 값: 인구 1만 명당 값이 있으면 그것, 없으면 시설 수(점수 계산 basis와 같음)."""
    return component["per_10k"] if component.get("per_10k") is not None else component["raw_value"]


def _value_text(component: dict) -> str:
    text = f"{component['raw_value']:.0f}개"
    if component.get("per_10k") is not None:
        text += f" (1만 명당 {component['per_10k']:.1f})"
    return text


def _axis_value_ranks(result: dict) -> dict[str, dict[str, int]]:
    """표시용: 지표별 비교값(인구 1만 명당)의 비교 지역 내 순위(값이 같으면 같은 순위). 점수 계산과 무관하다."""
    ranks: dict[str, dict[str, int]] = {}
    for uc in result["used_conditions"]:
        code = uc["indicator_code"]
        values = {r["region_id"]: _compare_value(r["component_scores"][code]) for r in result["region_scores"]}
        ranks[code] = {rid: 1 + sum(1 for v in values.values() if v > value) for rid, value in values.items()}
    return ranks


def _render_candidate_summary(candidate_set: dict, result: dict) -> None:
    """
    analysis.candidates.build_candidate_set()이 확정한 후보 역할을 결론부터 보여준다.
    같은 구가 여러 역할을 맡으면 한 줄로 합친다. 후보·순위는 Python이 계산한 그대로이며 AI가 만들지 않는다.
    """
    if candidate_set.get("status") != "ok":
        return
    groups: dict[str, list[dict]] = {}
    for role in candidate_set["roles"]:
        if role["status"] == "ok":
            groups.setdefault(role["region_id"], []).append(role)
    components = {r["region_id"]: r["component_scores"] for r in result.get("region_scores", [])}
    region_count = len(result.get("region_scores", []))
    with st.container(border=True):
        for roles in groups.values():
            head = roles[0]
            st.markdown(
                f"**{ROLE_ICONS.get(head['role'], '•')} {', '.join(r['role_label'] for r in roles)} · "
                f"{head['region_name']}** — 종합 {head['total_score']:.1f}점 ({region_count}곳 중 {head['rank']}위)"
            )
            profile = " · ".join(
                f"{p['axis']} {_value_text(components[head['region_id']][p['indicator_code']])} {p['axis_rank']}위"
                for p in head["axis_profile"]
            )
            st.caption(
                f"강점: {', '.join(head['strengths']) or '없음'} · 약점: {', '.join(head['weaknesses']) or '없음'}"
                f" | {profile}"
            )
        for role in candidate_set["roles"]:
            if role["status"] != "ok":
                st.caption(f"{ROLE_ICONS.get(role['role'], '•')} **{role['role_label']}** — 제공 안 함: {role['reason']}")
        st.caption(ROLE_GUIDE)


def _render_critic_highlights(candidate_set: dict) -> None:
    """Agent 자체 점검(Critic) 중 사용자가 꼭 알아야 할 것(후보 교체, 경고)만 위에 보여준다. 전체는 아래 접힌 영역."""
    checks = (candidate_set.get("critic") or {}).get("checks", [])
    revised = [c for c in checks if c["code"] == "revised"]
    warnings = [c for c in checks if c["level"] == "warning"]
    if not (revised or warnings):
        return
    st.markdown("**⚠️ 확인할 점 — Agent 자체 점검(Critic)**")
    for check in revised:
        st.caption(f"🔎 {check['message']}")
    for check in warnings:
        st.warning(check["message"])


FOCUS_NONE = "강조 안 함"

try:
    import folium
    from streamlit_folium import st_folium

    _MAP_AVAILABLE = True
except ImportError:  # 지도 라이브러리가 없어도 나머지 결과 화면은 그대로 동작해야 한다
    _MAP_AVAILABLE = False

MAP_ROLE_LEGEND = "🟦 최적 · 🟩 균형 · 🟧 대안 · ⬜ 비교했지만 후보 역할 없음"


def _map_label_html(region: dict) -> str:
    """지도 위 지역 이름표. 후보 지역은 역할을 함께, 나머지는 이름만 작게."""
    name = region["region_name"]
    if region["roles"]:
        text = f"{name}<br><span style='font-weight:600'>{' · '.join(region['roles'])}</span>"
        style = (f"background:{region['color']};color:#fff;font-size:12px;font-weight:700;"
                 "padding:3px 7px;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.35);")
    else:
        text = name
        style = "color:#4b5563;font-size:11px;font-weight:600;text-shadow:0 0 3px #fff,0 0 3px #fff;"
    return (f"<div style='position:absolute;transform:translate(-50%,-50%);white-space:nowrap;"
            f"text-align:center;line-height:1.25;{style}'>{text}</div>")


def _build_candidate_map(map_data: dict, focus: str | None):
    """비교 지역 경계 위에 후보 역할을 색으로 표시한 folium 지도(표시 전용 - 계산 없음)."""
    fmap = folium.Map(tiles="OpenStreetMap", control_scale=True, zoom_control=True, zoom_snap=0.25)
    for region in map_data["regions"]:
        is_candidate = region["primary_role"] is not None
        is_focus = focus is not None and region["region_name"] == focus
        style = {
            "fillColor": region["color"],
            "color": region["color"] if is_candidate else "#6b7280",
            "weight": 3 if is_candidate else 1,
            "fillOpacity": 0.45 if is_candidate else 0.12,
        }
        if is_focus:
            style.update({"weight": 4, "dashArray": "6 4", "color": "#111827"})
        tooltip = f"{region['region_name']} · {region['rank']}위 · 종합 {region['total_score']:.1f}점"
        if region["roles"]:
            tooltip += f" · {', '.join(region['roles'])}"
        folium.GeoJson(
            {"type": "Feature", "geometry": region["geometry"], "properties": {}},
            style_function=lambda _feature, style=style: style,
            highlight_function=lambda _feature: {"weight": 4, "fillOpacity": 0.6},
            tooltip=tooltip,
        ).add_to(fmap)
        folium.Marker(
            location=region["label_point"],
            icon=folium.DivIcon(html=_map_label_html(region), icon_size=(0, 0)),
            tooltip=tooltip,
        ).add_to(fmap)
    if map_data["bounds"]:
        fmap.fit_bounds(map_data["bounds"], padding=(12, 12))
    return fmap


def _render_candidate_map(result: dict, candidate_set: dict) -> None:
    """
    추천 후보 지역이 대략 어디인지 지도로 보여준다. 색은 이미 확정된 후보 역할(최적·균형·대안)이고,
    점수·순위·역할을 새로 계산하지 않는다. 칠한 영역은 그 시·군·구 전체이며 특정 동네를 고른 것이 아니다.
    """
    st.markdown("### 🗺️ 후보 지역 위치")
    if not _MAP_AVAILABLE:
        st.caption("지도 라이브러리(folium/streamlit-folium)를 불러오지 못해 지도를 표시할 수 없습니다. "
                   "아래 비교표는 그대로 이용할 수 있습니다.")
        return
    map_data = build_candidate_map_regions(result, candidate_set)
    if not map_data["regions"]:
        st.caption("행정구역 경계 파일을 찾지 못해 지도를 표시할 수 없습니다(data/raw/gyeongnam_boundaries.geojson).")
        return
    focus = st.session_state.get("focus_region")
    focus = None if focus in (None, FOCUS_NONE) else focus
    st.caption(MAP_ROLE_LEGEND + (" · 점선 = 강조한 관심 지역" if focus else ""))
    try:
        st_folium(
            _build_candidate_map(map_data, focus),
            height=440,
            use_container_width=True,
            key=f"candidate_map_{region_type_for(st.session_state.initial_input.get('희망지역', ''))}",
            returned_objects=[],
        )
    except Exception as exc:  # 지도 컴포넌트 오류가 결과 화면 전체를 막지 않게 한다
        st.warning(f"⚠️ 지도를 표시하는 중 문제가 발생했습니다: {exc}")
        return
    base_date = boundary_base_date()
    base_text = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}" if base_date and len(base_date) == 8 else "기준일 미상"
    missing = f" · 경계가 없어 표시하지 못한 지역: {', '.join(map_data['missing'])}" if map_data["missing"] else ""
    st.caption(
        "색칠한 영역은 해당 시·군·구 **전체**이며, 그 안의 특정 동네나 주거 단지를 추천한 것이 아닙니다"
        "(행정동 단위 지표 미확보). 지역 위에 마우스를 올리면 순위·종합점수가 보입니다. "
        f"경계: {BOUNDARY_SOURCE}({base_text}, 화면 표시용으로 단순화){missing}"
    )


def _render_focus_summary(result: dict, focus: str, ranks: dict, candidate_set: dict | None) -> None:
    """관심 지역 한 곳의 위치를 한 줄로: 종합 순위, 항목별 강점·약점(같은 판정 기준), 맡은 후보 역할. 표시 전용."""
    row = next((r for r in result["region_scores"] if r["region_name"] == focus), None)
    if row is None:
        return
    n = len(result["region_scores"])
    strengths, weaknesses = [], []
    for uc in result["used_conditions"]:
        rank = ranks[uc["indicator_code"]][row["region_id"]]
        axis = AXIS_SHORT_LABELS.get(uc["indicator_code"], uc["indicator_name"])
        if rank <= strength_max_rank(n):
            strengths.append(f"{axis}({rank}위)")
        elif rank >= weakness_min_rank(n):
            weaknesses.append(f"{axis}({rank}위)")
    roles = [r["role_label"] for r in (candidate_set or {}).get("roles", [])
             if r.get("status") == "ok" and r.get("region_name") == focus]
    role_text = f" · 맡은 후보 역할: {', '.join(roles)}" if roles else " · 이번 후보 역할에는 들지 않음"
    st.info(f"★ {focus}: 종합 {row['total_score']:.1f}점, {n}곳 중 {row['rank']}위 · "
            f"강점 {', '.join(strengths) or '없음'} · 약점 {', '.join(weaknesses) or '없음'}{role_text}")


def _render_comparison_table(result: dict, candidate_set: dict | None = None) -> None:
    """비교 지역 종합점수와 항목별 실제 개수·인구 1만 명당 값·순위를 표 하나로 보여준다.
    사용자가 고른 관심 지역은 ★로 강조하고 위치를 한 줄로 요약한다(점수·순위는 이미 계산된 값 그대로)."""
    ranks = _axis_value_ranks(result)
    st.markdown(f"### 📊 {_scope_label()} 한눈에 비교")
    names = [r["region_name"] for r in result["region_scores"]]
    if st.session_state.get("focus_region") not in [FOCUS_NONE, *names]:
        st.session_state["focus_region"] = FOCUS_NONE  # 범위가 바뀌어 예전 선택이 목록에 없으면 초기화
    focus = st.selectbox("관심 지역 강조 (선택)", [FOCUS_NONE, *sorted(names)], key="focus_region",
                         help="비교 범위 안에서 궁금한 지역을 고르면 표에서 ★로 표시하고 그 지역의 위치를 요약합니다. "
                              "점수·순위·후보는 바뀌지 않습니다.")
    if focus != FOCUS_NONE:
        _render_focus_summary(result, focus, ranks, candidate_set)
    rows = []
    for r in result["region_scores"]:
        mark = "★ " if r["region_name"] == focus else ""
        row = {"순위": r["rank"], "지역": mark + r["region_name"] + (" (동점)" if r["tied"] else ""),
               "종합점수": round(r["total_score"], 1)}
        for uc in result["used_conditions"]:
            code = uc["indicator_code"]
            row[f"{AXIS_SHORT_LABELS.get(code, code)} ({uc['indicator_name']})"] = (
                f"{_value_text(r['component_scores'][code])} · {ranks[code][r['region_id']]}위"
            )
        if r.get("population"):
            row["인구"] = f"{r['population']:,.0f}명"
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    requested = [r["region_name"] for r in result.get("top_candidates", [])]
    st.caption(
        f"요청하신 후보 {len(requested)}곳(종합점수 순): {', '.join(requested)} · "
        + ("시설 수를 인구 1만 명당으로 바꿔 같은 유형 지역끼리 비교한 상대 점수이며(순위도 1만 명당 값 기준), "
           "면적·실제 거리·이동시간은 반영하지 않았습니다." if result.get("basis") == scoring.BASIS_PER_CAPITA
           else "시설 수 그대로 비교한 상대 점수이며(인구 데이터 미확보), 인구·면적·실제 이동시간은 반영하지 않았습니다.")
    )


def _render_change_summary(initial_result: dict, current_result: dict, current_review: dict) -> None:
    """피드백 적용 결과를 최초 추천과 비교한다(후보 역할 변화는 analysis.feedback.candidate_changes 그대로)."""
    changes = [c for c in feedback.candidate_changes(_review_of(initial_result), current_review) if c["changed"]]
    change_text = ", ".join(f"{c['role_label']} {c['before'] or '-'} → {c['after'] or '-'}" for c in changes)
    st.info(f"🔁 처음 결과와 비교: {change_text or '후보 역할은 그대로입니다'}")

    initial_weight_by_code = {uc["indicator_code"]: uc["weight"] for uc in initial_result.get("used_conditions", [])}
    current_weight_by_code = {uc["indicator_code"]: uc["weight"] for uc in current_result["used_conditions"]}
    initial_rank_by_region = {
        r["region_id"]: (r["rank"], r["total_score"]) for r in initial_result.get("region_scores", [])
    }
    with st.expander("처음 결과와 자세히 비교"):
        st.markdown("**비율 변화**")
        st.dataframe(pd.DataFrame([
            {"항목": FEEDBACK_INDICATOR_LABELS[code],
             "처음": f"{initial_weight_by_code.get(code, 0) * 100:.1f}%",
             "지금": f"{current_weight_by_code.get(code, 0) * 100:.1f}%"}
            for code in REFERENCE_INDICATOR_ORDER
            if code in initial_weight_by_code or code in current_weight_by_code
        ]), hide_index=True, width="stretch")
        rows = []
        for row in current_result["region_scores"]:
            prev_rank, prev_score = initial_rank_by_region.get(row["region_id"], (None, None))
            if prev_rank is None:
                rank_change = "-"
            elif prev_rank == row["rank"]:
                rank_change = "변동없음"
            elif prev_rank > row["rank"]:
                rank_change = f"▲{prev_rank - row['rank']}"
            else:
                rank_change = f"▼{row['rank'] - prev_rank}"
            rows.append({
                "지역": row["region_name"],
                "처음 순위": prev_rank if prev_rank is not None else "-",
                "지금 순위": row["rank"],
                "순위 변화": rank_change,
                "처음 점수": round(prev_score, 1) if prev_score is not None else "-",
                "지금 점수": round(row["total_score"], 1),
                "점수 변화": round(row["total_score"] - prev_score, 1) if prev_score is not None else "-",
            })
        st.markdown("**비교 지역 점수·순위 변화**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_result_view() -> None:
    """
    완료 화면 맨 위: 지금 적용 중인 결과 하나를 결론 → 확인할 점 → AI 설명 → 비교표 순으로 보여준다.
    피드백이 승인되면 그 결과로 바뀌고(처음 결과와의 차이를 함께 표시), 아니면 최초 추천을 보여준다.
    점수·후보·Critic·설명은 이미 계산된 값을 표시만 한다.
    """
    initial_input = st.session_state.initial_input
    initial_result = st.session_state.initial_recommendation
    result = _current_view_result()
    is_feedback = result is not initial_result
    log = st.session_state.get("agent_execution_log") or {}

    if is_feedback:
        st.subheader("🏆 추천 결과 — 조건을 바꾼 뒤")
        st.caption(f"적용 중인 비율: {_weights_text(result)} (처음: {_weights_text(initial_result)})")
        candidate_set = build_candidate_set(result, _unscored_inputs(initial_input))
        final = (st.session_state.get("feedback_explanation") or {}).get("final_answer")
        explanation_heading = "### 🤖 AI의 재평가 결과 설명"
    else:
        st.subheader("🏆 추천 결과")
        st.caption(f"적용 중인 비율: {_weights_text(result)} · {_weight_source_text()}")
        candidate_set = log.get("candidate_review") or build_candidate_set(result, _unscored_inputs(initial_input))
        final = log.get("final_answer")
        explanation_heading = "### 🤖 AI의 추천 결과 설명"

    st.caption(f"🗺️ 비교 범위: {_scope_label()} — 규모가 비슷한 같은 유형 지역끼리만 인구 1만 명당으로 비교합니다.")

    if result["status"] == "no_usable_conditions":
        st.info(f"ℹ️ {result['message']} 선택하신 조건에 대응하는 실제 데이터가 아직 없습니다. "
                "교통·의료·생활편의 중 하나 이상을 골라 다시 시작해 주세요.")
        return

    _render_candidate_summary(candidate_set, result)
    _render_candidate_map(result, candidate_set)
    _render_critic_highlights(candidate_set)
    if is_feedback:
        _render_change_summary(initial_result, result, candidate_set)
    if final:
        _render_final_answer(final, explanation_heading)
    if not is_feedback:
        for sim in log.get("what_if_results") or []:
            weights = ", ".join(f"{FEEDBACK_INDICATOR_LABELS.get(c, c)} {v}%" for c, v in sim["weights_percent"].items())
            with st.expander(f"🔍 AI가 확인한 가정: 비율을 {weights}로 바꾼다면 (실제 추천에는 미적용)"):
                st.dataframe(
                    pd.DataFrame([{"순위": r["rank"], "구": r["region_name"], "종합점수(가정)": r["total_score"]}
                                  for r in sim["ranking"]]),
                    hide_index=True, width="stretch",
                )
                st.caption("이 가정을 실제로 적용하려면 아래 '🔄 조건 바꿔서 다시 보기'에서 요청하고 승인하세요.")
    _render_comparison_table(result, candidate_set)
    _render_demand_consent(result, candidate_set)


DEMAND_CONSENT_HELP = (
    "체크하면 이번 검색의 선택값만 익명으로 저장해 지자체 화면의 '이주 희망자 수요' 통계에 씁니다: "
    "비교 범위, 고른 생활조건, 적용 비율, 점수에 반영하지 못한 조건(주거비 예산 구간·직장/학교 구 등), "
    "조건을 바꾼 방향, 후보로 나온 지역. 추가 요청사항 같은 자유 문장, 지도 좌표, 위치 질문은 저장하지 않습니다. "
    "체크를 풀면 그 뒤로는 저장하지 않습니다."
)


def _render_demand_consent(result: dict, candidate_set: dict | None) -> None:
    """익명 통계 제공 동의(기본 꺼짐). 동의했을 때만, 내용이 바뀔 때마다 한 줄을 남긴다(services.demand_log)."""
    consent = st.checkbox("📊 익명 통계 제공에 동의 (선택)", key="demand_consent", help=DEMAND_CONSENT_HELP)
    st.caption("지자체가 이주 희망자들이 어떤 조건을 찾는지 볼 수 있도록 선택값만 익명으로 모읍니다. "
               "자유 문장·좌표·개인정보는 저장하지 않습니다.")
    if not consent:
        return
    session_id = st.session_state.setdefault("demand_session_id", uuid.uuid4().hex[:12])
    record = demand_log.build_record(
        session_id, st.session_state.initial_input, result, candidate_set,
        st.session_state.get("feedback_history"),
    )
    signature = json.dumps({k: v for k, v in record.items() if k != "date"}, ensure_ascii=False, sort_keys=True)
    if st.session_state.get("demand_saved_signature") == signature:
        return
    try:
        demand_log.append_record(record)
    except OSError as exc:  # 저장 실패가 결과 화면을 막지 않게 한다
        st.caption(f"⚠️ 익명 통계를 저장하지 못했습니다: {exc.__class__.__name__}")
        return
    st.session_state["demand_saved_signature"] = signature


def _weight_source_text() -> str:
    """최초 추천 비율이 어디서 왔는지 한 줄로."""
    wc = st.session_state.weight_confirmation
    if not wc or not wc["asked"]:
        return "선택하신 조건을 같은 비율로 비교"
    origin_label = "처음 입력한 '추가 요청사항'" if wc.get("answer_source") == "initial_extra_request" else "AI 추가질문 답변"
    if wc["source"] == "ai_approved":
        return f"✅ {origin_label}(\"{wc['answer_text']}\")에서 읽은 비율을 승인해 사용"
    if wc["source"] == "ai_rejected_by_user":
        return f"{origin_label}의 비율을 적용하지 않기로 해 같은 비율로 비교"
    return f"{wc['reason']} 같은 비율로 비교"


def _not_used_inputs(result: dict) -> list[str]:
    """입력했지만 대응 데이터가 없어 점수에 반영하지 못한 정보(화면 하단 '데이터 출처와 한계'에 표시)."""
    initial_input = st.session_state.initial_input
    wc = st.session_state.weight_confirmation
    items = []
    if initial_input.get("직장/학교 위치"):
        items.append(f"직장/학교 위치('{initial_input['직장/학교 위치']}') - 실제 이동시간 지표가 없어 반영하지 않음")
    if initial_input.get("주거비 예산"):
        items.append(f"주거비 예산('{initial_input['주거비 예산']}') - 실제 주거비(월세·전세) 지표가 없어 반영하지 않음")
    if initial_input.get("자가용 보유 여부"):
        items.append(f"자가용 보유 여부('{initial_input['자가용 보유 여부']}') - 대응하는 지표가 없어 반영하지 않음")
    extra_request_text = initial_input.get("추가 요청사항")
    extra_request_was_approved = (
        wc and wc.get("answer_source") == "initial_extra_request" and wc["source"] == "ai_approved"
    )
    if extra_request_text and not extra_request_was_approved:
        if wc and wc.get("answer_source") == "initial_extra_request":
            items.append(
                f"추가 요청사항('{extra_request_text}') - 비율 해석 결과는 위에 안내된 대로 "
                "처리되었고, 그 외 서술 내용은 대응하는 지표가 없어 반영하지 않음"
            )
        else:
            items.append(f"추가 요청사항('{extra_request_text}') - 대응하는 지표가 없어 반영하지 않음")
    weight_question = st.session_state.weight_question_plan
    weight_question_text = weight_question["text"] if weight_question else None
    if any(q != weight_question_text for q in st.session_state.followup_answers):
        items.append(
            "AI 추가질문(가중치 확인 질문 제외) 답변 - 실제 지표와 연결할 수 없어 점수 계산에 "
            "반영하지 않음(입력 내용으로는 보존됨)"
        )
    # 비율의 출처가 최초 입력의 '추가 요청사항'이면 바로 위 추가 요청사항 항목에서 이미
    # 안내했으므로, 이 줄은 AI 추가질문(가중치 확인)에 답한 경우에만 보여준다.
    if wc and wc["asked"] and wc["source"] != "ai_approved" and wc.get("answer_source") != "initial_extra_request":
        items.append(f"AI 추가질문(가중치 확인) 답변 - {wc['reason']}")
    for e in result.get("excluded_conditions", []):
        items.append(f"중요 생활조건 '{e['condition']}' - {e['reason']}")
    return items


def _indicator_sources(codes: list[str]) -> list[dict]:
    """점수에 쓴 지표의 출처·기준일(data/region_indicators.csv 그대로)."""
    found: dict[str, dict] = {}
    for region in get_all_regions():
        for indicators in region["categories"].values():
            for i in indicators:
                if i["indicator_code"] in codes and i["indicator_code"] not in found:
                    found[i["indicator_code"]] = {
                        "항목": f"{AXIS_SHORT_LABELS.get(i['indicator_code'], '인구')} ({i['indicator_name']})",
                        "출처": i["source"] or "-",
                        "기준일": i["reference_date"] or "-",
                        "주의": SHORT_NOTES.get(i["indicator_code"], "인구 1만 명당 비교의 분모로만 사용"),
                    }
    return [found[c] for c in codes if c in found]


REFERENCE_TYPE_ORDER = {"구": 0, "시": 1, "군": 2}
REFERENCE_TYPE_LABELS = {"구": "창원시 구", "시": "시", "군": "군"}


def _indicator_value(region: dict, category: str, code: str) -> float | None:
    for indicator in region["categories"].get(category, []):
        if indicator["indicator_code"] == code and indicator["data_status"] == "확보" and indicator["value"]:
            return float(indicator["value"])
    return None


def _render_all_regions_reference() -> None:
    """경남 22개 지역 참고 표 - 유형이 다른 지역도 실제 개수와 인구 1만 명당 값을 나란히 보되 점수·순위는 매기지 않는다
    (DEC-21: 유형이 다른 지역을 점수로 비교하면 왜곡이 크다). 값은 data/region_indicators.csv 그대로, 1만 명당은 표시용 나눗셈."""
    current_type = region_type_for(st.session_state.initial_input.get("희망지역", ""))
    with st.expander("🗺️ 경남 22개 지역 참고 표 — 점수 없음 (유형이 다른 지역도 실제 값만 나란히)"):
        rows = []
        for region in sorted(get_all_regions(), key=lambda r: (REFERENCE_TYPE_ORDER.get(r.get("region_type"), 9),
                                                               r["region_name"])):
            population = _indicator_value(region, scoring.POPULATION_CATEGORY, scoring.POPULATION_CODE)
            row = {"비교 범위": "● 지금 비교 중" if region.get("region_type") == current_type else "",
                   "유형": REFERENCE_TYPE_LABELS.get(region.get("region_type"), "-"), "지역": region["region_name"],
                   "인구": f"{population:,.0f}명" if population else "미확보"}
            for code in REFERENCE_INDICATOR_ORDER:
                value = _indicator_value(region, scoring.INDICATOR_CATEGORY[code], code)
                label = FEEDBACK_INDICATOR_LABELS[code]
                if value is None:
                    row[label] = "미확보"
                elif population:
                    row[label] = f"{value:.0f}개 (1만 명당 {value / population * scoring.PER_CAPITA_UNIT:.1f})"
                else:
                    row[label] = f"{value:.0f}개"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "이 표에는 점수·순위가 없습니다. 추천 점수는 규모가 비슷한 같은 유형 지역끼리만 계산합니다 - 유형이 다른 지역을 "
            "한 점수로 비교하면 시설 수 그대로는 큰 시가, 인구 1만 명당은 넓게 흩어진 군이 늘 앞서는 왜곡이 생기기 때문입니다. "
            "특히 군 지역의 1만 명당 버스정류장 수가 큰 것은 정류장이 넓게 흩어져 있어서이며 교통이 편하다는 뜻이 아닙니다. "
            "출처·기준일은 '📋 데이터 출처와 한계'를 참고하세요."
        )


def render_detail_sections(result: dict) -> None:
    """완료 화면 하단의 접힌 영역들: Agent 실행 과정, 계산 근거, 데이터 출처·한계, 교육시설 참고정보."""
    st.divider()
    st.markdown("#### 더 자세히 보기")
    initial_input = st.session_state.initial_input

    with st.expander("🤖 Agent가 실제로 한 일 — 계획 → 도구 실행 → 자체 점검 → 설명 검증"):
        if result is not st.session_state.initial_recommendation:
            st.caption("아래 계획·도구 실행 기록은 최초 추천 때의 기록이고, 자체 점검은 지금 적용 중인 결과 기준입니다.")
        render_agent_execution_log()
        if result.get("status") == "ok":
            review = (
                (st.session_state.get("agent_execution_log") or {}).get("candidate_review")
                if result is st.session_state.initial_recommendation else None
            ) or build_candidate_set(result, _unscored_inputs(initial_input))
            st.markdown("**자체 점검(Critic) 전체 결과**")
            for check in review.get("critic", {}).get("checks", []):
                prefix = "⚠️" if check["level"] == "warning" else "ℹ️"
                st.caption(f"{prefix} {check['message']}")
            if review.get("pareto"):
                st.caption(f"어느 항목에서도 다른 구에 완전히 뒤지지 않는 구: {', '.join(review['pareto'])}")
            st.dataframe(
                pd.DataFrame([{"평가축": a["axis"], "상태": a["status"]} for a in review.get("axes", [])]),
                hide_index=True, width="stretch",
            )

    if result.get("status") == "ok":
        with st.expander("🧮 점수 계산 방법과 근거"):
            st.markdown(f"**적용 비율:** {_weights_text(result)}")
            st.caption(
                "각 항목의 실제 개수를 인구 1만 명당 값으로 바꾸고, 비교 지역 안에서 0~100점으로 바꾼 뒤"
                "(가장 높은 지역 100점, 가장 낮은 지역 0점, min-max 정규화) 비율대로 더했습니다."
            )
            for code, info in result["normalization"].items():
                name = next(uc["indicator_name"] for uc in result["used_conditions"] if uc["indicator_code"] == code)
                st.caption(f"· {name}: {info['formula']}")
            st.markdown("**구별 계산 근거**")
            for row in result["region_scores"]:
                parts = [
                    f"{uc['indicator_name']} {_value_text(row['component_scores'][uc['indicator_code']])}"
                    f"→{row['component_scores'][uc['indicator_code']]['normalized_score']:.1f}점 × {uc['weight'] * 100:.0f}%"
                    for uc in result["used_conditions"]
                ]
                st.caption(f"{row['region_name']}: " + " + ".join(parts) + f" = {row['total_score']:.1f}점")
            st.caption(RELATIVE_SCORE_CAVEAT)

    with st.expander("📋 데이터 출처와 한계"):
        codes = [uc["indicator_code"] for uc in result.get("used_conditions", [])]
        if result.get("basis") == scoring.BASIS_PER_CAPITA:
            codes.append(scoring.POPULATION_CODE)
        if codes:
            st.markdown("**점수에 사용한 공공데이터**")
            st.dataframe(pd.DataFrame(_indicator_sources(codes)), hide_index=True, width="stretch")
        st.markdown("**아직 없는 데이터**")
        st.caption(UNAVAILABLE_DATA_NOTICE)
        st.markdown("**입력했지만 점수에 반영하지 못한 정보**")
        not_used = _not_used_inputs(result)
        if not_used:
            for item in not_used:
                st.caption(f"· {item}")
        else:
            st.caption("입력하신 조건이 전부 점수 계산에 반영되었습니다.")
        if result.get("caveats"):
            st.markdown("**점수를 볼 때 주의할 점**")
            for c in result["caveats"]:
                st.caption(f"· {c}")

    _render_all_regions_reference()
    _render_school_reference()


def _render_input_summary() -> None:
    """완료 화면 맨 위의 입력 요약 한 줄 + 전체 입력 내용(접힌 영역)."""
    inp = st.session_state.initial_input
    parts = ["비교 범위: " + (inp.get("희망지역") or "창원시 5개 구"),
             "중요 조건: " + (", ".join(inp.get("중요 생활조건") or []) or "선택 안 함(확보된 항목 전체)"),
             f"후보 {inp.get('원하는 후보 개수') or 3}곳"]
    st.caption("📝 입력: " + " · ".join(parts))

    with st.expander("입력 내용 전체 보기"):
        st.dataframe(
            pd.DataFrame([
                {"항목": key, "입력": ", ".join(value) if isinstance(value, list) else (str(value) if value else "선택 안 함")}
                for key, value in inp.items()
            ]),
            hide_index=True, width="stretch",
        )
        wc = st.session_state.weight_confirmation
        if st.session_state.followup_answers:
            st.markdown("**AI 추가질문에 대한 답변**")
            st.dataframe(
                pd.DataFrame([{"질문": q, "답변": a or "-"} for q, a in st.session_state.followup_answers.items()]),
                hide_index=True, width="stretch",
            )
            if wc and wc["asked"] and wc["source"] == "ai_approved" and wc.get("answer_source") == "followup_answer":
                st.caption("ℹ️ ⚖️ 가중치 확인 질문의 답변은 승인되어 점수 계산 비율에 반영되었습니다. 나머지는 참고용입니다.")
            else:
                st.caption("ℹ️ 위 답변은 참고용으로 저장만 되며 점수 계산에는 반영되지 않습니다.")
        elif st.session_state.followup_questions == []:
            st.caption("AI가 판단했을 때 추가로 필요한 정보가 없었습니다.")


def _current_applied_result() -> dict | None:
    """지금 실제로 적용 중인 결과(승인된 피드백 결과가 있으면 그것, 없으면 최초 추천)."""
    return st.session_state.feedback_recommendation or st.session_state.initial_recommendation


def _review_of(result: dict | None) -> dict:
    return build_candidate_set(result or {}, _unscored_inputs(st.session_state.initial_input))


def _sync_sliders(weights: dict[str, float]) -> None:
    """위젯 값은 on_click 콜백 안에서만 바꿀 수 있으므로 콜백에서만 호출한다."""
    for code, key in FEEDBACK_SLIDER_KEYS.items():
        st.session_state[key] = min(100, max(0, round(weights.get(code, 0.0))))


def _clear_nl_proposal() -> None:
    st.session_state.nl_feedback_proposal = None
    st.session_state.nl_feedback_proposal_text = None


def _apply_feedback(weights: dict[str, float], *, source: str, text: str | None = None,
                    adjustments: list[dict] | None = None) -> None:
    """
    모든 피드백(슬라이더 '다시 비교하기', 숫자 자연어, 방향성 자연어)이 공유하는 유일한 적용 경로.
    analysis.feedback.reevaluate()로 점수 → 후보·Critic을 다시 계산하고, 이전·이후 가중치와
    후보 변화를 feedback_history(Memory)에 승인된 기록으로 남긴다.
    """
    before_result = _current_applied_result()
    candidate_count = st.session_state.initial_input.get("원하는 후보 개수") or 3
    outcome = feedback.reevaluate(weights, candidate_count, _unscored_inputs(st.session_state.initial_input),
                                  regions=_scope_regions())
    st.session_state.feedback_recommendation = outcome["score_result"]
    st.session_state.feedback_explanation = None
    if outcome["score_result"].get("status") == "ok":
        # 재평가로 바뀐 후보·Critic 결과를 AI가 설명(검증 실패·AI 불가 시 Python 요약)
        st.session_state.feedback_explanation = explain_candidates(
            score_result=outcome["score_result"], candidate_review=outcome["candidate_review"],
            selected_conditions=st.session_state.initial_input.get("중요 생활조건") or [], model=PLANNER_MODEL,
        )
        st.session_state.feedback_history.append(feedback.history_entry(
            source=source, text=text, adjustments=adjustments,
            before_weights=feedback.weights_from_result(before_result),
            after_weights=feedback.weights_from_result(outcome["score_result"]), approved=True,
            before_result=before_result, after_result=outcome["score_result"],
            before_review=_review_of(before_result), after_review=outcome["candidate_review"],
        ))


def _reset_feedback_weights(text: str | None = None) -> None:
    """'처음 조건으로 되돌리기' 버튼 / 자연어 '원래대로' 승인의 on_click 콜백. 슬라이더를 최초 추천
    가중치로 되돌리고 피드백 결과를 지운다(다음 rerun에서 '변경 전후 비교'가 사라진다).
    되돌린 사실과 후보 변화는 feedback_history에 남긴다."""
    before_result = _current_applied_result()
    initial_result = st.session_state.initial_recommendation
    initial_weights = st.session_state.feedback_initial_weights
    for code, key in FEEDBACK_SLIDER_KEYS.items():
        st.session_state[key] = initial_weights.get(code, 0.0)
    if st.session_state.feedback_recommendation is not None:
        st.session_state.feedback_history.append(feedback.history_entry(
            source="reset", text=text,
            before_weights=feedback.weights_from_result(before_result),
            after_weights=feedback.weights_from_result(initial_result), approved=True,
            before_result=before_result, after_result=initial_result,
            before_review=_review_of(before_result), after_review=_review_of(initial_result),
        ))
    st.session_state.feedback_recommendation = None
    st.session_state.feedback_explanation = None
    _clear_nl_proposal()


def _approve_nl_feedback_weights(weights: dict[str, float], source: str = "nl_weights",
                                 adjustments: list[dict] | None = None) -> None:
    """
    AI가 해석한(숫자) 또는 Python이 규칙으로 계산한(방향성) 가중치를 사용자가 승인했을 때의
    on_click 콜백. 슬라이더를 맞추고 _apply_feedback() 한 경로로 재평가한다(AI는 이 계산에
    관여하지 않는다).
    """
    text = st.session_state.get("nl_feedback_proposal_text")
    _sync_sliders(weights)
    _apply_feedback(weights, source=source, text=text, adjustments=adjustments)
    _clear_nl_proposal()


def _reject_nl_feedback_proposal(weights: dict[str, float] | None = None, source: str = "nl_weights",
                                 adjustments: list[dict] | None = None) -> None:
    """무시한 제안도 approved=False로 기록한다(후보 변화 없음)."""
    if weights:
        st.session_state.feedback_history.append(feedback.history_entry(
            source=source, text=st.session_state.get("nl_feedback_proposal_text"), adjustments=adjustments,
            before_weights=feedback.weights_from_result(_current_applied_result()),
            after_weights=weights, approved=False,
        ))
    _clear_nl_proposal()


def _render_direction_proposal(proposal: dict) -> None:
    """
    방향성 피드백("의료를 더 중요하게")의 승인 대기 화면. AI는 축·방향·강도 표현만 추출했고,
    바뀔 가중치는 analysis.feedback.adjust_weights()가 현재 적용 중인 가중치에서 규칙으로 계산한다.
    """
    current = feedback.weights_from_result(_current_applied_result())
    plan = feedback.adjust_weights(current, proposal["adjustments"])
    targets = ", ".join(
        f"{a['axis']} {feedback.DIRECTION_LABELS[a['direction']]}"
        + (f"({feedback.STRENGTH_LABELS[a['strength']]})" if a.get("strength_explicit") else "")
        for a in proposal["adjustments"]
    )
    st.markdown(f"**AI가 읽은 요청:** {targets}")
    if plan["status"] == "no_change":
        st.info(f"ℹ️ {plan['message']}")
        return
    st.warning("⏳ **승인 대기 중인 제안** — 아직 적용되지 않았습니다. 아래에서 승인해야 반영됩니다.")
    st.dataframe(pd.DataFrame([
        {"지표": FEEDBACK_INDICATOR_LABELS[c], "현재 가중치": f"{plan['before'][c]:.1f}%",
         "변경될 가중치": f"{plan['after'][c]:.1f}%",
         "변화": f"{plan['after'][c] - plan['before'][c]:+.1f}%p"}
        for c in FEEDBACK_SLIDER_KEYS
    ]), hide_index=True, width="stretch")
    st.caption(
        "변경될 가중치는 AI가 아니라 Python 규칙으로 계산했습니다: "
        + " / ".join(f"{s['axis']} {s['rule']}" + (f" ({s['note']})" if s["note"] else "") for s in plan["steps"])
        + ". 강도 표현이 없으면 ×1.5, '조금'은 ×1.25, '많이·훨씬'은 ×2.0이며 같은 강도로 높였다 낮추면 원래 값으로 돌아옵니다."
    )
    col_approve, col_reject = st.columns(2)
    with col_approve:
        st.button("✅ 이 가중치로 다시 평가하기", type="primary", width="stretch",
                  on_click=_approve_nl_feedback_weights, args=(plan["after"], "nl_direction", plan["steps"]))
    with col_reject:
        st.button("❌ 무시하기", width="stretch", on_click=_reject_nl_feedback_proposal,
                  args=(plan["after"], "nl_direction", plan["steps"]))


FEEDBACK_SOURCE_LABELS = {"slider": "슬라이더", "nl_weights": "자연어(숫자)",
                          "nl_direction": "자연어(방향)", "reset": "되돌리기"}


def _render_feedback_history() -> None:
    """feedback_history(Memory): 승인·무시한 피드백과 그로 인한 후보 변화."""
    history = st.session_state.feedback_history
    if not history:
        return

    def fmt(weights):
        return ", ".join(f"{FEEDBACK_INDICATOR_LABELS[c].split(' ')[0]} {v:.0f}%" for c, v in weights.items() if v > 0)

    rows = []
    for i, h in enumerate(history, 1):
        changes = [f"{c['role_label']} {c['before'] or '-'}→{c['after'] or '-'}"
                   for c in h["candidate_changes"] if c["changed"]]
        rows.append({
            "#": i,
            "방식": FEEDBACK_SOURCE_LABELS.get(h["source"], h["source"]),
            "요청": h["text"] or "-",
            "대상 축·방향": ", ".join(
                f"{t['axis']} {feedback.DIRECTION_LABELS[t['direction']]}" for t in h["targets"]) or "-",
            "이전 가중치": fmt(h["before_weights"]),
            "변경 가중치": fmt(h["after_weights"]),
            "승인": "승인" if h["approved"] else "무시",
            "1위": f"{h['top_before']}→{h['top_after']}" if h["approved"] else "-",
            "후보 변화": ", ".join(changes) or ("변화 없음" if h["approved"] else "-"),
        })
    with st.expander(f"🧠 피드백 기록 ({len(history)}건) — 이번 세션에서 기억 중", expanded=False):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_nl_feedback_proposal(proposal: dict) -> None:
    """
    interpret_weight_feedback()의 반환값을 상태별로 보여준다. type=="set_weights"일
    때만 승인/무시 버튼이 뜨고, 승인 전에는 feedback_recommendation이 전혀 바뀌지
    않는다(요구사항: 사용자 승인 전까지 실제 결과 불변). 아직 승인하지 않은 제안임을
    "⏳ 승인 대기 중"으로 명확히 표시해, 아래 "현재 적용 중인 결과"와 혼동하지 않게 한다.
    """
    if proposal["status"] == "ollama_error":
        st.error(
            f"⚠️ {proposal['message']} 위 슬라이더로 직접 가중치를 조정하는 기능은 "
            "그대로 사용할 수 있습니다."
        )
        return
    if proposal["status"] == "invalid_response":
        st.warning(f"⚠️ AI 응답을 적용할 수 없습니다: {proposal['message']}")
        return

    if proposal["type"] == "unsupported":
        st.info(f"ℹ️ {proposal['message']}")
        return
    if proposal["type"] == "ask_clarification":
        st.info(f"🤔 {proposal['message']}")
        return
    if proposal["type"] == "adjust_direction":
        _render_direction_proposal(proposal)
        return
    if proposal["type"] == "reset":
        st.warning("⏳ **승인 대기 중인 제안** — 최초 추천 가중치로 되돌립니다.")
        st.button("✅ 최초 추천 기준으로 되돌리기", type="primary",
                  on_click=_reset_feedback_weights, args=(st.session_state.get("nl_feedback_proposal_text"),))
        return

    # type == "set_weights"
    weights = proposal["weights"]
    weight_sum = sum(weights.values())
    st.warning("⏳ **승인 대기 중인 제안** — 아직 적용되지 않았습니다. 아래에서 승인해야 반영됩니다.")
    preview_df = pd.DataFrame(
        [
            {
                "지표": FEEDBACK_INDICATOR_LABELS.get(code, code),
                "AI가 읽은 값": f"{value:g}%",
                "실제 적용될 정규화 가중치": f"{value / weight_sum * 100:.1f}%",
            }
            for code, value in weights.items()
        ]
    )
    st.dataframe(preview_df, hide_index=True, width="stretch")
    st.caption(
        "'AI가 읽은 값'은 요청하신 숫자 그대로입니다. 이미 합계가 100%에 가까워야만 "
        "승인 가능한 제안으로 올라오므로 두 값은 거의 같습니다."
    )

    col_approve, col_reject = st.columns(2)
    with col_approve:
        st.button(
            "✅ 이 가중치로 적용하기",
            type="primary",
            width="stretch",
            on_click=_approve_nl_feedback_weights,
            args=(weights,),
        )
    with col_reject:
        st.button("❌ 무시하기", width="stretch", on_click=_reject_nl_feedback_proposal, args=(weights,))


def _apply_slider_weights() -> None:
    """슬라이더 '다시 비교하기'의 on_click 콜백 - 다른 피드백과 같은 _apply_feedback() 한 경로로 재평가한다.
    콜백으로 처리해야 화면 위쪽의 결과가 같은 실행에서 바로 갱신된다."""
    weights = {code: float(st.session_state[key]) for code, key in FEEDBACK_SLIDER_KEYS.items()}
    _apply_feedback(weights, source="slider")


def render_feedback_section() -> None:
    """
    '조건 바꿔서 다시 보기' - 문장(AI 해석 + 승인) 또는 슬라이더로 교통/의료/생활편의 비율을 바꿔
    같은 비교 범위의 지역을 재평가한다. 재평가 결과는 화면 맨 위 결과 영역에 바로 반영된다.
    비율 정규화와 재계산은 analysis.feedback.reevaluate()가 결정적으로 수행하며, AI는 문장을
    비율 '제안'으로 해석만 할 뿐 점수를 계산하지 않는다.
    """
    st.divider()
    st.subheader("🔄 조건 바꿔서 다시 보기")
    st.caption(
        "원하는 방향을 문장으로 적어 보세요. 예: '병원을 더 중요하게', '교통은 조금 덜 중요하게', "
        f"'의료 80%, 교통 20%'. AI({llm.backend_label()})는 요청을 읽기만 하고, 바뀔 비율은 정해진 "
        "규칙으로 계산해 보여드립니다. 승인해야 적용됩니다."
    )
    nl_text = st.text_input(
        "어떻게 바꿔 볼까요?", key="nl_feedback_input", label_visibility="collapsed", max_chars=300,
        placeholder="예: 병원을 더 중요하게 봐줘",
    )
    if st.button("AI로 해석하기"):
        if not nl_text.strip():
            st.warning("문장을 입력해 주세요.")
        else:
            with st.spinner("AI가 요청을 해석하고 있습니다..."):
                st.session_state.nl_feedback_proposal = interpret_weight_feedback(nl_text)
            st.session_state.nl_feedback_proposal_text = nl_text

    # 제안을 받은 뒤 사용자가 입력창의 문장을 고쳤다면, 그 고친 문장을 다시 해석하지
    # 않고도 이전 제안이 그대로 남아 "새 요청"처럼 승인되는 걸 막는다. 승인 버튼은
    # 반드시 그 버튼이 뜬 시점의 문장과 지금 입력창 문장이 같을 때만 유효해야 한다.
    if (
        st.session_state.nl_feedback_proposal is not None
        and nl_text != st.session_state.get("nl_feedback_proposal_text")
    ):
        st.session_state.nl_feedback_proposal = None
        st.info("ℹ️ 입력하신 문장이 바뀌었습니다. 'AI로 해석하기'를 다시 눌러 주세요.")

    if st.session_state.nl_feedback_proposal is not None:
        _render_nl_feedback_proposal(st.session_state.nl_feedback_proposal)

    for code, key in FEEDBACK_SLIDER_KEYS.items():
        if key not in st.session_state:
            st.session_state[key] = st.session_state.feedback_initial_weights.get(code, 0.0)

    with st.expander("비율 직접 조정 (슬라이더)"):
        cols = st.columns(3)
        current_weights: dict[str, float] = {}
        for col, code in zip(cols, FEEDBACK_SLIDER_KEYS):
            with col:
                current_weights[code] = st.slider(
                    FEEDBACK_INDICATOR_LABELS[code], 0, 100, key=FEEDBACK_SLIDER_KEYS[code]
                )
        weight_sum = sum(current_weights.values())
        if weight_sum > 0:
            st.caption("적용될 비율: " + ", ".join(
                f"{AXIS_SHORT_LABELS[c]} {v / weight_sum * 100:.1f}%" for c, v in current_weights.items() if v > 0
            ))
        else:
            st.caption("⚠️ 비율이 전부 0입니다. 하나 이상 0보다 크게 설정해야 다시 비교할 수 있습니다.")
        col_recompute, col_reset = st.columns(2)
        with col_recompute:
            st.button("다시 비교하기", type="primary", width="stretch", on_click=_apply_slider_weights)
        with col_reset:
            st.button("처음 조건으로 되돌리기", on_click=_reset_feedback_weights, args=(None,), width="stretch")

    _render_feedback_history()

    feedback_result = st.session_state.feedback_recommendation
    if feedback_result is not None and feedback_result.get("status") == "no_usable_conditions":
        st.warning(f"⚠️ {feedback_result['message']} 위 결과는 이전에 적용한 결과 그대로입니다.")


st.set_page_config(page_title="경남 이주자 생활권 탐색 AI", page_icon="🏡")

st.title("🏡 경남 이주자 맞춤형 생활권 탐색 AI")
st.caption(
    "경상남도 22개 지역(창원시 5개 구·시 7곳·군 10곳)을 내 생활 조건(교통·의료·생활편의)으로 비교하고, "
    "AI Agent가 성격이 다른 정착 후보를 "
    "골라 근거와 함께 설명합니다. 공공데이터로 계산한 결과만 보여주며, 없는 데이터는 '미확보'로 표시합니다."
)

if "stage" not in st.session_state:
    st.session_state.stage = "input"
if "initial_input" not in st.session_state:
    st.session_state.initial_input = {}
if "followup_questions" not in st.session_state:
    st.session_state.followup_questions = []
if "followup_answers" not in st.session_state:
    st.session_state.followup_answers = {}
if "initial_recommendation" not in st.session_state:
    st.session_state.initial_recommendation = None
if "feedback_initial_weights" not in st.session_state:
    st.session_state.feedback_initial_weights = {}
if "feedback_recommendation" not in st.session_state:
    st.session_state.feedback_recommendation = None
if "nl_feedback_proposal" not in st.session_state:
    st.session_state.nl_feedback_proposal = None
if "nl_feedback_proposal_text" not in st.session_state:
    st.session_state.nl_feedback_proposal_text = None
if "feedback_explanation" not in st.session_state:
    st.session_state.feedback_explanation = None  # 피드백 재평가 뒤 explain_candidates() 결과
if "feedback_history" not in st.session_state:
    st.session_state.feedback_history = []  # 승인·무시한 피드백 기록(analysis.feedback.history_entry)
if "weight_question_plan" not in st.session_state:
    st.session_state.weight_question_plan = None
if "weight_interpretation" not in st.session_state:
    st.session_state.weight_interpretation = None
if "weight_interpretation_source" not in st.session_state:
    # "initial_extra_request"(최초 입력의 '추가 요청사항'에서 비율을 읽음) 또는
    # "followup_answer"(AI 추가질문의 가중치 확인 질문에 답함) 또는 None. 두 출처가
    # 동시에 weight_interpretation을 채우는 경로가 구조적으로 없으므로(아래
    # plan_followup_questions 호출부 참고) 가중치 충돌 걱정 없이 "지금 보고 있는
    # 해석 결과가 어디서 왔는지"만 추적하면 된다.
    st.session_state.weight_interpretation_source = None
if "weight_confirmation" not in st.session_state:
    st.session_state.weight_confirmation = None
if "followup_ai_error" not in st.session_state:
    # AI 추가질문(가중치 확인 질문 제외) 생성이 Ollama 실패로 불가능했을 때의 사유.
    # 가중치 확인 질문(결정적)이나 추가 요청사항 해석과는 별개 경로라 이 실패가
    # 나머지 흐름을 막지 않는다(화면에는 안내만 한다).
    st.session_state.followup_ai_error = None
if "agent_execution_log" not in st.session_state:
    # _finalize_initial_recommendation()이 run_agent_plan()을 호출할 때마다 그
    # 결과(계획/실행 로그/오류)를 통째로 저장한다. "완료" 화면이 다시 렌더링될 때마다
    # Agent를 재실행하지 않도록, 이 값은 _finalize_initial_recommendation() 호출
    # 시점에만 갱신한다.
    st.session_state.agent_execution_log = None


def reset_all():
    st.session_state.stage = "input"
    # 새 검색은 새 익명 세션으로 센다(동의도 다시 받는다).
    for key in ("demand_session_id", "demand_saved_signature", "demand_consent"):
        st.session_state.pop(key, None)
    st.session_state.initial_input = {}
    st.session_state.followup_questions = []
    st.session_state.followup_answers = {}
    st.session_state.initial_recommendation = None
    st.session_state.feedback_initial_weights = {}
    st.session_state.feedback_recommendation = None
    st.session_state.nl_feedback_proposal = None
    st.session_state.nl_feedback_proposal_text = None
    st.session_state.feedback_history = []
    st.session_state.feedback_explanation = None
    st.session_state.weight_question_plan = None
    st.session_state.weight_interpretation = None
    st.session_state.weight_interpretation_source = None
    st.session_state.weight_confirmation = None
    st.session_state.followup_ai_error = None
    st.session_state.agent_execution_log = None
    for key in FEEDBACK_SLIDER_KEYS.values():
        st.session_state.pop(key, None)
    st.session_state.pop("nl_feedback_input", None)
    st.session_state.pop("focus_region", None)


def _finalize_initial_recommendation(confirmed_weights: dict[str, float] | None = None) -> None:
    """
    stage가 'done'으로 넘어가는 바로 그 순간에 딱 한 번만 호출한다. 이후 피드백
    슬라이더를 아무리 조정해도 이 결과(initial_recommendation)는 다시 계산되지
    않고 그대로 '최초 추천'으로 남아, 변경 전후 비교의 기준점 역할을 한다.

    confirmed_weights가 주어지면(AI 추가질문의 가중치 확인 질문에 사용자가 답하고
    승인한 경우) 그 가중치를 "승인된 가중치"로 agent.planner.run_agent_plan()에
    넘긴다. None이면(가중치 확인 질문이 없었거나, 답변을 적용하지 못해 동일
    가중치로 진행하기로 한 경우) run_agent_plan()이 내부적으로 기존 compute_region_
    scores()와 동일한 조건별 동일 가중치를 유도해서 쓴다 - 최종 계산 결과는 이전과
    같다. run_agent_plan()은 AI가 세운 분석 계획을 검증된 도구로 실행하거나(Ollama
    실패/검증 실패 시) 기존과 동일한 기본 절차로 폴백하며, 실행 기록은
    agent_execution_log에 저장해 완료 화면의 '🤖 Agent가 실제로 한 일'에서 보여준다.
    """
    with st.spinner("Agent가 분석 계획을 세우고 실제 데이터로 점수를 계산하는 중입니다..."):
        run_result = run_agent_plan(
            selected_conditions=st.session_state.initial_input.get("중요 생활조건") or [],
            confirmed_weights=confirmed_weights,
            desired_region=st.session_state.initial_input.get("희망지역", ""),
            candidate_count=st.session_state.initial_input.get("원하는 후보 개수") or 3,
            unscored_inputs=_unscored_inputs(st.session_state.initial_input),
        )
    st.session_state.agent_execution_log = run_result
    st.session_state.initial_recommendation = run_result["score_result"]
    st.session_state.feedback_initial_weights = scoring.initial_feedback_weights(
        st.session_state.initial_recommendation
    )
    st.session_state.feedback_recommendation = None
    st.session_state.nl_feedback_proposal = None
    st.session_state.nl_feedback_proposal_text = None
    st.session_state.feedback_history = []
    st.session_state.feedback_explanation = None
    for code, key in FEEDBACK_SLIDER_KEYS.items():
        st.session_state[key] = st.session_state.feedback_initial_weights.get(code, 0.0)


def _apply_weight_confirmation(
    confirmed_weights: dict[str, float] | None,
    source: str,
    reason: str,
    ai_confirmed_weights: dict[str, float] | None = None,
) -> None:
    """
    'weight_confirm' 단계에서 사용자가 최종 선택(승인 / AI 제안 무시 / 동일 가중치로
    진행)을 내렸을 때 호출한다. weight_confirmation에 "무엇이 왜 적용됐는지"
    기록을 남기고, _finalize_initial_recommendation()으로 실제 최초 추천을
    계산한 뒤 stage를 'done'으로 넘긴다.

    confirmed_weights가 None이면(승인하지 않음/답변 적용 불가) 기존 조건 기준
    동일 가중치 계산으로 안전하게 폴백한다 - 근거 없는 가중치를 만들어내지 않는다.
    """
    weight_question = st.session_state.weight_question_plan
    interp_source = st.session_state.weight_interpretation_source
    if interp_source == "initial_extra_request":
        answer_text = st.session_state.initial_input.get("추가 요청사항", "")
    elif weight_question:
        answer_text = st.session_state.followup_answers.get(weight_question["text"], "")
    else:
        answer_text = ""
    st.session_state.weight_confirmation = {
        "asked": True,
        "answer_text": answer_text,
        "answer_source": interp_source,
        "ai_confirmed_weights": ai_confirmed_weights,
        "applied_weights": confirmed_weights,
        "source": source,
        "reason": reason,
    }
    st.session_state.stage = "done"
    _finalize_initial_recommendation(confirmed_weights)


# 1단계: 최초 입력 ------------------------------------------------------
if st.session_state.stage == "input":
    # weight_confirm 단계에서 "다시 답변 입력하기"로 돌아온 경우 이전 입력을 그대로
    # 복원한다(처음 진입 시에는 initial_input이 비어 있어 전부 기본값으로 보인다).
    prev = st.session_state.initial_input
    prev_car_options = ["보유", "미보유"]
    prev_conditions_options = ["교통", "의료", "교육", "생활편의(마트/편의점)", "안전", "자연환경", "문화시설"]

    with st.form("initial_input_form"):
        region = st.selectbox(
            "비교 범위 (경상남도)",
            REGION_OPTIONS,
            index=_option_index(REGION_OPTIONS, prev.get("희망지역")),
            help="규모가 비슷한 같은 유형 지역끼리만 비교합니다(창원시 구끼리 / 시끼리 / 군끼리). 유형이 다른 "
                 "지역을 한 표에서 점수로 비교하면 큰 도시나 군 지역으로 결과가 크게 쏠리기 때문입니다.",
        )
        workplace_choice = st.selectbox(
            "직장 또는 학교 위치",
            WORKPLACE_OPTIONS,
            index=_option_index(WORKPLACE_OPTIONS, prev.get("직장/학교 위치"), NOT_SELECTED),
            help=REFERENCE_ONLY_HELP,
        )
        has_car = st.radio(
            "자가용 보유 여부",
            prev_car_options,
            horizontal=True,
            index=prev_car_options.index(prev["자가용 보유 여부"])
            if prev.get("자가용 보유 여부") in prev_car_options
            else 0,
        )
        budget_choice = st.selectbox(
            "주거비 예산",
            HOUSING_BUDGET_OPTIONS,
            index=_option_index(HOUSING_BUDGET_OPTIONS, prev.get("주거비 예산"), NOT_SELECTED),
            help=REFERENCE_ONLY_HELP,
        )
        important_conditions = st.multiselect(
            "중요하게 생각하는 생활 조건",
            prev_conditions_options,
            help="지금 점수에 반영되는 항목은 교통·의료·생활편의입니다. 나머지는 데이터를 확보하지 못해 "
                 "선택해도 점수에 들어가지 않습니다. 아무것도 고르지 않으면 세 항목을 같은 비율로 비교합니다.",
            default=[c for c in (prev.get("중요 생활조건") or []) if c in prev_conditions_options],
        )
        candidate_count = st.number_input(
            "원하는 후보 지역 개수",
            min_value=1,
            max_value=MAX_CANDIDATE_COUNT,
            value=int(prev.get("원하는 후보 개수") or 3),
            step=1,
        )
        extra_request = st.text_area(
            "추가 요청사항 (선택, 예: '교통 70%, 의료 30%'처럼 비율을 적으면 AI가 해석해 "
            "최초 추천에 반영할 수 있습니다)",
            value=prev.get("추가 요청사항", ""),
            max_chars=500,
        )

        submitted = st.form_submit_button("다음 단계로")

    if submitted:
        workplace = "" if workplace_choice == NOT_SELECTED else workplace_choice
        budget = "" if budget_choice == NOT_SELECTED else budget_choice
        region_text = region.strip()
        if not region_text:
            st.error("희망 지역을 입력해 주세요.")
        elif not is_supported_region(region_text):
            st.error(
                f"'{region_text}'은(는) 현재 지원 범위 밖입니다. 이 서비스는 현재 "
                "**경상남도 22개 지역(창원시 5개 구·시 7곳·군 10곳)**만 지원합니다. "
                "비교 범위를 다시 골라 주세요."
            )
        else:
            st.session_state.initial_input = {
                "희망지역": region,
                "직장/학교 위치": workplace,
                "자가용 보유 여부": has_car,
                "주거비 예산": budget,
                "중요 생활조건": important_conditions,
                "원하는 후보 개수": candidate_count,
                "추가 요청사항": extra_request,
            }

            # plan_followup_questions()는 Ollama 호출(추가질문 생성)이 실패해도 예외를
            # 던지지 않는다 - 가중치 확인 질문(결정적)과 추가 요청사항 비율 해석은
            # 별도 경로라 AI 추가질문 생성 실패가 전체 흐름을 막지 않는다.
            with st.spinner("AI가 입력 정보를 분석하고 있습니다..."):
                plan = plan_followup_questions(st.session_state.initial_input)

            st.session_state.followup_questions = plan["questions"]
            st.session_state.weight_question_plan = plan["weight_question"]
            st.session_state.followup_ai_error = plan["ai_questions_error"]

            if plan["initial_weight_interpretation"] is not None:
                st.session_state.weight_interpretation = plan["initial_weight_interpretation"]
                st.session_state.weight_interpretation_source = "initial_extra_request"
                st.session_state.stage = "followup" if plan["questions"] else "weight_confirm"
            elif plan["questions"]:
                st.session_state.stage = "followup"
            else:
                st.session_state.weight_confirmation = {
                    "asked": False, "answer_text": None, "answer_source": None,
                    "ai_confirmed_weights": None, "applied_weights": None, "source": "not_asked",
                    "reason": "확인할 생활조건 우선순위 질문이 없었습니다.",
                }
                st.session_state.stage = "done"
                _finalize_initial_recommendation()
            st.rerun()

# 2단계: AI 추가 질문 ----------------------------------------------------
elif st.session_state.stage == "followup":
    st.subheader("📝 AI의 추가 질문")
    weight_question = st.session_state.weight_question_plan
    initial_ratio_pending = (
        weight_question is None and st.session_state.weight_interpretation_source == "initial_extra_request"
    )
    if st.session_state.followup_ai_error:
        st.info(
            f"ℹ️ AI 추가질문 생성 중 문제가 발생해 일부 질문은 만들지 못했습니다"
            f"({st.session_state.followup_ai_error}). 아래 남은 항목만으로 계속 "
            "진행할 수 있습니다."
        )
    if weight_question:
        st.write(
            "선택하신 생활조건 중 비율이 아직 명확하지 않은 항목이 있어 확인 질문을 "
            "포함했습니다. ⚖️ 표시가 붙은 질문에 답하고 다음 화면에서 승인하시면 "
            "**실제 최초 추천 계산의 가중치**로 사용됩니다. 그 외 질문의 답변은 "
            "참고용으로만 저장되며, 현재 구현상 시설 수 기반 비교 점수 계산에는 "
            "반영되지 않습니다."
        )
    elif initial_ratio_pending:
        st.write(
            "입력하신 '추가 요청사항'의 비율은 다음 화면에서 AI 해석 결과를 보여드리고 "
            "승인을 받습니다. 아래 질문들의 답변은 참고용으로만 저장되며, 현재 구현상 "
            "시설 수 기반 비교 점수 계산에는 반영되지 않습니다."
        )
    else:
        st.write(
            "거주 조건을 추가로 파악하기 위한 질문입니다. 답변은 저장되어 아래 완료 "
            "화면에서 확인할 수 있지만, 현재 구현상 시설 수 기반 비교 점수 계산에는 "
            "반영되지 않습니다."
        )

    with st.form("followup_form"):
        answers = {}
        for i, question in enumerate(st.session_state.followup_questions, start=1):
            if weight_question and question == weight_question["text"]:
                st.caption("⚖️ 이 질문의 답변은 다음 화면에서 승인하시면 실제 추천 계산의 가중치로 사용됩니다.")
            answers[question] = st.text_input(f"{i}. {question}", key=f"followup_{i}", max_chars=300)

        followup_submitted = st.form_submit_button("답변 제출")

    if followup_submitted:
        st.session_state.followup_answers = answers
        if weight_question:
            weight_answer = answers.get(weight_question["text"], "")
            with st.spinner("AI가 답변을 해석하고 있습니다..."):
                st.session_state.weight_interpretation = interpret_weight_feedback(weight_answer)
            st.session_state.weight_interpretation_source = "followup_answer"
            st.session_state.stage = "weight_confirm"
        elif initial_ratio_pending:
            # 최초 입력의 '추가 요청사항'에서 이미 해석해 둔 가중치가 있다 - 그대로
            # weight_confirm 단계로 넘어가 승인받는다(여기서 다시 해석하지 않는다).
            st.session_state.stage = "weight_confirm"
        else:
            st.session_state.weight_confirmation = {
                "asked": False, "answer_text": None, "answer_source": None,
                "ai_confirmed_weights": None, "applied_weights": None, "source": "not_asked",
                "reason": "확인할 생활조건 우선순위 질문이 없었습니다.",
            }
            st.session_state.stage = "done"
            _finalize_initial_recommendation()
        st.rerun()

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()

# 2.5단계: 가중치(최초 입력의 추가 요청사항 또는 AI 추가질문 답변) 승인 -------
elif st.session_state.stage == "weight_confirm":
    st.subheader("⚖️ 가중치 확인")
    weight_question = st.session_state.weight_question_plan
    interpretation = st.session_state.weight_interpretation
    source = st.session_state.weight_interpretation_source

    if source == "initial_extra_request":
        answer_text = st.session_state.initial_input.get("추가 요청사항", "")
        origin_label = "최초 입력의 '추가 요청사항'"
        retry_stage = "input"
    else:
        answer_text = st.session_state.followup_answers.get(weight_question["text"], "") if weight_question else ""
        origin_label = "AI 추가질문 답변"
        retry_stage = "followup"

    def _retry() -> None:
        st.session_state.weight_interpretation = None
        st.session_state.weight_interpretation_source = None
        st.session_state.stage = retry_stage

    st.write(
        f"{origin_label}(\"{answer_text}\")을 AI가 해석한 결과입니다. "
        "승인하시면 **이 가중치로 최초 추천을 계산**합니다. "
        "승인하지 않으면 선택하신 조건에 동일 가중치를 적용합니다."
    )

    if interpretation["status"] == "ollama_error":
        st.error(f"⚠️ {interpretation['message']}")
        if st.button("동일 가중치로 진행하기", type="primary"):
            _apply_weight_confirmation(
                None, "equal_fallback", f"AI 연결에 실패해 답변을 해석하지 못했습니다: {interpretation['message']}"
            )
            st.rerun()

    elif interpretation["status"] == "invalid_response":
        st.warning(f"⚠️ 답변을 해석하지 못했습니다: {interpretation['message']}")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("다시 답변 입력하기"):
                _retry()
                st.rerun()
        with col_b:
            if st.button("동일 가중치로 진행하기", type="primary"):
                _apply_weight_confirmation(
                    None, "equal_fallback", f"답변을 해석하지 못했습니다: {interpretation['message']}"
                )
                st.rerun()

    elif interpretation["type"] == "unsupported":
        st.info(f"ℹ️ {interpretation['message']}")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("다시 답변 입력하기"):
                _retry()
                st.rerun()
        with col_b:
            if st.button("동일 가중치로 진행하기", type="primary"):
                _apply_weight_confirmation(None, "equal_fallback", interpretation["message"])
                st.rerun()

    elif interpretation["type"] == "ask_clarification":
        st.info(f"🤔 {interpretation['message']}")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("다시 답변 입력하기"):
                _retry()
                st.rerun()
        with col_b:
            if st.button("동일 가중치로 진행하기", type="primary"):
                _apply_weight_confirmation(None, "equal_fallback", interpretation["message"])
                st.rerun()

    else:  # interpretation["type"] == "set_weights"
        weights = interpretation["weights"]
        weight_sum = sum(weights.values())
        preview_df = pd.DataFrame(
            [
                {
                    "조건": FEEDBACK_INDICATOR_LABELS.get(code, code),
                    "답변에서 읽은 값": f"{value:g}%",
                    "실제 적용될 가중치": f"{value / weight_sum * 100:.1f}%",
                }
                for code, value in weights.items()
            ]
        )
        st.dataframe(preview_df, hide_index=True, width="stretch")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("✅ 이 가중치로 최초 추천 계산", type="primary"):
                _apply_weight_confirmation(
                    weights, "ai_approved", f"{origin_label}의 비율을 승인해 가중치로 사용했습니다.",
                    ai_confirmed_weights=weights,
                )
                st.rerun()
        with col_b:
            if st.button("❌ 무시하고 동일 가중치로 진행"):
                _apply_weight_confirmation(
                    None, "ai_rejected_by_user",
                    "AI가 해석한 가중치를 적용하지 않기로 선택해 동일 가중치를 사용했습니다.",
                    ai_confirmed_weights=weights,
                )
                st.rerun()

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()

# 3단계: 완료 - 결론부터 보여주는 추천 결과 화면 -------------------------------
elif st.session_state.stage == "done":
    if st.session_state.followup_ai_error:
        st.caption(
            f"ℹ️ AI 추가질문 생성 중 문제가 있어 일부 질문은 만들지 못했습니다"
            f"({st.session_state.followup_ai_error}). 추천은 확보된 데이터로 정상 진행했습니다."
        )
    _render_input_summary()
    render_result_view()
    render_feedback_section()
    render_detail_sections(_current_view_result())

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()
