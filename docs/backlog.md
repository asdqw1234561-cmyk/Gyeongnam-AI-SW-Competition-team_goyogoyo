# 개발 백로그

개발 Agent(`/loop`, `/dev`)가 위에서부터 하나씩 처리하는 작업 목록이다.

## 처리 규칙
- 상태가 `[ ]`이고 **승인: 불필요**인 항목 중 가장 위의 것 하나만 처리한다. 한 번에 한 항목.
- **승인: 필요** 항목은 처리하지 않고 건너뛴다(사용자가 `승인: 완료`로 바꾸면 처리).
- 처리 절차는 `.claude/skills/dev/SKILL.md`와 `CLAUDE.md`를 따른다.
- 끝나면 상태를 `[x]`로 바꾸고 `결과:` 줄에 날짜·변경 파일·테스트 결과를 한 줄로 남긴다.
- 막히면(테스트를 통과시킬 수 없음, 범위가 예상보다 큼, 원칙 충돌) 상태를 `[!]`로 바꾸고 `결과:`에 이유를 적은 뒤 다음 항목으로 넘어가지 말고 멈춘다.
- 항목을 처리하다 새로 발견한 일은 맨 아래 "발견된 작업"에 추가만 하고 바로 처리하지 않는다.
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
- [!] 상태
- 승인: 완료 (2026-10-02 사용자 승인. 단 `.claude/settings.json`의 `Edit(./data/region_indicators.csv)` 차단은 **아직 유지 중**이며 사용자가 직접 해제해야 한다. 해제 전에는 CSV 수정 단계에서 `[!]`로 멈추고, Bash 등으로 우회 수정하지 않는다. 이 항목 범위 밖의 값·행은 수정하지 않는다)
- 범위: 5개 행의 `indicator_name` "병원 수" → "의료기관 수". 수치·출처·기준일은 그대로. 수집 스크립트(`scripts/ingest_hira_hospital_data.py`)가 다시 생성할 때도 같은 이름을 쓰도록 맞춘다.
- 결과: 2026-10-02 막힘 · `.claude/settings.json`의 deny에 `Edit(./data/region_indicators.csv)`가 아직 있어 CSV를 수정할 수 없음(Agent가 권한 규칙을 직접 고치는 것은 auto 모드에서 차단됨). 사용자가 그 한 줄을 지운 뒤 상태를 `[ ]`로 되돌리면 처리 가능. 코드 변경 없음.

## B9. 빈 스텁 파일 정리
- [ ] 상태
- 승인: 필요 (팀원이 쓸 예정인 파일일 수 있음)
- 범위: `agent/claude_agent.py`, `agent/agent_state.py`, `services/housing.py`, `services/medical.py`, `services/transport.py`, `database/db.py` — 주석 한 줄뿐. 삭제할지, 역할 설명 docstring을 채울지 결정 필요.
- 결과:

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

## 발견된 작업
(처리 중 새로 발견한 일을 여기에 추가)

### D1. "아직 점수에 반영되지 않은 입력정보"의 가중치 답변 항목이 출처를 구분하지 않음 (B2 처리 중 발견)
- [ ] 상태
- 승인: 불필요
- 범위: `app.py:432-433` — 가중치 해석을 적용하지 않았을 때 항상 "AI 추가질문(가중치 확인) 답변 - …"으로 표시. 출처가 최초 입력의 '추가 요청사항'이면 바로 위(`app.py:413-416`)에서 이미 그 항목을 안내하므로 같은 내용이 잘못된 이름으로 한 번 더 나온다. 출처가 추가 요청사항이면 이 줄을 생략하거나 이름을 맞춘다.
- 완료 조건: 두 출처 각각 중복·오표기 없음, 전체 테스트 통과.
- 결과:
