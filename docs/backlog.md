# 개발 백로그

개발 Agent(`/steward`, `/dev`, `/loop /steward`)가 관리하는 작업 큐와 처리 이력이다.
**사람이 직접 항목을 추가·완료 표시하지 않아도 된다.** `/steward`가 `docs/agent/EVAL_MATRIX.md`의 공백과 대회 기준으로 작업을 찾아 여기에 추가하고, 처리 후 결과를 스스로 기록한다.
사람은 `승인: 필요` 항목을 `승인: 완료`로 바꾸거나, 자연어로 지시하면 된다(그 지시를 Agent가 항목으로 옮긴다).

## 처리 규칙
- 한 번에 한 항목. 상태가 `[ ]`이고 **승인: 불필요**(또는 `완료`)인 항목 중 대회 기준 우선순위가 가장 높은 것을 처리한다(`.claude/skills/steward/SKILL.md` STEP 5).
- **승인: 필요** 항목은 처리하지 않는다. 주제 변경·새 외부 데이터·핵심 구조 대수정·팀원 충돌 가능성·`CLAUDE.md`가 사전 보고를 요구하는 변경이 여기에 해당한다.
- 새 항목에는 근거(R-* 요구사항 ID 또는 EVAL_MATRIX 공백 번호)를 적는다.
- 처리 절차는 `.claude/skills/dev/SKILL.md`와 `CLAUDE.md`를 따른다.
- 끝나면 상태를 `[x]`로 바꾸고 `결과:` 줄에 날짜·변경 파일·테스트 결과를 한 줄로 남긴다.
- 막히면(테스트를 통과시킬 수 없음, 범위가 예상보다 큼, 원칙 충돌) 상태를 `[!]`로 바꾸고 `결과:`에 이유를 적은 뒤 멈춘다.
- 처리 중 새로 발견한 일은 맨 아래 "발견된 작업"에 추가만 하고 바로 처리하지 않는다.
- 상태 표기: `[ ]` 대기 · `[x]` 완료 · `[!]` 막힘

---

## B1. 의료 지표 표현을 "의료기관 수"로 통일 (코드·화면 문구)
- [x] 상태
- 승인: 불필요
- 범위: 코드 안의 라벨·프롬프트·설명 문구만. `data/region_indicators.csv`의 `indicator_name`("병원 수")은 원본 데이터라 이 항목에서 바꾸지 않는다(B8 참고).
  - `app.py:22` `"의료 (병원 수)"`
  - `agent/ollama_agent.py:233`, `:243`
  - `agent/planner.py:111`
  - `analysis/scoring.py:12` (docstring)
  - `pages/government.py:33` ("병원 수는 … 등록 의료기관 수이며" → 문장 주어를 "의료기관 수는"으로)
  - `app.py:144`의 "응급실 운영 병원 수"는 별도 미확보 지표 이름이므로 바꾸지 않는다.
- 완료 조건: 위 위치에 "병원 수"가 남지 않음, 전체 테스트 통과. 테스트 픽스처의 `"indicator_name": "병원 수"`는 CSV 값을 흉내 낸 것이므로 그대로 둔다.
- 결과: 2026-10-02 · app.py, agent/ollama_agent.py(2곳), agent/planner.py, analysis/scoring.py, pages/government.py 라벨·프롬프트·설명 변경(6줄) · 모킹 테스트 관련 85개/전체 240개 통과(skip 1) · 실제 Ollama·화면 확인 미실행. 화면 표의 지표명은 CSV 값이라 여전히 "병원 수"로 보임(B8에서 처리).

## B2. app.py 가중치 승인 사유 문구가 출처와 맞지 않는 문제
- [x] 상태
- 승인: 불필요
- 범위: `app.py:1115` — 추가 요청사항에서 온 비율을 승인해도 reason이 "AI 추가질문 답변을 승인해…"로 저장됨. `weight_interpretation_source`에 따라 문구를 나눈다.
- 완료 조건: 두 출처 각각 올바른 문구, 기존 화면 동작 불변, 전체 테스트 통과.
- 결과: 2026-10-02 · app.py:1115 reason을 이미 출처별로 정해지는 `origin_label`로 생성(1줄) · py_compile OK, 전체 240개 통과(skip 1), AppTest로 app.py 첫 화면 예외 0건 · 이 reason은 승인 시 화면에 직접 표시되지 않아 화면상 변화 없음. 승인 버튼 클릭 흐름은 브라우저 수동 확인 필요.

