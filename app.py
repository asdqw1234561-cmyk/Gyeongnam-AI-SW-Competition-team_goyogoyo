# Streamlit 실행 진입점
import streamlit as st

from agent.ollama_agent import generate_followup_questions

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

    if st.button("처음부터 다시 입력하기"):
        reset_all()
        st.rerun()
