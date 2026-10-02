# 현재 프로젝트 상태 (CURRENT STATE)

> 실제 코드·데이터 기준의 현재 상태다. 문서·기획보다 이 파일과 코드가 우선한다.
> `/steward`가 코드 변경 후 스스로 갱신하고, `.agent_state/state.json`의 `last_synced_commit`을 맞춘다.
> 상세 구조·규칙은 `CLAUDE.md`, 작업 이력은 `docs/backlog.md`.

- 최종 확인: 2026-10-02
- 브랜치: `jhy-next` · 기준 커밋: `219a4f2` ([R1] 정착 후보군 역할 부여 + Critic 점검)
- 제출 마감: 2026-10-06 12:00 (R-SCH-2, 재확인 필요) — 확인일 기준 D-4

## 구현된 기능 (IMPLEMENTED)

| # | 핵심기능 | 위치 | 상태 |
|---|---|---|---|
| F1 | 이주 조건 입력 → AI 추가질문 → 가중치 해석·승인 | `app.py`, `agent/ollama_agent.py` | 동작(모킹 테스트·AppTest 일부), 브라우저 클릭 흐름 수동 확인 필요 |
| F2 | Agent Planner: AI 계획 → Python 검증 → 도구 실행 → 5개 구 상대 비교 점수 | `agent/planner.py`, `analysis/scoring.py` | 동작 |
| F3 | 결과 검토 루프: AI 설명 → 숫자 검증 → 실패 시 재작성/Python 요약, 자연어·슬라이더 피드백 재계산 | `agent/planner_loop.py`, `app.py` | 동작 |
| F4 | 위치 기반 주변 시설 탐색(300m/500m/1km 직선거리, 버스정류장·편의점, 지도 클릭 승인) | `pages/user.py`, `services/bus_stops.py`, `services/convenience.py` | 동작 |
| F5 | 위치 AI Agent: MCP 반복형(claude_cli) / 계획형+검토 루프(ollama) / 기본 절차 폴백, 답변 숫자 검증, 같은 위치 대화 기억 | `agent/location_agent.py`, `agent/location_mcp_server.py`, `agent/agent_loop.py`, `agent/agent_state.py` | 동작 |
| F7 | 정착 후보군 + Critic: 최적·균형·대안(가성비는 주거비 미확보로 산출 불가), 6개 평가축 상태, Critic 점검·지배된 대안 수정 — 최초·피드백 결과 모두 | `analysis/candidates.py`, `agent/planner.py`(`candidate_review`), `app.py` `_render_candidate_set` | 동작(단위·AppTest), 실제 Ollama·브라우저 미확인 |
| F6 | 정부용 구별 시설 현황·가상 증감 시뮬레이션 | `pages/government.py`, `analysis/simulation.py` | 동작 (주제 연결은 OPEN-3) |

공통: `agent/llm.py`(백엔드 선택·호출 수·입력 길이 제한), `agent/llm_json.py`(JSON 추출).

## 데이터

| 구분 | 내용 |
|---|---|
| 확보(구별) | 버스정류장 수 2,926 / 의료기관 수 1,358 / 편의점 등록 업소 수 950 — `data/region_indicators.csv` |
| 확보(위치) | 버스정류장(`data/raw/changwon_bus_stops.csv`), 편의점(`data/convenience/changwon_convenience_stores.csv`) |
| 미확보 | 통근·이동시간, 배차간격, 실시간 도착, 도보경로, 위치 기반 의료기관, 월세·전세, 범죄율·안전, 교육·자연·문화, 대형마트, 응급실, 종합 거주 적합도 |

## 테스트

- 단위 테스트: `python -m unittest discover -s tests` → **377개 통과, skip 1** (2026-10-02 R1 반영 후 확인, 실제 AI 호출 없음)
- AppTest 스모크: 3개 화면 첫 렌더링 + 일부 흐름
- 브라우저 E2E 자동 확인: 없음 (backlog N4)
- 제출용 대표 Test Case 5건 기록: 없음

## 추천 품질 분석 — "정착 후보군 탐색 Agent" 기준 차이 (2026-10-02, 기준 커밋 `78ec6e0`)

현재 최우선 목표: 제출자료가 아니라 **핵심 Agent 추천 품질**(DEC-11). 목표는 "가장 좋은 구 하나"가 아니라 "사용자 조건에 따라 장단점이 다른 현실적인 정착 후보군 탐색"이다.

**현재 구조 (FACT, 코드 확인)**: `analysis/scoring.py` `_build_ok_result()`가 지표별 min-max 정규화 → 승인 가중치 합 `total_score` → 내림차순 정렬 → `top_candidates = region_scores[:candidate_count]`. 즉 **단일 총점 순위의 앞부분 자르기**다. 화면(`app.py` `_render_top_candidates`)도 "N위 — 구 · 종합점수"로만 보여준다.
실제 데이터 동일 가중치 결과(INFERENCE, CSV로 계산): 성산 72.9 > 의창 60.8 > 마산합포 37.1 > 진해 27.0 > 마산회원 9.4. 세 축 모두에서 지배되지 않는 구(Pareto)는 의창(교통 1위)·성산(의료·편의 1위) 둘뿐이고, 나머지 셋은 의창에 모든 축에서 뒤진다 — 이런 장단점 구조가 지금 화면에는 드러나지 않는다.