## B3. AI 응답 JSON 추출을 안전하게 + 중복 함수 정리
- [x] 상태
- 승인: 불필요
- 범위: `agent/planner.py:146`과 `agent/location_agent.py:235`의 `_parse_plan`(탐욕적 `\{.*\}` 정규식), `_sanitize_goals`/`_sanitize_unsupported_requests`가 두 파일에 중복. 공용 모듈(예: `agent/llm_json.py`)로 옮기고, 응답 안의 **첫 번째 완전한 JSON 객체**를 `json.JSONDecoder.raw_decode`로 찾도록 바꾼다. `agent/ollama_agent.py`의 같은 패턴(`_parse_questions`, `_parse_weight_feedback`)도 같은 헬퍼를 쓰게 한다.
- 완료 조건: 기존 테스트 전부 통과 + "JSON 앞뒤에 설명 문장", "JSON 객체 2개" 응답 케이스 테스트 추가. 기존 반환 형식과 sanitize 길이 제한은 그대로.
- 결과: 2026-10-02 · 새 `agent/llm_json.py`(extract_json_object=raw_decode로 첫 완전한 객체, sanitize_goals, sanitize_unsupported_requests(request_max_len)) · planner/location_agent의 중복 함수 제거(길이 제한 100/150 유지), ollama_agent 두 파서 교체(오류 메시지 유지), 미사용 import 정리 · 새 tests/test_llm_json.py 15개 포함 전체 255개 통과(skip 1) · 실제 Ollama(planner·가중치 해석·추가질문·위치 계획)와 실제 claude Agent 각 1회 정상.

## B4. 위치 Agent 답변 속 숫자 검증
- [x] 상태
- 승인: 불필요
- 범위: `agent/location_agent.py`의 `_build_agent_result()`. Claude의 `agent_answer`에 나온 숫자(개수·거리 m)가 실제 실행한 도구 결과(`total_count`, `straight_distance_m`, 반경별 counts 등)에 존재하는지 Python이 확인한다. 반경(300/500/1000)·순위 같은 질문 맥락 숫자는 허용 목록으로 처리.
  - 근거 없는 숫자가 있으면 결과에 `answer_verification`(검증 결과·문제 숫자)을 담고, `pages/user.py`에서 답변 위에 "⚠️ 일부 수치를 실제 조회 결과에서 확인하지 못했습니다 - 아래 표를 기준으로 보세요"를 표시한다. 답변 문장을 임의로 고치지는 않는다.
- 완료 조건: 일치/불일치 케이스 단위 테스트, 전체 테스트 통과, 화면 반영(브라우저 확인 못 하면 "수동 확인 필요" 기록).
- 결과: 2026-10-02 · agent/location_agent.py에 verify_answer_numbers() 추가(실행된 도구 결과의 숫자·문자열 속 숫자·YYYYMM(DD) 날짜 분해, 질문 숫자, 반경 m/km 허용) → 결과에 answer_verification, 실패 시 notes 기록, 답변 문장은 수정 안 함 · pages/user.py에 경고 표시 · 테스트 8개 추가, 전체 263개 통과(skip 1) · 실제 claude Agent 3개 질문 중 1건이 기준년월 "202606"→"2026년" 오탐이라 날짜 분해 규칙 추가 후 재실행 통과 · AppTest로 경고 렌더링 확인(예외 0). 실제 브라우저 클릭 흐름은 수동 확인 필요.
- 이후: M1(방식 A)에서 잠시 제거됐다가 M2(팀원 hwang3 기준 통합)에서 다시 살아남. 현재는 MCP 반복형 Agent의 답변 검증으로 유지.

## B5. services 공통 유틸 중복 정리
- [x] 상태
- 승인: 불필요
- 범위: `services/bus_stops.py`와 `services/convenience.py`에 같은 `_haversine_m`, `_to_float`, `_to_int`, `DISTRICTS`, `_CHANGWON_BBOX`가 있고 `pages/user.py:73`에도 `_CHANGWON_BBOX`가 있다. `services/geo.py`(가칭)로 옮겨 재사용한다. 지구 반지름·bbox 값은 그대로.
- 완료 조건: 동작 변화 없음(버스 공식 집계 검증 포함 전체 테스트 통과), 중복 정의 제거.
- 결과: 2026-10-02 · 새 services/geo.py(DISTRICTS, CHANGWON_BBOX, EARTH_RADIUS_M, to_float, to_int, haversine_m, is_within_changwon_bbox) · bus_stops/convenience는 기존 이름으로 import(호출부 무변경), 미사용 math·numpy import 제거 · pages/user.py bbox 중복 제거 · tests/test_geo.py 7개 추가, 전체 269개 통과(skip 1) · `python -m services.bus_stops` 공식 집계 일치, 창원시청 예시 좌표 반경별 개수·최근접 거리 리팩터링 전과 동일, AppTest user.py 예외 0.

