# Streamlit 실행 진입점
import pandas as pd
import streamlit as st

from agent.ollama_agent import generate_followup_questions
from services.region_data import get_all_changwon_regions

CHART_ACCENT_COLOR = "#2a78d6"

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
        region = st.text_input("희망 지역 (예: 창원시, 김해시 등)")
        workplace = st.text_input("직장 또는 학교 위치")
        has_car = st.radio("자가용 보유 여부", ["보유", "미보유"], horizontal=True)
        budget = st.text_input("주거비 예산 (예: 월세 50만원 이하)")
        important_conditions = st.multiselect(
            "중요하게 생각하는 생활 조건",
            ["교통", "의료", "교육", "생활편의(마트/편의점)", "안전", "자연환경", "문화시설"],
        )
        candidate_count = st.number_input(
            "원하는 후보 지역 개수", min_value=1, max_value=10, value=3, step=1
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
    render_indicator_category("의료", "🏥 창원시 의료기관 현황")
    st.divider()
    render_indicator_category("교통", "🚌 창원시 버스정류장 현황")
    st.divider()
    render_indicator_category("생활편의", "🏪 창원시 편의점 현황")
    st.caption(
        "현재는 창원시 5개 구 전체를 비교용으로 보여드리며, 지역 추천 순위나 "
        "적합도 점수는 아직 계산하지 않습니다. 주거비 등 나머지 데이터가 "
        "확보되면 이 화면에 같은 방식으로 추가될 예정입니다."
    )

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()
