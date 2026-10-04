# Streamlit 실행 진입점
import pandas as pd
import streamlit as st

from agent import llm
from agent.ollama_agent import interpret_weight_feedback, plan_followup_questions
from agent.planner import TOOL_LABELS, run_agent_plan
from agent.planner import OLLAMA_MODEL as PLANNER_MODEL
from agent.planner_loop import REVIEW_TOOL_LABELS, explain_candidates
from analysis import feedback, scoring
from analysis.candidates import build_candidate_set
from services.region_data import get_all_changwon_regions, is_supported_region
from services.schools import SCHOOL_REFERENCE_LIMITATIONS, SCHOOL_REFERENCE_TITLE, load_school_reference

CHART_ACCENT_COLOR = "#2a78d6"
MAX_CANDIDATE_COUNT = 5  # 현재 지원 지역(창원시 5개 구) 수와 동일하게 맞춤

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
SPECIFIC_DISTRICT_NAMES = ["의창구", "성산구", "마산합포구", "마산회원구", "진해구"]

# 최초 입력 폼 선택지. 희망지역은 지원 범위(창원시 5개 구) 안에서만 고르게 해 미지원 지역
# 입력 자체가 생기지 않게 한다. 직장/학교 위치·주거비 예산은 아직 대응 지표(이동시간,
# 실거래 주거비)가 없어 점수 계산에 쓰지 않고 참고용으로만 저장한다 - "선택 안 함"은
# 기존 자유입력의 빈 값과 같게 ""로 저장한다.
NOT_SELECTED = "선택 안 함"
REGION_OPTIONS = ["창원시 전체"] + [f"창원시 {d}" for d in SPECIFIC_DISTRICT_NAMES]
WORKPLACE_OPTIONS = [NOT_SELECTED] + [f"창원시 {d}" for d in SPECIFIC_DISTRICT_NAMES] + [
    "창원시 외 경남 지역",
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

# compute_region_scores_from_weights()로 계산된 결과(AI 가중치 확인 승인 포함)는
# used_conditions 항목에 "condition"(원래 선택한 생활조건 이름)이 없다 - 그 경로는
# 지표 가중치만 받으므로 구조적으로 알 수 없다(analysis/scoring.py 설계). 화면
# 표시용으로만 indicator_code에서 역으로 조건 이름을 찾는다.
INDICATOR_CODE_TO_CONDITION = {code: cond for cond, code in scoring.CONDITION_TO_INDICATOR_CODE.items()}

# 화면에 표시할 간결한 주의사항. CSV의 note 컬럼(검증 정보 전체, 예외 ID 목록 등)은
# data/region_indicators.csv에 그대로 보존되며, 여기서는 화면용 짧은 문구로만 대체한다.
SHORT_NOTES = {
    "hospital_count": "의원·치과의원·한의원 등이 포함된 등록 의료기관 수입니다.",
    "bus_stop_count": "공식 행정경계 내부 좌표 기준 집계이며, 버스정류장 개수는 실제 통근시간을 의미하지 않습니다.",
    "convenience_store_count": "상가정보에 등록된 업소 기준 집계로, 동일 주소 중복 등록 등으로 실제 영업 매장 수와 다를 수 있습니다.",
}


def render_indicator_category(category: str, section_title: str) -> None:
    """
    창원시 5개 구 전체(get_all_changwon_regions)를 category 기준으로 조회해
    지표별 표 + 막대그래프를 렌더링한다. CSV에 새 category/지표가 추가되면
    (교통/생활편의/주거비 등) 이 함수를 그대로 재사용해 같은 화면을 확장할 수 있다.

    - 5개 구 전부 미확보인 지표는 긴 표 대신 "지표명 — 미확보" 한 줄 + 접기 영역으로
      간결하게 표시한다(수치를 0으로 바꾸거나 숨기지 않는다 - 접으면 볼 수 있다).
    - 일부만 확보된 지표(향후 생길 수 있음)는 기존처럼 표 전체를 보여주되, 확보 행만
      그래프에 반영한다.
    - 출처·기준일은 표를 좁게 유지하기 위해 별도 접기 영역으로 뺐다(원본 CSV 값은
      그대로 보존, 표시 위치만 바뀜).
    """
    st.subheader(section_title)
    regions = get_all_changwon_regions(categories=[category])

    indicator_codes: list[str] = []
    for region in regions:
        for indicator in region["categories"].get(category, []):
            if indicator["indicator_code"] not in indicator_codes:
                indicator_codes.append(indicator["indicator_code"])

    if not indicator_codes:
        st.info("아직 등록된 지표가 없습니다.")
        return

    for code in indicator_codes:
        rows = []
        for region in regions:
            indicator = next(
                (i for i in region["categories"].get(category, []) if i["indicator_code"] == code),
                None,
            )
            if indicator is None:
                continue
            rows.append(
                {
                    "구": region["region_name"],
                    "값": indicator["value"] if indicator["value"] is not None else "미확보",
                    "단위": indicator["unit"] or "-",
                    "상태": indicator["data_status"],
                    "출처": indicator["source"] or "-",
                    "기준일": indicator["reference_date"] or "-",
                }
            )

        indicator_name = next(
            (
                i["indicator_name"]
                for region in regions
                for i in region["categories"].get(category, [])
                if i["indicator_code"] == code
            ),
            code,
        )
        note_text = SHORT_NOTES.get(code) or next(
            (
                i["note"]
                for region in regions
                for i in region["categories"].get(category, [])
                if i["indicator_code"] == code and i["note"]
            ),
            None,
        )

        rows_df = pd.DataFrame(rows)
        confirmed_rows = [row for row in rows if row["상태"] == "확보"]

        if not confirmed_rows:
            # 5개 구 전체 미확보 - 긴 표 대신 한 줄 + 접기 영역
            st.markdown(f"**{indicator_name}** — 미확보")
            with st.expander("구별 상태 보기"):
                st.dataframe(rows_df[["구", "상태"]], hide_index=True, width="stretch")
            if note_text:
                st.caption(f"ℹ️ {note_text}")
            continue

        st.markdown(f"**{indicator_name}**")
        st.dataframe(rows_df[["구", "값", "단위", "상태"]], hide_index=True, width="stretch")

        if note_text:
            st.caption(f"ℹ️ {note_text}")

        with st.expander("출처 및 기준일 보기"):
            st.dataframe(rows_df[["구", "출처", "기준일"]], hide_index=True, width="stretch")

        chart_df = pd.DataFrame(
            [{"구": row["구"], "값": float(row["값"])} for row in confirmed_rows]
        ).set_index("구")
        st.bar_chart(chart_df, color=CHART_ACCENT_COLOR)

        missing_regions = [row["구"] for row in rows if row["상태"] != "확보"]
        if missing_regions:
            st.caption(
                f"미확보 구: {', '.join(missing_regions)} "
                "(0이 아니라 '미확보'로 표시되며 그래프에서는 제외됩니다)"
            )


REFERENCE_INDICATOR_ORDER = ["hospital_count", "bus_stop_count", "convenience_store_count"]

UNAVAILABLE_DATA_NOTICE = (
    "실제 대중교통 소요시간, 월세·전세 가격, 응급실 운영 병원 수, 대형마트 수, "
    "교육·안전·자연환경·문화시설 지표는 아직 확보되지 않아 추천 계산에 사용되지 않습니다. "
    "(구별 초·중·고 학교 수는 결과 아래 '교육시설 수 참고정보'로만 보여 주며 점수에는 쓰지 않습니다.)"
)

RELATIVE_SCORE_CAVEAT = (
    "이 점수는 인구·면적이나 실제 접근성을 보정하지 않은, 시설 수 기준 상대 비교 "
    "점수입니다(100점 = 해당 지표에서 5개 구 중 수치가 가장 높다는 뜻일 뿐, "
    "완벽한 정주환경을 의미하지 않습니다)."
)


def _render_school_reference() -> None:
    """구별 초·중·고 학교 수 참고정보. 추천 점수·후보·Critic과 무관하게 읽기만 한다(DEC-19)."""
    reference = load_school_reference()
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


def _render_top_candidates(result: dict, heading: str = "추천 후보지역") -> None:
    """
    result["top_candidates"]를 "N위 - 구 이름 - 종합점수 - 실제 수치 - 계산 근거"
    형태로 렌더링한다. 최초 추천과 피드백(가중치 조정) 추천 둘 다 이 함수를 쓴다.
    """
    st.markdown(f"**{heading} (요청하신 {len(result['top_candidates'])}개)**")
    for row in result["top_candidates"]:
        tie_label = " (동점)" if row["tied"] else ""
        st.markdown(f"#### {row['rank']}위 — {row['region_name']} · 종합점수 {row['total_score']:.1f}점{tie_label}")

        ref_rows = [
            {"지표": info["indicator_name"], "실제 수치": f"{info['raw_value']:.0f}개"}
            for code, info in (
                (c, row["reference_indicators"].get(c)) for c in REFERENCE_INDICATOR_ORDER
            )
            if info is not None
        ]
        if ref_rows:
            st.dataframe(pd.DataFrame(ref_rows), hide_index=True, width="stretch")

        explanation_parts = [
            f"{uc['indicator_name']} {row['component_scores'][uc['indicator_code']]['raw_value']:.0f}개"
            f"(정규화 {row['component_scores'][uc['indicator_code']]['normalized_score']:.1f}점 "
            f"× 가중치 {uc['weight'] * 100:.0f}%)"
            for uc in result["used_conditions"]
        ]
        st.caption("계산 근거: " + " + ".join(explanation_parts) + f" = {row['total_score']:.1f}점")


def _unscored_inputs(initial_input: dict) -> list[str]:
    """입력은 받았지만 대응 데이터가 없어 점수에 반영하지 못한 항목 이름(Critic 커버리지 점검용)."""
    return [name for name in ("직장/학교 위치", "주거비 예산") if (initial_input or {}).get(name)]


def _render_candidate_set(candidate_set: dict, heading: str = "🧭 정착 후보군 — 성격이 다른 후보") -> None:
    """
    analysis.candidates.build_candidate_set() 결과(후보 역할 + Critic 점검)를 보여준다.
    후보와 점검 결과는 Python이 점수 결과에서 결정적으로 계산한 것이며 AI가 만들지 않는다.
    """
    if candidate_set.get("status") != "ok":
        return
    st.markdown(f"### {heading}")
    st.caption(
        "종합점수 1위 하나만 보지 않도록, 같은 점수 결과에서 성격이 다른 후보를 함께 보여줍니다. "
        "후보 선정과 아래 점검은 Python이 계산 결과로만 수행했습니다."
    )
    for role in candidate_set["roles"]:
        if role["status"] != "ok":
            st.caption(f"**{role['role_label']}** — 산출 불가: {role['reason']}")
            continue
        st.markdown(
            f"**{role['role_label']} · {role['region_name']}** "
            f"(종합 {role['total_score']:.1f}점, {role['rank']}위) — {role['reason']}"
        )
        st.caption(
            f"강점: {', '.join(role['strengths']) or '없음'} · 약점: {', '.join(role['weaknesses']) or '없음'} · "
            + " / ".join(
                f"{p['axis']} {p['raw_value']:.0f}개({p['normalized_score']:.1f}점, {p['axis_rank']}위)"
                for p in role["axis_profile"]
            )
        )
    if candidate_set["pareto"]:
        st.caption(f"어느 축에서도 다른 구에 완전히 뒤지지 않는 구: {', '.join(candidate_set['pareto'])}")

    critic = candidate_set["critic"]
    with st.expander(f"🔎 Critic 점검 (경고 {critic['warning_count']}건)", expanded=critic["warning_count"] > 0):
        for check in critic["checks"]:
            if check["level"] == "warning":
                st.warning(f"⚠️ {check['message']}")
            else:
                st.caption(f"ℹ️ {check['message']}")
        st.dataframe(
            pd.DataFrame([{"평가축": a["axis"], "상태": a["status"]} for a in candidate_set["axes"]]),
            hide_index=True, width="stretch",
        )


def render_agent_execution_log() -> None:
    """
    'AI 분석 실행 과정 보기' 접기 영역의 내용을 렌더링한다.
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


_REVIEW_ACTION_LABELS = {"answer": "설명 작성", "call_tools": "추가 도구 요청", "error": "판단 실패"}


def _render_review_steps(log: dict) -> None:
    """점수 계산 후 AI가 결과를 보고 판단한 과정(관찰 -> 판단 -> 행동). 표시 전용."""
    steps = log.get("agent_steps") or []
    st.markdown("**결과 확인 후 AI 판단 (관찰 → 판단 → 행동)**")
    if not steps:
        st.caption("없음 — AI 계획으로 계산하지 않아 결과 검토 단계를 건너뛰었습니다."
                   if log["mode"] != "ai_planned" else "없음")
        return
    for step in steps:
        line = f"· {step['round']}회차: {_REVIEW_ACTION_LABELS.get(step['action'], step['action'])}"
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
        st.success("Python이 확정 사실로 만든 설명을 AI가 자연스럽게 다듬은 문장입니다 · "
                   "새로운 사실·숫자·후보 역할이 추가되지 않았는지 검증했습니다")
        st.write(final["text"])
        with st.expander("Python 기본 설명(검증 기준) 보기"):
            st.markdown(final["deterministic_text"])
        return
    if final["source"] == "deterministic":
        reason = f" (AI 다듬기를 쓰지 않은 이유: {final['rejected_reason']})" if final.get("rejected_reason") else ""
        st.info(f"📋 Python이 확정 사실(후보 역할·강점·약점·평가축·한계)로 작성한 설명입니다{reason}")
        st.markdown(final["text"])
        return
    if final["source"] == "ai_verified":
        st.success("AI가 계산 결과와 Agent가 확정한 후보·Critic 결과를 보고 작성한 설명입니다 · "
                   "설명 속 숫자와 후보 역할을 실제 결과와 대조해 검증했습니다")
        st.write(final["text"])
    else:
        reason = f" (AI 설명을 쓰지 않은 이유: {final['rejected_reason']})" if final.get("rejected_reason") else ""
        st.info(f"📋 계산 결과 요약 · Python이 실제 계산 결과로 작성했습니다{reason}")
        st.text(final["text"])


def render_ai_recommendation_explanation() -> None:
    """AI가 점수 계산 결과를 보고 작성한 설명(숫자 검증 통과분)과 가중치 가정 시뮬레이션 결과.
    실제 추천 순위는 위의 결과(승인된 가중치) 그대로이며, 여기 내용은 참고 정보다."""
    log = st.session_state.get("agent_execution_log")
    if not log or not log.get("final_answer"):
        return
    _render_final_answer(log["final_answer"], "### 🤖 AI의 추천 결과 설명")

    for sim in log.get("what_if_results") or []:
        weights = ", ".join(f"{FEEDBACK_INDICATOR_LABELS.get(c, c)} {v}%" for c, v in sim["weights_percent"].items())
        with st.expander(f"🔍 AI가 확인한 가정: 가중치를 {weights}로 바꾼다면 (실제 추천에는 미적용)"):
            st.dataframe(
                pd.DataFrame([{"순위": r["rank"], "구": r["region_name"], "종합점수(가정)": r["total_score"]}
                              for r in sim["ranking"]]),
                hide_index=True, width="stretch",
            )
            st.caption("이 가정을 실제로 적용하려면 아래 '🔄 조건 조정 후 다시 비교하기'에서 가중치를 바꿔 승인하세요.")


def render_recommendation_section() -> None:
    """
    사용자가 선택한 '중요 생활조건' 중 실제 데이터가 확보된 것만으로 창원시 5개 구를
    상대 비교하고 후보 개수만큼 추천한다. 점수 계산은 analysis/scoring.py가 전부
    담당하며, Ollama는 이 단계에 전혀 관여하지 않는다(추천 설명은 계산 결과를
    그대로 문장으로 바꾼 규칙 기반 텍스트일 뿐 AI가 새로 생성하지 않는다).

    표시 순서(사용자가 가장 먼저 보고 싶어할 내용부터):
    조건·가중치 -> 추천 후보지역 -> 5개 구 전체 순위 -> 계산식 -> 미반영 정보.
    """
    st.subheader("🏆 최초 추천 결과")
    st.caption(
        "창원시 5개 구를 시설 수 기준으로 상대 비교한 결과입니다. 아래 "
        "'🔄 조건 조정 후 다시 비교하기'에서 가중치를 바꾸기 전까지는 이 결과가 "
        "**현재 적용 중인 결과**입니다."
    )

    initial_input = st.session_state.initial_input
    region_text = initial_input.get("희망지역", "")
    mentioned_districts = [d for d in SPECIFIC_DISTRICT_NAMES if d in region_text]
    if mentioned_districts:
        st.info(
            f"ℹ️ 입력하신 '{region_text}'에 특정 구({', '.join(mentioned_districts)})가 "
            "포함되어 있지만, 현재 이 기능은 입력한 구로 범위를 좁히지 않고 **창원시 5개 구 "
            "전체**를 항상 비교합니다."
        )

    # 희망지역 지원 여부는 1단계 제출 시점에 이미 검증을 마쳤으므로(미지원/빈 값이면
    # 애초에 이 화면에 도달하지 않는다), 여기서는 최초 추천 결과를 다시 계산하지 않고
    # _finalize_initial_recommendation()이 stage='done' 전환 시 저장해 둔 값을 그대로 쓴다.
    result = st.session_state.initial_recommendation

    st.markdown("**이번 비교에 사용된 조건과 가중치**")
    if result["used_conditions"]:
        used_df = pd.DataFrame(
            [
                {
                    "조건": uc.get("condition")
                    or INDICATOR_CODE_TO_CONDITION.get(uc["indicator_code"], uc["indicator_name"]),
                    "사용 지표": uc["indicator_name"],
                    "가중치": f"{uc['weight'] * 100:.1f}%",
                }
                for uc in result["used_conditions"]
            ]
        )
        st.dataframe(used_df, hide_index=True, width="stretch")
    else:
        st.write("없음")

    if result["excluded_conditions"]:
        excluded_text = ", ".join(f"{e['condition']}({e['reason']})" for e in result["excluded_conditions"])
        st.caption(f"⚠️ 선택했지만 점수 계산에서 제외된 조건: {excluded_text}")

    wc = st.session_state.weight_confirmation
    if wc and wc["asked"]:
        origin_label = (
            "최초 입력의 '추가 요청사항'" if wc.get("answer_source") == "initial_extra_request" else "AI 추가질문 답변"
        )
        if wc["source"] == "ai_approved":
            st.success(
                f"✅ {origin_label}(\"{wc['answer_text']}\")을 승인해 이 가중치를 "
                "최초 추천 계산에 사용했습니다."
            )
        elif wc["source"] == "ai_rejected_by_user":
            ai_weights_text = ", ".join(
                f"{FEEDBACK_INDICATOR_LABELS.get(c, c)} {v:.0f}%"
                for c, v in (wc["ai_confirmed_weights"] or {}).items()
            )
            st.info(
                f"ℹ️ AI는 {origin_label}(\"{wc['answer_text']}\")에서 {ai_weights_text}을(를) 읽었지만, "
                "적용하지 않기로 선택해 선택하신 조건에 동일 가중치를 사용했습니다."
            )
        else:  # equal_fallback, skipped_blank 등
            st.info(f"ℹ️ {wc['reason']} 선택하신 조건에 동일 가중치를 적용했습니다.")

    with st.expander("아직 확보하지 못한 데이터"):
        st.write(UNAVAILABLE_DATA_NOTICE)

    if result["status"] == "no_usable_conditions":
        st.info(f"ℹ️ {result['message']} 선택하신 조건에 대응하는 실제 데이터가 아직 없습니다.")
        for c in result["caveats"]:
            st.caption(f"· {c}")
        return

    # 성격이 다른 정착 후보와 Critic 점검을 먼저, 이어서 종합점수 순 후보를 보여준다.
    candidate_review = (st.session_state.get("agent_execution_log") or {}).get("candidate_review")
    _render_candidate_set(candidate_review or build_candidate_set(result, _unscored_inputs(initial_input)))
    _render_top_candidates(result)

    st.markdown("---")
    st.markdown("**창원시 5개 구 전체 순위**")
    overview_df = pd.DataFrame(
        [
            {
                "순위": row["rank"],
                "구": row["region_name"],
                "종합점수": round(row["total_score"], 1),
                "동점": "예" if row["tied"] else "-",
            }
            for row in result["region_scores"]
        ]
    )
    st.dataframe(overview_df, hide_index=True, width="stretch")
    chart_df = pd.DataFrame(
        [{"구": r["region_name"], "종합점수": r["total_score"]} for r in result["region_scores"]]
    ).set_index("구")
    st.bar_chart(chart_df, color=CHART_ACCENT_COLOR)
    st.caption(RELATIVE_SCORE_CAVEAT)

    with st.expander("정규화 계산식 보기 (min-max, 0~100점)"):
        for code, info in result["normalization"].items():
            indicator_name = next(
                uc["indicator_name"] for uc in result["used_conditions"] if uc["indicator_code"] == code
            )
            st.markdown(f"- **{indicator_name}**: {info['formula']}")

    _render_school_reference()

    st.markdown("**아직 점수에 반영되지 않은 입력정보**")
    not_used_inputs = []
    if initial_input.get("직장/학교 위치"):
        not_used_inputs.append(
            f"직장/학교 위치('{initial_input['직장/학교 위치']}') - 실제 이동시간 지표가 없어 반영하지 않음"
        )
    if initial_input.get("주거비 예산"):
        not_used_inputs.append(
            f"주거비 예산('{initial_input['주거비 예산']}') - 실제 주거비(월세·전세) 지표가 없어 반영하지 않음"
        )
    if initial_input.get("자가용 보유 여부"):
        not_used_inputs.append(
            f"자가용 보유 여부('{initial_input['자가용 보유 여부']}') - 대응하는 지표가 없어 반영하지 않음"
        )
    extra_request_text = initial_input.get("추가 요청사항")
    extra_request_was_approved = (
        wc and wc.get("answer_source") == "initial_extra_request" and wc["source"] == "ai_approved"
    )
    if extra_request_text and not extra_request_was_approved:
        if wc and wc.get("answer_source") == "initial_extra_request":
            not_used_inputs.append(
                f"추가 요청사항('{extra_request_text}') - 비율 해석 결과는 위에 안내된 대로 "
                "처리되었고, 그 외 서술 내용은 대응하는 지표가 없어 반영하지 않음"
            )
        else:
            not_used_inputs.append(
                f"추가 요청사항('{extra_request_text}') - 대응하는 지표가 없어 반영하지 않음"
            )
    weight_question = st.session_state.weight_question_plan
    weight_question_text = weight_question["text"] if weight_question else None
    non_weight_answers = {
        q: a for q, a in st.session_state.followup_answers.items() if q != weight_question_text
    }
    if non_weight_answers:
        not_used_inputs.append(
            "AI 추가질문(가중치 확인 질문 제외) 답변 - 실제 지표와 연결할 수 없어 점수 계산에 "
            "반영하지 않음(입력정보로는 위에 보존됨)"
        )
    # 비율의 출처가 최초 입력의 '추가 요청사항'이면 바로 위 추가 요청사항 항목에서 이미
    # 안내했으므로, 이 줄은 AI 추가질문(가중치 확인)에 답한 경우에만 보여준다.
    if wc and wc["asked"] and wc["source"] != "ai_approved" and wc.get("answer_source") != "initial_extra_request":
        not_used_inputs.append(f"AI 추가질문(가중치 확인) 답변 - {wc['reason']}")
    for e in result["excluded_conditions"]:
        not_used_inputs.append(f"중요 생활조건 '{e['condition']}' - {e['reason']}")

    if not_used_inputs:
        for item in not_used_inputs:
            st.caption(f"· {item}")
    else:
        st.caption("입력하신 조건이 전부 점수 계산에 반영되었습니다.")

    for c in result["caveats"]:
        st.caption(f"· {c}")


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
    outcome = feedback.reevaluate(weights, candidate_count, _unscored_inputs(st.session_state.initial_input))
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
    """'초기 조건으로 되돌리기' 버튼 / 자연어 '원래대로' 승인의 on_click 콜백. 슬라이더를 최초 추천
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


def _current_applied_weights_label() -> str:
    """지금 이 순간 '실제로 점수 계산에 쓰이고 있는' 가중치를 문자열로 요약한다
    (승인된 피드백이 있으면 그것, 없으면 최초 추천 가중치). 승인 대기 중인 AI 제안은
    여기 포함하지 않는다 - 아직 적용된 게 아니기 때문이다."""
    source = st.session_state.feedback_recommendation or st.session_state.initial_recommendation
    if not source or source.get("status") != "ok":
        return "없음"
    parts = [
        f"{FEEDBACK_INDICATOR_LABELS.get(uc['indicator_code'], uc['indicator_code'])} {uc['weight'] * 100:.0f}%"
        for uc in source["used_conditions"]
    ]
    return ", ".join(parts) if parts else "없음"


def render_feedback_section() -> None:
    """
    '조건 조정 후 다시 비교하기' - 교통/의료/생활편의 가중치를 수동 슬라이더 또는
    자연어(AI 해석 + 승인)로 조정해 창원시 5개 구를 재평가한다. 가중치 정규화와
    5개 구 재계산은 analysis.scoring.compute_region_scores_from_weights()가 전부
    결정적으로 수행하며, AI는 자연어를 가중치 "제안"으로 해석만 할 뿐 점수를
    계산하지 않는다.
    """
    st.divider()
    st.subheader("🔄 조건 조정 후 다시 비교하기")
    st.caption(
        "교통·의료·생활편의 가중치를 직접 조정하거나 문장으로 요청해서 창원시 5개 구를 "
        "다시 비교할 수 있습니다. min-max 정규화 방식과 지표값 자체는 최초 추천과 동일합니다."
    )
    st.info(f"📌 현재 적용 중인 결과: {_current_applied_weights_label()}")

    for code, key in FEEDBACK_SLIDER_KEYS.items():
        if key not in st.session_state:
            st.session_state[key] = st.session_state.feedback_initial_weights.get(code, 0.0)

    st.markdown("**① 슬라이더로 직접 조정**")
    cols = st.columns(3)
    current_weights: dict[str, float] = {}
    for col, code in zip(cols, FEEDBACK_SLIDER_KEYS):
        with col:
            current_weights[code] = st.slider(
                FEEDBACK_INDICATOR_LABELS[code], 0, 100, key=FEEDBACK_SLIDER_KEYS[code]
            )

    weight_sum = sum(current_weights.values())
    if weight_sum > 0:
        normalized_preview = ", ".join(
            f"{FEEDBACK_INDICATOR_LABELS[c]} {v / weight_sum * 100:.1f}%"
            for c, v in current_weights.items()
            if v > 0
        )
        st.caption(f"정규화 후 적용될 가중치: {normalized_preview}")
    else:
        st.caption("⚠️ 가중치가 전부 0입니다. 하나 이상 0보다 크게 설정해야 다시 비교할 수 있습니다.")

    col_recompute, col_reset = st.columns(2)
    with col_recompute:
        recompute_clicked = st.button("다시 비교하기", type="primary", width="stretch")
    with col_reset:
        st.button(
            "초기 조건으로 되돌리기", on_click=_reset_feedback_weights, args=(None,), width="stretch"
        )

    if recompute_clicked:
        _apply_feedback(current_weights, source="slider")

    st.markdown("**② 자연어로 요청 (AI 해석)**")
    st.caption(
        f"예: '의료 80%, 교통 20%로 비교해줘', '병원을 더 중요하게', '교통은 조금 덜 중요하게'. "
        f"버튼을 누를 때만 AI({llm.backend_label()})를 "
        "호출하며, AI는 요청을 가중치 '제안'으로 해석만 할 뿐 점수는 계산하지 않습니다 - "
        "실제 재계산은 아래에서 승인해야 적용됩니다."
    )
    nl_text = st.text_input(
        "자연어 요청", key="nl_feedback_input", label_visibility="collapsed", max_chars=300
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

    _render_feedback_history()

    feedback_result = st.session_state.feedback_recommendation
    if feedback_result is None:
        return

    if feedback_result["status"] == "no_usable_conditions":
        st.warning(f"⚠️ {feedback_result['message']}")
        return

    st.markdown("---")
    st.markdown("### 📊 현재 적용 중인 피드백 결과 — 변경 전후 비교")
    st.caption("아래는 최초 추천과, 지금까지 승인(또는 '다시 비교하기')으로 **실제 적용된** 가중치를 비교한 내용입니다.")
    initial_result = st.session_state.initial_recommendation

    initial_weight_by_code = {
        uc["indicator_code"]: uc["weight"] for uc in initial_result.get("used_conditions", [])
    }
    feedback_weight_by_code = {
        uc["indicator_code"]: uc["weight"] for uc in feedback_result["used_conditions"]
    }
    weight_compare_df = pd.DataFrame(
        [
            {
                "지표": FEEDBACK_INDICATOR_LABELS[code],
                "변경 전 가중치": f"{initial_weight_by_code.get(code, 0) * 100:.1f}%",
                "변경 후 가중치": f"{feedback_weight_by_code.get(code, 0) * 100:.1f}%",
            }
            for code in REFERENCE_INDICATOR_ORDER
            if code in initial_weight_by_code or code in feedback_weight_by_code
        ]
    )
    st.markdown("**조건·가중치 변경**")
    st.dataframe(weight_compare_df, hide_index=True, width="stretch")

    initial_rank_by_region = {
        r["region_id"]: (r["rank"], r["total_score"]) for r in initial_result.get("region_scores", [])
    }
    score_compare_rows = []
    for row in feedback_result["region_scores"]:
        rid = row["region_id"]
        prev_rank, prev_score = initial_rank_by_region.get(rid, (None, None))
        score_delta = None if prev_score is None else round(row["total_score"] - prev_score, 1)
        if prev_rank is None:
            rank_change = "-"
        elif prev_rank == row["rank"]:
            rank_change = "변동없음"
        elif prev_rank > row["rank"]:
            rank_change = f"▲{prev_rank - row['rank']}"
        else:
            rank_change = f"▼{row['rank'] - prev_rank}"
        score_compare_rows.append(
            {
                "구": row["region_name"],
                "변경 전 순위": prev_rank if prev_rank is not None else "-",
                "변경 후 순위": row["rank"],
                "순위 변화": rank_change,
                "변경 전 점수": round(prev_score, 1) if prev_score is not None else "-",
                "변경 후 점수": round(row["total_score"], 1),
                "점수 변화": score_delta if score_delta is not None else "-",
            }
        )
    st.markdown("**5개 구 점수·순위 변화**")
    st.dataframe(pd.DataFrame(score_compare_rows), hide_index=True, width="stretch")

    weight_deltas = [
        (code, initial_weight_by_code.get(code, 0) * 100, feedback_weight_by_code.get(code, 0) * 100)
        for code in REFERENCE_INDICATOR_ORDER
        if abs(feedback_weight_by_code.get(code, 0) - initial_weight_by_code.get(code, 0)) > 0.0005
    ]
    if not weight_deltas:
        st.caption("가중치 구성 자체는 최초 추천과 동일합니다.")
    else:
        change_text = ", ".join(
            f"{FEEDBACK_INDICATOR_LABELS[c]} {before:.0f}%→{after:.0f}%" for c, before, after in weight_deltas
        )
        st.caption(f"가중치 변경: {change_text}")

    numeric_deltas = [r for r in score_compare_rows if isinstance(r["점수 변화"], (int, float))]
    if not numeric_deltas or all(abs(r["점수 변화"]) < 0.05 for r in numeric_deltas):
        st.caption(
            "가중치를 조정했지만 5개 구의 종합점수·순위에 의미 있는 변화는 없습니다"
            "(가중치가 바뀐 지표에서 구별 순위 구조가 비슷하기 때문일 수 있습니다)."
        )
    else:
        biggest = max(numeric_deltas, key=lambda r: abs(r["점수 변화"]))
        direction = "올라갔습니다" if biggest["점수 변화"] > 0 else "내려갔습니다"
        st.caption(
            f"예: {biggest['구']}는 가중치가 커진 지표에서의 정규화 점수가 상대적으로 "
            f"{'높아' if biggest['점수 변화'] > 0 else '낮아'} 종합점수가 "
            f"{biggest['점수 변화']:+.1f}점 {direction}."
        )

    _render_candidate_set(
        build_candidate_set(feedback_result, _unscored_inputs(st.session_state.initial_input)),
        heading="🧭 피드백 기준 정착 후보군 — 다시 평가한 결과",
    )
    explanation = st.session_state.get("feedback_explanation")
    if explanation and explanation.get("final_answer"):
        _render_final_answer(explanation["final_answer"], "### 🤖 AI의 재평가 결과 설명")
    _render_top_candidates(feedback_result, heading="현재 적용 중인 피드백 기준 추천 후보지역")


st.set_page_config(page_title="경남 이주자 생활권 탐색 AI", page_icon="🏡")

st.title("🏡 경남 이주자 맞춤형 생활권 탐색 AI")
st.caption("이주 희망 조건을 입력하면 AI가 추천에 필요한 추가 질문을 제안합니다.")

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
    agent_execution_log에 저장해 완료 화면의 'AI 분석 실행 과정 보기'에서 보여준다.
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
            "희망 지역 (현재는 창원시 5개 구만 지원합니다)",
            REGION_OPTIONS,
            index=_option_index(REGION_OPTIONS, prev.get("희망지역")),
            help="특정 구를 골라도 추천은 항상 창원시 5개 구 전체를 비교합니다.",
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
                "**창원시 5개 구(의창구·성산구·마산합포구·마산회원구·진해구)**만 지원합니다. "
                "희망 지역에 '창원시' 또는 '창원시 OO구'처럼 창원시를 포함해 다시 입력해 주세요."
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

# 3단계: 완료 - 추천 결과 중심 화면 ----------------------------------------
elif st.session_state.stage == "done":
    st.subheader("✅ 입력이 완료되었습니다")
    st.caption("아래에서 창원시 5개 구 비교 및 추천 결과를 확인하세요.")

    if st.session_state.followup_ai_error:
        st.info(
            f"ℹ️ AI 추가질문 생성 중 문제가 발생해 일부 질문은 만들지 못했습니다"
            f"({st.session_state.followup_ai_error}). 기존에 확보된 데이터로 추천은 "
            "정상적으로 진행되었습니다."
        )

    with st.expander("📝 내가 입력한 정보 보기 (최초 입력 + AI 추가질문 답변)"):
        st.markdown("**최초 입력 정보**")
        st.json(st.session_state.initial_input)

        wc = st.session_state.weight_confirmation
        if wc and wc.get("answer_source") == "initial_extra_request" and wc["source"] == "ai_approved":
            st.caption(
                "ℹ️ 위 '추가 요청사항'에 적으신 비율이 승인되어 실제 시설 수 기반 비교 "
                "점수 계산(가중치)에 반영되었습니다."
            )

        if st.session_state.followup_answers:
            st.markdown("**AI 추가질문에 대한 답변**")
            st.json(st.session_state.followup_answers)
            if wc and wc["asked"] and wc["source"] == "ai_approved" and wc.get("answer_source") == "followup_answer":
                st.caption(
                    "ℹ️ 이 중 ⚖️ 가중치 확인 질문의 답변은 승인되어 실제 시설 수 기반 비교 "
                    "점수 계산(가중치)에 반영되었습니다. 나머지 답변은 참고용으로 저장만 됩니다."
                )
            else:
                st.caption(
                    "ℹ️ 위 답변은 참고용으로 저장만 되며, 현재 시설 수 기반 비교 점수 계산에는 "
                    "반영되지 않습니다."
                )
        elif st.session_state.followup_questions == []:
            st.info("AI가 판단했을 때 추가로 필요한 정보가 없었습니다.")

    st.divider()
    render_recommendation_section()
    render_ai_recommendation_explanation()

    with st.expander("🤖 AI 분석 실행 과정 보기"):
        render_agent_execution_log()

    render_feedback_section()

    st.divider()
    st.subheader("📋 창원시 5개 구 상세 공공데이터")
    render_indicator_category("의료", "🏥 창원시 의료기관 현황")
    st.divider()
    render_indicator_category("교통", "🚌 창원시 버스정류장 현황")
    st.divider()
    render_indicator_category("생활편의", "🏪 창원시 편의점 현황")
    st.caption(
        "주거비 등 나머지 데이터가 확보되면 이 화면에 같은 방식으로 추가될 예정입니다."
    )

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()