## B6. scoring의 private 함수 공개 이름 제공
- [x] 상태
- 승인: 불필요
- 범위: `analysis/scoring.py:91` `_collect_confirmed_indicator`를 `agent/planner.py`, `analysis/simulation.py`가 직접 import한다. 공개 이름 `collect_confirmed_indicator`를 추가하고(기존 이름은 별칭으로 유지) 호출부를 공개 이름으로 바꾼다. 계산 로직은 건드리지 않는다.
- 완료 조건: 전체 테스트 통과, 계산식 diff 없음.
- 결과: 2026-10-02 · analysis/scoring.py에 공개 함수 collect_confirmed_indicator() 추가(내부 _collect_confirmed_indicator에 그대로 위임 - 기존 테스트의 private 모킹이 계속 유효하도록 별칭 대신 위임 함수로 구현) · agent/planner.py, analysis/simulation.py 호출부를 공개 이름으로 변경 · scoring.py는 추가만 있고 계산 코드 삭제·수정 0줄 · 위임 테스트 2개 추가, 전체 271개 통과(skip 1), AppTest app.py·government.py 예외 0.

## B7. Streamlit 화면 스모크 테스트 추가
- [x] 상태
- 승인: 불필요
- 범위: `streamlit.testing.v1.AppTest`로 `app.py`, `pages/user.py`, `pages/government.py`가 예외 없이 첫 화면을 그리는지 확인하는 테스트(`tests/test_pages_smoke.py`). Ollama·claude 호출은 모킹하거나 버튼을 누르지 않는 범위로 제한한다. folium 지도는 AppTest에서 렌더링되지 않을 수 있으니 예외 없이 지나가는지만 본다.
- 완료 조건: 새 테스트가 실제 AI 호출 없이 통과, 전체 테스트 시간 큰 증가 없음(대략 +30초 이내).
- 결과: 2026-10-02 · 새 tests/test_pages_smoke.py 4개(app.py 입력 단계, user.py, government.py 첫 렌더링 + user.py에 검증 실패 Agent 결과 재렌더링 시 경고·답변 표시) · 첫 화면에서 ollama.chat/claude CLI가 호출되면 실패하도록 차단 · CLAUDE.md 7절에 안내 1줄 · 전체 275개 통과(skip 1), 전체 시간 1.2초→2.6초 · Stop hook 정상 동작 확인. 버튼 클릭 흐름은 범위 밖(브라우저 확인 필요).

## B8. CSV의 hospital_count 지표명을 "의료기관 수"로 변경
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 승인. 단 `.claude/settings.json`의 `Edit(./data/region_indicators.csv)` 차단은 **아직 유지 중**이며 사용자가 직접 해제해야 한다. 해제 전에는 CSV 수정 단계에서 `[!]`로 멈추고, Bash 등으로 우회 수정하지 않는다. 이 항목 범위 밖의 값·행은 수정하지 않는다)
- 범위: 5개 행의 `indicator_name` "병원 수" → "의료기관 수". 수치·출처·기준일은 그대로. 수집 스크립트(`scripts/ingest_hira_hospital_data.py`)가 다시 생성할 때도 같은 이름을 쓰도록 맞춘다.
- 결과: 2026-10-02 · 사용자가 settings.json의 CSV 차단을 해제(e30f4e1) · data/region_indicators.csv의 hospital_count 5행 indicator_name "병원 수"→"의료기관 수"(값·출처·기준일·상태 불변, UTF-8/LF 유지, 40행 유지) · scripts/ingest_hira_hospital_data.py가 재수집 시에도 같은 이름을 쓰도록 명시 + docstring 표현 수정 · 전체 279개 통과(skip 1), AppTest로 government.py에 "의료기관 수" 표시·"병원 수" 미표시 확인, 점수 결과 지표명 확인.

