# Streamlit 실행 진입점
import pandas as pd
import streamlit as st

from agent.ollama_agent import generate_followup_questions
from analysis import scoring
from services.region_data import get_all_changwon_regions, is_supported_region

CHART_ACCENT_COLOR = "#2a78d6"
MAX_CANDIDATE_COUNT = 5  # 현재 지원 지역(창원시 5개 구) 수와 동일하게 맞춤

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

        st.markdown(f"**{indicator_name}**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        if note_text:
            st.caption(f"ℹ️ {note_text}")

        confirmed_rows = [row for row in rows if row["상태"] == "확보"]
        if confirmed_rows:
            chart_df = pd.DataFrame(
                [{"구": row["구"], "값": float(row["값"])} for row in confirmed_rows]
            ).set_index("구")
            st.bar_chart(chart_df, color=CHART_ACCENT_COLOR)
        else:
            st.caption("확보된 수치가 없어 그래프를 표시하지 않습니다.")

        missing_regions = [row["구"] for row in rows if row["상태"] != "확보"]
        if missing_regions:
            st.caption(
                f"미확보 구: {', '.join(missing_regions)} "
                "(0이 아니라 '미확보'로 표시되며 그래프에서는 제외됩니다)"
            )


REFERENCE_INDICATOR_ORDER = ["hospital_count", "bus_stop_count", "convenience_store_count"]

UNAVAILABLE_DATA_NOTICE = (
    "실제 대중교통 소요시간, 월세·전세 가격, 응급실 운영 병원 수, 대형마트 수, "
    "교육·안전·자연환경·문화시설 지표는 아직 확보되지 않아 추천 계산에 사용되지 않습니다."
)


def render_recommendation_section() -> None:
    """
    사용자가 선택한 '중요 생활조건' 중 실제 데이터가 확보된 것만으로 창원시 5개 구를
    상대 비교하고 후보 개수만큼 추천한다. 점수 계산은 analysis/scoring.py가 전부
    담당하며, Ollama는 이 단계에 전혀 관여하지 않는다(추천 설명은 계산 결과를
    그대로 문장으로 바꾼 규칙 기반 텍스트일 뿐 AI가 새로 생성하지 않는다).
    """
    st.subheader("🏆 창원시 5개 구 비교 및 후보지역")

    initial_input = st.session_state.initial_input
    region_text = initial_input.get("희망지역", "")
    if region_text and not is_supported_region(region_text):
        st.warning(
            f"입력하신 '{region_text}'는 현재 지원 범위 밖입니다. 이 서비스는 현재 "
            "**창원시 5개 구만** 지원하며, 아래 비교·추천 결과는 입력하신 지역과 "
            "무관하게 항상 창원시 5개 구를 대상으로 계산됩니다."
        )

    user_conditions = initial_input.get("중요 생활조건") or []
    candidate_count = initial_input.get("원하는 후보 개수") or 3
    result = scoring.compute_region_scores(user_conditions, candidate_count)

    st.markdown("**비교에 사용된 조건**")
    if result["used_conditions"]:
        used_df = pd.DataFrame(
            [
                {
                    "조건": uc["condition"],
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

    with st.expander("아직 확보하지 못한 데이터"):
        st.write(UNAVAILABLE_DATA_NOTICE)

    if result["status"] == "no_usable_conditions":
        st.info(f"ℹ️ {result['message']} 선택하신 조건에 대응하는 실제 데이터가 아직 없습니다.")
        for c in result["caveats"]:
            st.caption(f"· {c}")
        return

    st.markdown("**창원시 5개 구 전체 비교 결과**")
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

    with st.expander("정규화 계산식 보기 (min-max, 0~100점)"):
        for code, info in result["normalization"].items():
            indicator_name = next(
                uc["indicator_name"] for uc in result["used_conditions"] if uc["indicator_code"] == code
            )
            st.markdown(f"- **{indicator_name}**: {info['formula']}")

    st.markdown(f"**추천 후보지역 (요청하신 {len(result['top_candidates'])}개)**")
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
    if st.session_state.followup_answers:
        not_used_inputs.append(
            "AI 추가질문 답변 - 실제 지표와 연결할 수 없어 점수 계산에 반영하지 않음(입력정보로는 위에 보존됨)"
        )
    for e in result["excluded_conditions"]:
        not_used_inputs.append(f"중요 생활조건 '{e['condition']}' - {e['reason']}")

    if not_used_inputs:
        for item in not_used_inputs:
            st.caption(f"· {item}")
    else:
        st.caption("입력하신 조건이 전부 점수 계산에 반영되었습니다.")

    for c in result["caveats"]:
        st.caption(f"· {c}")


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


def reset_all():
    st.session_state.stage = "input"
    st.session_state.initial_input = {}
    st.session_state.followup_questions = []
    st.session_state.followup_answers = {}


# 1단계: 최초 입력 ------------------------------------------------------
if st.session_state.stage == "input":
    with st.form("initial_input_form"):
        region = st.text_input("희망 지역 (예: 창원시, 창원시 의창구 등 - 현재는 창원시만 지원합니다)")
        workplace = st.text_input("직장 또는 학교 위치")
        has_car = st.radio("자가용 보유 여부", ["보유", "미보유"], horizontal=True)
        budget = st.text_input("주거비 예산 (예: 월세 50만원 이하)")
        important_conditions = st.multiselect(
            "중요하게 생각하는 생활 조건",
            ["교통", "의료", "교육", "생활편의(마트/편의점)", "안전", "자연환경", "문화시설"],
        )
        candidate_count = st.number_input(
            "원하는 후보 지역 개수", min_value=1, max_value=MAX_CANDIDATE_COUNT, value=3, step=1
        )
        extra_request = st.text_area("추가 요청사항 (선택)")

        submitted = st.form_submit_button("다음 단계로")

    if submitted:
        st.session_state.initial_input = {
            "희망지역": region,
            "직장/학교 위치": workplace,
            "자가용 보유 여부": has_car,
            "주거비 예산": budget,
            "중요 생활조건": important_conditions,
            "원하는 후보 개수": candidate_count,
            "추가 요청사항": extra_request,
        }

        with st.spinner("AI가 입력 정보를 분석하고 있습니다..."):
            try:
                questions = generate_followup_questions(st.session_state.initial_input)
            except RuntimeError as exc:
                st.error(str(exc))
                st.stop()

        st.session_state.followup_questions = questions
        st.session_state.stage = "followup" if questions else "done"
        st.rerun()

# 2단계: AI 추가 질문 ----------------------------------------------------
elif st.session_state.stage == "followup":
    st.subheader("📝 AI의 추가 질문")
    st.write("추천 정확도를 높이기 위해 아래 질문에 답변해 주세요.")

    with st.form("followup_form"):
        answers = {}
        for i, question in enumerate(st.session_state.followup_questions, start=1):
            answers[question] = st.text_input(f"{i}. {question}", key=f"followup_{i}")

        followup_submitted = st.form_submit_button("답변 제출")

    if followup_submitted:
        st.session_state.followup_answers = answers
        st.session_state.stage = "done"
        st.rerun()

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()

# 3단계: 완료 - 수집된 정보 확인 ------------------------------------------
elif st.session_state.stage == "done":
    st.subheader("✅ 입력 정보 확인")
    st.write("아래 정보가 저장되었습니다. (지역 추천 기능은 아직 구현되지 않았습니다.)")

    st.markdown("**최초 입력 정보**")
    st.json(st.session_state.initial_input)

    if st.session_state.followup_answers:
        st.markdown("**AI 추가 질문에 대한 답변**")
        st.json(st.session_state.followup_answers)
    elif st.session_state.followup_questions == [] and st.session_state.stage == "done":
        st.info("AI가 판단했을 때 추가로 필요한 정보가 없었습니다.")

    st.divider()
    render_recommendation_section()

    st.divider()
    st.subheader("📋 창원시 5개 구 상세 지표")
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
