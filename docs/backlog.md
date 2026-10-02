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
- [ ] 상태
- 승인: 불필요
- 범위: `app.py:1115` — 추가 요청사항에서 온 비율을 승인해도 reason이 "AI 추가질문 답변을 승인해…"로 저장됨. `weight_interpretation_source`에 따라 문구를 나눈다.
- 완료 조건: 두 출처 각각 올바른 문구, 기존 화면 동작 불변, 전체 테스트 통과.
- 결과:

## B3. AI 응답 JSON 추출을 안전하게 + 중복 함수 정리
- [ ] 상태
- 승인: 불필요
- 범위: `agent/planner.py:146`과 `agent/location_agent.py:235`의 `_parse_plan`(탐욕적 `\{.*\}` 정규식), `_sanitize_goals`/`_sanitize_unsupported_requests`가 두 파일에 중복. 공용 모듈(예: `agent/llm_json.py`)로 옮기고, 응답 안의 **첫 번째 완전한 JSON 객체**를 `json.JSONDecoder.raw_decode`로 찾도록 바꾼다. `agent/ollama_agent.py`의 같은 패턴(`_parse_questions`, `_parse_weight_feedback`)도 같은 헬퍼를 쓰게 한다.
- 완료 조건: 기존 테스트 전부 통과 + "JSON 앞뒤에 설명 문장", "JSON 객체 2개" 응답 케이스 테스트 추가. 기존 반환 형식과 sanitize 길이 제한은 그대로.
- 결과:

## B4. 위치 Agent 답변 속 숫자 검증
- [ ] 상태
- 승인: 불필요
- 범위: `agent/location_agent.py`의 `_build_agent_result()`. Claude의 `agent_answer`에 나온 숫자(개수·거리 m)가 실제 실행한 도구 결과(`total_count`, `straight_distance_m`, 반경별 counts 등)에 존재하는지 Python이 확인한다. 반경(300/500/1000)·순위 같은 질문 맥락 숫자는 허용 목록으로 처리.
  - 근거 없는 숫자가 있으면 결과에 `answer_verification`(검증 결과·문제 숫자)을 담고, `pages/user.py`에서 답변 위에 "⚠️ 일부 수치를 실제 조회 결과에서 확인하지 못했습니다 - 아래 표를 기준으로 보세요"를 표시한다. 답변 문장을 임의로 고치지는 않는다.
- 완료 조건: 일치/불일치 케이스 단위 테스트, 전체 테스트 통과, 화면 반영(브라우저 확인 못 하면 "수동 확인 필요" 기록).
- 결과:

## B5. services 공통 유틸 중복 정리
- [ ] 상태
- 승인: 불필요
- 범위: `services/bus_stops.py`와 `services/convenience.py`에 같은 `_haversine_m`, `_to_float`, `_to_int`, `DISTRICTS`, `_CHANGWON_BBOX`가 있고 `pages/user.py:73`에도 `_CHANGWON_BBOX`가 있다. `services/geo.py`(가칭)로 옮겨 재사용한다. 지구 반지름·bbox 값은 그대로.
- 완료 조건: 동작 변화 없음(버스 공식 집계 검증 포함 전체 테스트 통과), 중복 정의 제거.
- 결과:

## B6. scoring의 private 함수 공개 이름 제공
- [ ] 상태
- 승인: 불필요
- 범위: `analysis/scoring.py:91` `_collect_confirmed_indicator`를 `agent/planner.py`, `analysis/simulation.py`가 직접 import한다. 공개 이름 `collect_confirmed_indicator`를 추가하고(기존 이름은 별칭으로 유지) 호출부를 공개 이름으로 바꾼다. 계산 로직은 건드리지 않는다.
- 완료 조건: 전체 테스트 통과, 계산식 diff 없음.
- 결과:

## B7. Streamlit 화면 스모크 테스트 추가
- [ ] 상태
- 승인: 불필요
- 범위: `streamlit.testing.v1.AppTest`로 `app.py`, `pages/user.py`, `pages/government.py`가 예외 없이 첫 화면을 그리는지 확인하는 테스트(`tests/test_pages_smoke.py`). Ollama·claude 호출은 모킹하거나 버튼을 누르지 않는 범위로 제한한다. folium 지도는 AppTest에서 렌더링되지 않을 수 있으니 예외 없이 지나가는지만 본다.
- 완료 조건: 새 테스트가 실제 AI 호출 없이 통과, 전체 테스트 시간 큰 증가 없음(대략 +30초 이내).
- 결과:

## B8. CSV의 hospital_count 지표명을 "의료기관 수"로 변경
- [ ] 상태
- 승인: 필요 (원본 데이터 파일 `data/region_indicators.csv` 수정, `.claude/settings.json`에서 Edit 차단 중)
- 범위: 5개 행의 `indicator_name` "병원 수" → "의료기관 수". 수치·출처·기준일은 그대로. 수집 스크립트(`scripts/ingest_hira_hospital_data.py`)가 다시 생성할 때도 같은 이름을 쓰도록 맞춘다.
- 결과:

## B9. 빈 스텁 파일 정리
- [ ] 상태
- 승인: 필요 (팀원이 쓸 예정인 파일일 수 있음)
- 범위: `agent/claude_agent.py`, `agent/agent_state.py`, `services/housing.py`, `services/medical.py`, `services/transport.py`, `database/db.py` — 주석 한 줄뿐. 삭제할지, 역할 설명 docstring을 채울지 결정 필요.
- 결과:

## B10. README 현행화
- [ ] 상태
- 승인: 불필요
- 범위: 현재 README는 두 줄이고 "경남·주거비 기반 추천"이라 실제(창원시 5개 구, 주거비 미확보)와 다르다. 실행 방법, 화면 3개, 확보/미확보 데이터, Agent 구조, 테스트 방법을 CLAUDE.md와 모순 없이 정리한다.
- 완료 조건: 실제 코드·데이터와 일치하는 내용만, 과장 표현 없음.
- 결과:

---

## 발견된 작업
(처리 중 새로 발견한 일을 여기에 추가)