## B9. 빈 스텁 파일 정리
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 승인. 처리 방식: 파일은 남기고 역할 설명 docstring만 채운다. `agent/agent_state.py`는 팀원이 hwang2에서 97줄로 구현해 사용 중이라 **제외** - jhy에서 건드리면 병합 시 충돌)
- 범위: `agent/claude_agent.py`, `agent/agent_state.py`, `services/housing.py`, `services/medical.py`, `services/transport.py`, `database/db.py` — 주석 한 줄뿐. 삭제할지, 역할 설명 docstring을 채울지 결정 필요.
- 결과: 2026-10-02 · 5개 파일(agent/claude_agent.py, services/housing.py, services/medical.py, services/transport.py, database/db.py)에 '미구현 - 현재 상태 / 예정 역할' docstring 추가(코드 없음, 어디서도 import 안 됨 확인) · 언급한 미확보 지표 4개·로더 함수명 CSV/코드로 확인 · agent/agent_state.py는 제외 · hwang2 병합 시뮬레이션에서 이 5개 파일 충돌 없음 · py_compile OK, 전체 279개 통과(skip 1).

## B10. README 현행화
- [x] 상태
- 승인: 불필요
- 범위: 현재 README는 두 줄이고 "경남·주거비 기반 추천"이라 실제(창원시 5개 구, 주거비 미확보)와 다르다. 실행 방법, 화면 3개, 확보/미확보 데이터, Agent 구조, 테스트 방법을 CLAUDE.md와 모순 없이 정리한다.
- 완료 조건: 실제 코드·데이터와 일치하는 내용만, 과장 표현 없음.
- 결과: 2026-10-02 · README.md 전면 작성(대상 범위, 화면 3개, 확보/미확보 데이터·출처·기준일, 해석 주의, Agent 구조, 실행·테스트 방법, Claude Code 개발 설정) · 수치(2,926/1,358/950)·기준일·미확보 지표 5개·기본 백엔드·모델명을 CSV와 코드로 교차 확인 · 코드 변경 없음.

---

## U1. 최초 입력 폼의 희망지역·직장/학교 위치·주거비 예산을 선택지로 변경 (사용자 요청)
- [x] 상태
- 승인: 불필요(사용자 직접 요청)
- 범위: `app.py` 입력 폼 3개 항목을 text_input → selectbox. 희망지역은 창원시 전체/5개 구, 직장·학교와 주거비는 "선택 안 함" 포함 선택지이며 "선택 안 함"은 기존 빈 값("")으로 저장. 직장·학교와 주거비는 점수 계산에 쓰지 않는다는 도움말 표시.
- 결과: 2026-10-02 · app.py(+53/-5), tests/test_pages_smoke.py 2개 추가(선택지 노출, 선택값 저장·제출 흐름) · 전체 277개 통과(skip 1) · 실제 브라우저 확인은 수동 확인 필요.

---

## M1. hwang2(팀원 브랜치) 통합 병합 - 방식 A (사용자 요청)
- [x] 상태
- ⚠️ 취소됨: 팀원이 MCP 위치 Agent를 계속 쓰므로 M2에서 팀원의 hwang3 통합 결과로 되돌림(MCP 유지).
- 승인: 완료(사용자 직접 요청)
- 범위: origin/hwang2(OHunCoin, 4커밋: llm.chat 단일 호출 계층·Claude CLI 백엔드·사용량 제한, 위치/추천 결과 검토 루프, 대화 기억, 반경 인식 버그 수정)를 jhy에 병합. 위치 Agent 반복 방식은 팀원의 agent_loop로 통일(A).
- 결과: 2026-10-02 · 충돌 6개 해결 — requirements.txt(hwang2, mcp 불필요), ollama_agent.py·planner.py(llm.chat + think=False + llm_json 모두 유지), location_agent.py(hwang2 기준 + llm_json 공용화만 재적용), pages/user.py(hwang2 결과 표시 + jhy geo 유지, ai_agent 분기 제거), app.py(jhy 선택지 폼 + hwang2 AI 설명·max_chars) · 삭제: agent/location_mcp_server.py, MCP/claude 백엔드/답변 검증 테스트 · planner_loop 라벨 "병원 수"→"의료기관 수" · CLAUDE.md·README 아키텍처 갱신 · 전체 338개 통과(skip 1, hwang2 테스트 5개 파일 포함) · 실제 실행: LLM_BACKEND=ollama/claude_cli 각각 위치 Agent·planner·가중치 해석 정상.

---