| 요구사항 | 현재 상태 | 판정 |
|---|---|---|
| 1. 독립 평가축(주거비·교통·의료·교육·생활편의·직장 접근성) | 교통·의료·생활편의 3축만 `component_scores`로 축별 보존. 주거비·교육·직장 접근성은 축 자체가 없고 입력(주거비 예산·직장 위치)은 받기만 하고 무시, `excluded_conditions`에만 남음 | 부분 |
| 2. 성격이 다른 후보(최적/균형/가성비/대안) | 없음. 총점 순위 상위 N개 | **없음** |
| 3. Critic — 후보 쏠림 탐지 | 없음. `planner_loop`는 AI 설명 속 숫자만 검증하고 후보 집합 품질(근소차·지배관계·단일 지표 의존·쏠림)은 보지 않음. 후보 단위가 구(5개)라 "같은 생활권 쏠림"은 행정동 단위 데이터 없이는 판단 불가(UNKNOWN) | **없음** |
| 4. 방향성 피드백("병원을 더 중요하게") → 재평가 | 숫자가 있으면(예: 의료 80%) 승인 후 결정적 재계산 + 최초 대비 순위 변화 표시. "더 중요하게"처럼 숫자 없는 요청은 `ask_clarification`으로 되묻기만 함(`agent/ollama_agent.py:250`). "집값"은 미확보로 올바르게 거절 | 부분 |
| 5. LLM 점수 생성 금지 | 승인 가중치 강제 치환, 점수는 `scoring.py`만 계산, AI 설명 숫자 검증 | 충족 |
| 6. 데이터 부족 시 불확실성 표시 | 지표 단위 "미확보" 표시는 있음. 후보별로 "평가축 6개 중 몇 개로 판단했는지", 근소차로 순위가 불안정한지 같은 불확실성은 없음 | 부분 |
| 7. Goal → Planning → Tool Use → 후보 생성 → 평가 → Critic → 결과 → Feedback → Memory | Goal(추가질문)·Planning(planner)·Tool Use·평가(scoring)·결과·Feedback(슬라이더/숫자 자연어) 있음. **후보 생성은 순위 자르기, Critic 없음**. Memory는 최초 결과 대비 비교만(피드백 이력·누적 없음) | 부분 |

### 가장 영향이 큰 부족점 3개

1. **G1. 후보 생성이 "총점 순위 자르기"뿐** — 최적·균형·대안처럼 성격이 다른 후보와 후보별 강점·약점 축이 없다. "가장 좋은 구 찾기" 서비스(CHARTER §5의 '단순 지역 순위 서비스')에 가깝게 보인다. (요구 1·2, R-EV-2 판단·추론, R-EV-4 차별성)
2. **G2. Critic 단계 부재** — 후보 집합을 결정적으로 점검하는 단계가 없다: 1·2위 근소차, 한 지표에만 기댄 1위, 다른 후보에 모든 축에서 지배되는 후보, 후보가 한 구로 쏠림, 데이터가 없는 평가축 비율. Agent Workflow의 "평가 → Critic" 연결이 끊겨 있다. (요구 3·6·7, R-AG-5 "결과 오류를 확인하고 수정")
3. **G3. 방향성 피드백이 재평가로 이어지지 않음** — "병원을 더 중요하게"는 되묻기만 하고, 현재 가중치를 기준으로 결정적으로 조정하는 규칙이 없다. 피드백 이력도 남지 않아 Memory가 "최초 결과 대비"에 그친다. (요구 4·7, R-EV-2 Memory·Feedback)

선택(STEP 6): G1+G2를 하나의 결정적 모듈로 묶어 먼저 구현한다 — 새 점수식 없이 기존 `score_result`의 축별 정규화 점수만으로 후보 역할 부여와 Critic 점검을 수행한다. G3는 다음 후보.

**진행 (2026-10-02, backlog R1):** G1·G2 구현 완료(F7, 커밋 219a4f2). 흐름은 이제 Goal → Planning → Tool Use → 평가(scoring) → **후보 생성 → Critic(점검·대안 수정)** → 결과 → Feedback(재평가 시 후보·Critic 다시 계산)까지 이어진다.
남은 것: **G3**(방향성 피드백·피드백 이력 Memory), Critic 결과가 아직 AI 설명(planner_loop 관찰)에는 들어가지 않음, "같은 생활권 쏠림"은 행정동 단위 데이터가 없어 판단 불가로 표시만 함.

## 알려진 문제 / 공백

- 구 비교 결과 → 위치 탐색 화면 연결 없음(시연 흐름이 끊김) — EVAL_MATRIX 공백 3
- 제출물 5종·출처 신고서 미작성 — EVAL_MATRIX 공백 1
- 신청 지정주제 미기록 — DECISIONS OPEN-1
- 버튼 클릭 흐름·실제 AI 시연은 브라우저 수동 확인 필요

## 진행 중 / 대기 작업

- `docs/backlog.md` N3(위치 Agent 경로 단순화 재평가), N4(브라우저 자동 확인 절차) — 대기
- 다음 후보는 `/steward` 실행 시 EVAL_MATRIX 공백 기준으로 다시 우선순위를 정한다.

## 팀 브랜치 (참고)

`main`, `jhy`, `jhy-next`(이 Agent 작업 브랜치), `hwang`, `hwang2`, `hwang3`(팀원). 다른 팀원 브랜치는 수정하지 않는다.