## M2. 팀원의 hwang3 통합 결과로 재정렬 - MCP 위치 Agent 유지 (사용자 요청)
- [x] 상태
- 승인: 완료(사용자 직접 요청)
- 범위: 팀원(OHunCoin)이 jhy@f82e67d 위에 hwang2를 병합하며 MCP 위치 Agent를 살린 origin/hwang3를 기준으로 삼는다. jhy의 M1(MCP 제거)을 결과적으로 취소하고, hwang3에 없던 jhy 커밋(B9 빈 파일 설명, 권한 설정 조정, B8 CSV 지표명)만 다시 적용.
- 결과: 2026-10-02 · origin/hwang3 병합(파일 내용은 hwang3 그대로) + B9·권한·B8 재적용 + planner_loop/agent_loop 문서의 "병원 수"→"의료기관 수" + README Agent 표 보강 · 위치 Agent: LOCATION_AGENT_BACKEND=claude_agent(기본, MCP) / 그 외 AI 호출: LLM_BACKEND · 테스트·실제 실행 결과는 커밋 메시지 참고.

---

## N1. AI 백엔드 설정 단일화 - 위치 Agent 기본 경로를 LLM_BACKEND에서 결정
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 승인, 브랜치 jhy-next)
- 범위: 지금은 위치 Agent가 `LOCATION_AGENT_BACKEND`(기본 claude_agent), 나머지 AI 호출이 `LLM_BACKEND`(기본 ollama)를 따로 본다. `LOCATION_AGENT_BACKEND`가 없으면 `LLM_BACKEND`로 기본 경로를 정한다: claude_cli → claude_agent(MCP), ollama → 계획형(ollama, agent_loop 검토 포함). `LOCATION_AGENT_BACKEND`는 명시했을 때만 덮어쓰는 선택 설정으로 유지한다.
- 주의: 기본값 변화 - 아무 설정 없을 때 위치 Agent가 MCP(claude) 대신 Ollama 계획형으로 동작하게 된다. .env.example·CLAUDE.md·README·docs/claude_cli_agent.md 설명을 함께 맞춘다.
- 완료 조건: 설정 조합별 단위 테스트(미설정, LLM_BACKEND만, 둘 다), 전체 테스트 통과, 실제 실행 확인.
- 결과: 2026-10-02 · agent/location_agent.py `_planner_backend()` - LOCATION_AGENT_BACKEND가 비어 있으면 LLM_BACKEND로 결정(claude_cli→claude_agent, 그 외→ollama), 명시 시 기존대로 덮어쓰기 · .env.example(LOCATION_AGENT_BACKEND= 비움)·CLAUDE.md·README 설명 갱신 · BackendSelectionTest 4개 추가, 전체 362개 통과(skip 1) · 실제 실행: LLM_BACKEND=ollama→계획형 ai_verified 답변, claude_cli→MCP ai_agent · ⚠️ 팀원 .env에 LOCATION_AGENT_BACKEND=claude_agent가 적혀 있으면 기존처럼 MCP로 동작(변화 없음), 비어 있으면 LLM_BACKEND를 따름.

## N2. 위치 Agent 답변 숫자 검증을 agent_loop 기준으로 통일
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 승인, 브랜치 jhy-next)
- 범위: MCP 경로(`_build_agent_result`)의 `verify_answer_numbers`(경고만 표시)를 제거하고, 팀원 `agent/agent_loop.py`의 단위별 검사(개수·거리 값 일치, 거리엔 "직선거리", 계산하지 않은 이동시간 금지)를 MCP 답변에도 적용한다. 실패하면 답변을 쓰지 않고 Python 요약으로 대체하고 사유를 남긴다(계획형과 같은 `final_answer` 형식). 화면(`pages/user.py`)도 하나의 표시 방식으로 맞춘다.
- 완료 조건: MCP 경로 통과/실패 케이스 테스트, 기존 agent_loop 테스트 유지, 전체 테스트 통과, 실제 claude 실행으로 오탐 여부 확인.
- 결과: 2026-10-02 · agent/location_agent.py: verify_answer_numbers 등 제거, MCP 결과도 agent_loop.summarize_observations → verify_answer → 실패 시 python_summary로 `final_answer` 생성(agent_answer/answer_verification 키 제거), MCP 지시문에 검사 규칙 명시 · pages/user.py 답변 표시 한 경로로 통일, agent/agent_state.py 기억도 final_answer만 사용 · 테스트: McpAnswerVerificationTest 6개·화면 테스트 2개로 교체, 전체 361개 통과(skip 1) · 실제 claude MCP 5회 중 4회 ai_verified, 1회는 목록을 세어 만든 개수(2개)로 탈락→Python 요약(규칙상 의도된 엄격함) · README·CLAUDE.md 갱신.

## N3. 위치 Agent 경로 단순화 재평가 (N1·N2 이후)
- [ ] 상태
- 승인: 불필요
- 범위: N1·N2 반영 후에도 남는 중복(결과 형식, 화면 분기, 테스트)을 점검해 정리할 수 있는 것만 최소 수정한다. 경로 자체(MCP/계획형)를 없애는 변경은 하지 않고, 필요하면 "발견된 작업"에 제안으로만 남긴다.
- 결과:

## N4. 브라우저 자동 확인 절차 정식화
- [ ] 상태
- 승인: 불필요
- 범위: Playwright(헤드리스 Chromium)로 Streamlit을 띄워 주요 버튼 흐름을 확인하는 스크립트(`scripts/e2e_smoke.py`)와, 그 실행법을 담은 프로젝트 skill(`.claude/skills/run-app/SKILL.md`)을 만든다. 실제 AI 호출이 필요한 단계는 옵션으로 분리한다. 단위 테스트 묶음에는 넣지 않는다(브라우저 설치 필요). playwright는 개발용 의존성으로만 안내(requirements에 넣지 않음).
- 완료 조건: 스크립트가 3개 화면 + 위치 Agent 실행 흐름을 스크린샷으로 남기고 콘솔 오류를 보고, skill 문서대로 재현 가능.
- 결과:

## R1. 정착 후보군 역할 부여 + Critic 점검 (추천 품질 G1+G2)
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 지시 - 추천 품질 최우선, DEC-11)
- 근거: `docs/agent/CURRENT_STATE.md` 추천 품질 분석 G1·G2, R-EV-2(판단·추론), R-AG-5(결과 확인·수정), R-EV-4(차별성)
- 범위: 새 `analysis/candidates.py` - 기존 `score_result`의 축별 정규화 점수만으로 후보 역할(최적·균형·대안, 가성비는 주거비 미확보로 산출 불가 표시)과 Critic 점검(1·2위 근소차, 단일 지표 의존, 지배 관계, 후보 쏠림, 평가축 데이터 커버리지, 구 단위 한계)을 결정적으로 계산. `agent/planner.run_agent_plan` 결과에 `candidate_review` 추가(Agent 단계로 기록), `app.py` 기존 결과 화면(최초·피드백)에 후보군·Critic 표시. 점수 계산식·순위·`top_candidates`는 바꾸지 않는다. LLM 호출 없음.
- 테스트: 새 `tests/test_candidates.py`(실데이터 형태 픽스처로 역할·Critic 각 규칙, 축 1개·동점·no_usable 경계), planner 결과 키 테스트, 전체 테스트 + AppTest 스모크.
- 완료 조건: 실제 CSV 동일 가중치에서 최적≠균형 후보가 나오고 Critic이 근거 숫자와 함께 표시, 기존 테스트 전부 통과.
- 결과: 2026-10-02 · 새 analysis/candidates.py(build_candidate_set: 6개 평가축 상태, 최적·균형·대안·가성비(산출 불가) 역할, Pareto, Critic 7규칙 close_gap/single_axis/dominated/concentration/granularity/coverage/ties + 지배된 1차 대안을 바꾸는 revised) · agent/planner.py run_agent_plan에 unscored_inputs 인자·candidate_review 결과 추가 · app.py 최초·피드백 결과 화면에 후보군·Critic 표시(_render_candidate_set) · scoring.py 변경 0줄 · tests/test_candidates.py 15개 + planner 1개 + AppTest 1개 추가, 전체 377개 통과(skip 1) · 실데이터 동일 가중치: 최적 성산구 / 균형 의창구 / 대안 1차 마산합포구→Critic이 의창구로 수정 / 가성비 산출 불가 · AppTest로 "다시 비교하기"(교통 80%) 후 최적 의창구·대안 성산구 재평가 확인 · 실제 Ollama·브라우저 확인 미실행.

## R2. 방향성 자연어 피드백 → 결정적 가중치 조정 → 재평가 → feedback_history (추천 품질 G3)
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 지시, DEC-12)
- 근거: `docs/agent/CURRENT_STATE.md` G3, R-EV-2(Memory·Feedback), CHARTER §4 Feedback·Memory
- 범위: 새 `analysis/feedback.py`(adjust_weights 배율 규칙, reevaluate 단일 재평가 경로, candidate_changes, history_entry, 축 별칭 판정) · `agent/ollama_agent.py` 프롬프트에 adjust_direction/reset 추가, 축 재판정·문장 근거 확인 파서 · `app.py` 슬라이더·숫자·방향·되돌리기를 `_apply_feedback` 한 경로로, 방향성 제안 승인 화면, 무시 기록, 피드백 기록 표시. scoring.py·candidates.py 변경 없음.
- 결과: 2026-10-02 · 위 3개 파일 + tests/test_feedback.py 22개 + AppTest 1개(승인→재평가·기록, 무시 기록, 슬라이더 같은 경로) · 전체 400개 통과(skip 1) · scoring.py·candidates.py diff 0줄 · 실제 Ollama 8문장 해석 기대대로(단, "날씨"는 LLM이 바로 unsupported를 골라 일반 문구로 안내) · 브라우저 확인 미실행 · 미커밋(사용자 지시).

## R3. G4 주거비 데이터 확보 가능성 조사·설계 (코드 변경 없음)
- [x] 상태
- 승인: 완료 (2026-10-02 사용자 지시, DEC-13)
- 결과: 2026-10-02 · `docs/agent/CURRENT_STATE.md` "G4 feasibility"에 10개 항목 조사 결과와 A/B/C 구현 후보 기록 · 공식 페이지로 확인: 요청 파라미터 LAWD_CD(5자리)·DEAL_YMD, XML/REST, 동·호 제외, 이용허락 제한 없음, 개발계정 10,000회/일 · 창원 5개 구 코드 48121/48123/48125/48127/48129 ↔ region_id 1:1(bjd_code.csv) · 실제 샘플 호출은 키 미등록(resultCode 30)으로 실패 → 필드명·표본 수·중앙값 검증은 활용신청 후(OPEN-5) · 코드·테스트 변경 없음.

## R4. Critic·후보 역할을 AI 설명 단계에 연결
- [x] 상태
- 승인: 완료 (2026-10-03 사용자 지시)
- 근거: R-EV-2(판단·추론), R-AG-5(결과 확인·수정), R-ETH-5(AI 오류 대책), DEC-11
- 결과: 2026-10-03 · agent/planner_loop.py: candidate_view(후보 역할·revised_from·Critic facts·평가축·미확보 축·반영 못 한 조건·limits)를 관찰에 추가, 프롬프트에 후보군 설명 규칙, verify_answer에 verify_candidate_claims(판단 범위·과장·미확보 축 단정·역할 불일치, 구 이름은 5개 구 전체), python_summary에 후보·교체·경고·한계, 피드백용 explain_candidates, call_reviewer think=False(기존 결함) · agent/planner.py run_review_loop에 candidate_review 전달 · analysis/candidates.py Critic check에 facts만 추가(선정 규칙 불변) · app.py 피드백 재평가 뒤 설명 생성·표시(_render_final_answer 공용) · 입력 길이 4000자 이내로 압축(최악 3,942자) · tests/test_candidate_explanation.py 21개 + AppTest 1개, test_planner_loop 정상 답변 픽스처 3개에 판단 범위 문구 추가(규칙 의미 변경) · 전체 436개 통과(skip 1) · 실제 Ollama 최초·피드백 설명 ai_verified, 단 의미 오류 2건은 검사를 통과(CURRENT_STATE 참고) · 미커밋.

## R5. AI 후보 설명의 의미 정확성 - explanation_facts
- [x] 상태
- 승인: 완료 (2026-10-03 사용자 지시)
- 근거: R4 실제 Ollama 설명의 의미 오류 2건(약점 교통을 "앞선다", "총 3개 중 6개"), R-ETH-5(AI 오류 대책), R-EV-2
- 결과: 2026-10-03 · 새 analysis/explanation_facts.py(구별 축 value·score·rank, 강점/약점/중립 목록 - candidates와 같은 상수, 축 방향, 역할 구 축별 높은 순서, 지배 관계, 확보/전체 평가축 수·미확보·미사용·반영 못 한 조건) · agent/planner_loop.py: 관찰을 candidate_view → explanation_facts로 교체, 프롬프트에 "사실만 옮기기" 규칙, verify_fact_claims(강점↔약점 뒤집기·가장 많다/적다 순위·두 구 비교 방향·지배 방향·미확보 축 강점/약점 표현·N개 중 M개·가정 결과 없는 "가중치를 바꿔도"·**숫자 귀속**(한 구·한 축 절의 N점/N개/N위는 그 구 그 축 값)·**역할 구문**("균형과 대안 역할"+주어 생략)), Python 요약에 강점/약점·평가축 수 · 입력 최악 3,927자(4,000 이내) · tests/test_explanation_facts.py 20개(사용자 지정 1~7 + 실제 Ollama 오답 3유형 회귀), test_candidate_explanation 키 갱신 · 전체 456개 통과(skip 1) · scoring.py·candidates.py 선정 규칙 변경 없음 · 실제 Ollama 결과는 CURRENT_STATE R5 참고 · 미커밋.

## R6. 설명을 deterministic explanation 중심 구조로 통일
- [x] 상태
- 승인: 완료 (2026-10-03 사용자 지시, DEC-16)
- 결과: 2026-10-03 · analysis/explanation_facts.py에 render_explanation(역할·강점·약점·확보/미확보 축·반영 못 한 조건·판단 한계·Critic 교체 이유·주의), facts에 region_count 추가·limits 제거 · agent/planner_loop.py: explain_from_facts(기본 설명 → 다듬기 1회 → validate_paraphrase → 실패 시 기본 설명), R5 검사를 fact_violations(전부 수집)로 형태만 변경, 리뷰 루프는 후보군이 있으면 answer 문장 대신 공용 경로, explain_candidates는 위임 · agent/planner.py AI 계획 실패 시에도 같은 경로로 설명 · app.py 표시(ai_paraphrase/deterministic) · tests/test_unified_explanation.py 11개 + AppTest 1개, 기존 설명 흐름 테스트(test_planner_loop 6개, test_candidate_explanation 7개, test_explanation_facts 1개, 스모크 1개)를 새 의미로 갱신 · 전체 468개 통과(skip 1) · scoring.py·후보 선정 규칙 변경 없음 · 실제 Ollama 결과는 CURRENT_STATE R6 · 미커밋.

## G4-A. 주거비 데이터 수집·정규화 (scoring 미연결)
- [!] 상태
- 승인: 완료 (2026-10-02 사용자 지시 - 아파트·오피스텔·연립다세대·단독/다가구 독립 수집)
- 범위·완료 조건: `CURRENT_STATE.md` G4 feasibility 표 A행 + "G4-A 진행".
- 결과: 2026-10-02 · 막힘 - 수집·분석 도구는 완성(scripts/collect_rent_transactions.py, scripts/analyze_rent_transactions.py, tests/test_rent_transactions.py 18개, 전체 418개 통과), 4개 API 공식 기술문서로 필드 확정 · 실제 호출은 4개 모두 resultCode 30(키 유효, 활용승인 미반영) → 실데이터 분석 미산출, G4-B 진행 불가 · 해제 조건: 승인된 키로 `python scripts/collect_rent_transactions.py inspect` 성공.

## G4-B. 주거비 평가축 (낮을수록 좋음 방향 플래그)
- [ ] 상태
- 승인: 필요 (scoring.py 변경 · OPEN-6, G4-A 선행)
- 범위·완료 조건: `CURRENT_STATE.md` G4 feasibility 표 B행.
- 결과:

## G4-C. 가성비 후보 역할 활성화
- [ ] 상태
- 승인: 필요 (후보 역할 의미 추가, G4-B 선행)
- 범위·완료 조건: `CURRENT_STATE.md` G4 feasibility 표 C행.
- 결과:

---

## 발견된 작업
(처리 중 새로 발견한 일을 여기에 추가)

### D1. "아직 점수에 반영되지 않은 입력정보"의 가중치 답변 항목이 출처를 구분하지 않음 (B2 처리 중 발견)
- [x] 상태
- 승인: 불필요
- 범위: `app.py:432-433` — 가중치 해석을 적용하지 않았을 때 항상 "AI 추가질문(가중치 확인) 답변 - …"으로 표시. 출처가 최초 입력의 '추가 요청사항'이면 바로 위(`app.py:413-416`)에서 이미 그 항목을 안내하므로 같은 내용이 잘못된 이름으로 한 번 더 나온다. 출처가 추가 요청사항이면 이 줄을 생략하거나 이름을 맞춘다.
- 완료 조건: 두 출처 각각 중복·오표기 없음, 전체 테스트 통과.
- 결과: 2026-10-02 · app.py — 'AI 추가질문(가중치 확인) 답변' 줄을 출처가 추가 요청사항이 아닐 때만 표시(조건 1개 추가) · tests/test_pages_smoke.py에 AppTest 흐름 테스트 2개(추가 요청사항 비율 → 중복 없음 / AI 추가질문 답변 → 기존 줄 유지), 수정 전 조건에서 실패·수정 후 통과 확인 · 전체 279개 통과(skip 1).
