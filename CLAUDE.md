# 경남 이주자 맞춤형 생활권 탐색 AI — 개발 Agent 지침

이 파일은 이 저장소에서 작업하는 Claude Code(개발 Agent)가 매 세션 자동으로 읽는 공통 규칙이다.
팀원 모두 같은 규칙을 쓰므로, 개인 취향·개인 계정 정보는 여기 넣지 말고 `CLAUDE.local.md`(커밋 안 함)에 둔다.

## 1. 이 서비스의 핵심 원칙 (모든 작업에 우선)

이 프로젝트는 AI가 지역 정보를 지어내는 추천 서비스가 아니다.

- 시설 수·점수 계산은 **Python**이 한다. AI는 요청 해석, 분석 계획, 도구 선택, 설명만 한다.
- AI가 만든 숫자를 실제 데이터처럼 쓰지 않는다. AI의 Tool call·가중치 제안은 **Python이 검증한 뒤에만** 실행한다.
- 데이터가 없으면 0이 아니라 **"미확보"**로 표시한다. 없는 데이터를 추정·대체 조회하지 않는다.
- 공공데이터의 **출처와 기준일**을 화면·결과에서 유지한다.
- 사용자가 승인하지 않은 조건(가중치, 검색 위치 등)을 임의로 적용하지 않는다.
- "AI가 제안한 계획"과 "Python이 실제 실행한 작업"을 화면에서 항상 구분한다.

## 2. 구조

| 영역 | 파일 | 내용 |
|---|---|---|
| 이주자용 5개 구 비교 | `app.py` | 입력 → AI 추가질문/가중치 확인(승인) → Agent Planner → min-max 상대 비교 → 자연어·슬라이더 피드백 |
| 위치 기반 탐색 | `pages/user.py` | 확정 좌표 주변 300m/500m/1km 버스정류장·편의점, Folium 지도, 위치 AI Agent |
| 정부용 분석 | `pages/government.py` | 구별 시설 수 비교, 차이, 가상 시설 증감 시뮬레이션 |
| 점수 계산 | `analysis/scoring.py` | min-max 정규화 + 가중합 (유일한 점수 계산 경로) |
| 시뮬레이션 | `analysis/simulation.py` | deepcopy 사본에 가상값 → `compute_region_scores_from_weights()` 재사용 |
| 데이터 조회 | `services/region_data.py`, `services/bus_stops.py`, `services/convenience.py`, `services/map_markers.py` | CSV 읽기 전용 |
| 수집·집계 | `scripts/` | 공공데이터 수집, 버스정류장 공간판정·집계 |

**AI Agent**
- `agent/ollama_agent.py` (Ollama qwen3.5:4b): 추가질문 생성, 자연어 가중치 해석. 승인 전에는 적용하지 않는다.
- `agent/planner.py` (Ollama): 5개 구 분석 도구 계획. 가중치는 항상 사용자가 승인한 값으로 강제 치환한다.
- `agent/location_agent.py` + `agent/location_mcp_server.py`: 위치 기반 Agent. `LLM_BACKEND=claude_cli`면 `claude -p` + stdio MCP 서버(Claude가 도구를 반복 호출), `ollama`면 계획형 + `agent_loop` 결과 검토로 동작하고, 실패 시 계획형 → 기본 절차로 폴백. `LOCATION_AGENT_BACKEND`(`claude_agent`/`claude_cli`/`ollama`)는 명시했을 때만 이를 덮어쓴다.
- `agent/llm.py` + `agent/claude_cli.py`: LLM 호출 공통 창구. `LLM_BACKEND`(`ollama`/`claude_cli`)로 백엔드를 고르고, 세션·일일 호출 수와 입력 길이를 제한한다. 새 AI 호출은 `ollama.chat` 대신 `llm.chat`을 쓴다.
- `agent/planner_loop.py`: 5개 구 점수 계산 뒤 AI가 결과를 보고 설명 작성·참고 지표 조회·가중치 가정 계산을 반복(최대 3회). 실제 추천 점수는 바꾸지 않는다.
- `agent/agent_loop.py`: 위치 Agent 계획형 경로의 결과 검토 루프(관찰 → 판단 → 행동, 최대 3회). AI 답변 숫자를 검증해 실패하면 Python 요약으로 대체한다. MCP 반복형 답변도 같은 `verify_answer` 기준을 쓰며, 두 경로 모두 결과의 `final_answer`(source: ai_verified / python_summary)로 화면·대화 기억에 전달된다. `agent/agent_state.py`의 `ConversationMemory`가 같은 위치의 최근 대화를 후속 질문 맥락으로 넘긴다.
- 모든 Ollama 호출은 `think=False`를 유지한다(qwen3.5의 thinking이 토큰을 소모해 응답이 비는 문제).

`app.py`의 구별 점수와 `pages/user.py`의 위치 주변 시설 수는 **서로 다른 분석**이다. 둘을 섞어 새 점수를 만들지 않는다.

## 3. 데이터 (기준값은 CSV가 원본, 아래는 검증용 참고치)

| 지표 | 의창 | 성산 | 마산합포 | 마산회원 | 진해 | 합계 | 파일 |
|---|---|---|---|---|---|---|---|
| bus_stop_count | 831 | 452 | 760 | 365 | 518 | 2,926 | `data/raw/changwon_bus_stops.csv` (원본 3,526, 경계 밖 600 제외) |
| hospital_count | 262 | 398 | 242 | 256 | 200 | 1,358 | `data/region_indicators.csv` (구별 집계만, 위치 데이터 없음) |
| convenience_store_count | 199 | 245 | 159 | 151 | 196 | 950 | `data/convenience/*.csv` |

- `data/region_indicators.csv`가 운영 지표다. `data/region_indicators_sample_dev.csv`는 개발용 더미이며 실제 추천에 쓰지 않는다(`include_dev_sample=False` 기본).
- 버스정류장: 정류소아이디 기준, SGIS 행정경계 공간판정, 보정 3건(`SPATIAL_OVERRIDES`)·품질 예외 8건 기준을 유지한다. `services.bus_stops.verify_official_counts()`가 원본으로 다시 세서 검증한다.
- 편의점: "상가정보에 **등록된 업소 수**"다. 동일 주소 중복 등록 가능성이 있어 실제 영업 매장 수라고 단정하지 않는다.
- 의료: 의원·치과의원·한의원·보건소 등 전 종별 합산이다. "종합병원 수"·"병원 수"로 단정하지 말고 **"의료기관 수"**로 표현한다.
- 원본 CSV·GeoJSON은 수정하지 않는다. 데이터 수치를 코드에 하드코딩하지 않는다(기존 `EXPECTED_COUNTS` 같은 검증용 상수는 예외).

**현재 지원하지 않는 데이터** — 질문받으면 이유를 설명하고, 다른 데이터로 억지로 대신 답하지 않는다:
실제 버스 이동시간·통근시간, 배차간격, 실시간 도착정보, 도보경로, 위치 기반 의료기관 검색, 월세·전세(실거래·매물), 범죄율·안전, 교육·자연환경·문화시설, 대형마트 수, 응급실 운영 병원 수, 종합적인 거주 적합도 확정.

## 4. 점수와 거리 해석

- 점수: 각 지표를 창원시 5개 구 사이에서 min-max 정규화(0~100) → 승인된 가중치로 가중합. 값이 모두 같으면 50점. 5개 구 전부 `확보`인 지표만 사용.
- **100점 = 5개 구 중 그 지표 값이 가장 높다는 뜻일 뿐**, 완벽한 거주지가 아니다. 인구·면적·접근성·통근시간을 보정하지 않은 "시설 수 기반 상대 비교"다.
- `analysis/scoring.py` 계산식은 임의로 바꾸지 않는다. 바꿔야 하면 먼저 보고한다.
- 시뮬레이션은 실제 정책 효과 예측이 아니다 — "시설 수가 가상으로 바뀌면 상대 점수가 어떻게 변하는가"만 보여준다.
- 위치 기반 거리는 **하버사인 직선거리**다. "500m 안"은 직선거리 500m이며 도보 500m·도보 5분·실제 이동거리·접근성 보장이 아니다. 화면과 AI 설명 모두에서 구분한다.

## 5. 위치 Agent 고유 규칙

- 검색 중심 좌표와 반경은 Python이 소유한다. AI는 바꿀 수 없고, MCP 도구 인자에 lat/lon/radius를 노출하지 않는다.
- 자연어에 명시된 반경이 300/500/1000m가 아니면 AI를 호출하지 않고 거절한다.
- 사용자가 요청하지 않은 시설·비교 도구는 Python guardrail이 차단한다. "가장 가까운" 요청은 max_results=1.
- 지도 클릭 좌표(`map_click_candidate`)는 사용자 승인 전까지 검색에 쓰지 않는다. Agent는 버튼 클릭 때만 실행한다.

## 6. 작업 방식

`/dev <요청>`(`.claude/skills/dev/SKILL.md`)으로 부르면 아래 절차를 그대로 수행한다.
무엇을 할지 스스로 찾아 진행하는 자율 루프는 `/steward`다(10절).

요청 유형에 따라 다르게 행동한다.
- "어떻게 만드는 게 좋을까?" 같은 질문 → 설계안만 제안하고 코드는 수정하지 않는다.
- "개발해줘 / 구현해줘 / 진행해 / 수정해줘" → 아래 절차를 끝까지 수행한다.

1. `git status`, `git branch --show-current`로 상태를 확인한다. 작업자 본인 브랜치가 아니면 멈추고 알린다.
2. 관련 코드를 실제로 읽고 기존 함수·데이터 흐름을 파악한다(설명을 그대로 믿지 말고 파일로 검증).
3. 기존 함수를 재사용하고, 같은 계산을 여러 파일에 중복 구현하지 않는다. 수정 범위는 최소로.
4. 기존 기능의 의미를 바꾸는 수정은 먼저 보고한다.
5. 테스트를 실행하고, 실패하면 원인을 고친다(아래 7절).
6. `git diff`를 검토하고 변경 파일·테스트 결과를 보고한다. **실제로 확인한 것과 확인하지 못한 것을 구분**한다.

## 7. 테스트

```
python -m unittest discover -s tests        # 전체 (약 1초, 실제 AI 호출 없음)
python -m unittest tests.test_location_agent -v
```

- Stop hook(`.claude/hooks/run_tests_on_stop.py`): 작업을 끝낼 때 `.claude/` 밖의 .py가 바뀌어 있으면 전체 테스트를 자동 실행하고, 실패하면 종료를 막아 고치게 한다(같은 실패로는 한 번만 막음).
- 수정 기능 관련 테스트 + 전체 테스트를 모두 돌린다. 가능하면 `streamlit run app.py`로 화면도 확인한다.
- `tests/test_pages_smoke.py`가 AppTest로 3개 화면의 첫 렌더링(예외 없음, 첫 화면에서 AI 미호출)을 확인한다. 버튼 클릭 흐름은 여전히 브라우저 확인이 필요하다.
- 단위 테스트는 `ollama.chat`·`subprocess.run`(claude CLI)을 모킹한다. 실제 Ollama 스모크 테스트는 `LOCATION_AGENT_REAL_OLLAMA_TEST=1`일 때만 실행된다.
- 보고할 때 모킹 테스트 / 실제 Claude·Ollama 호출 / 브라우저 확인을 구분한다. 브라우저에서만 확인 가능한 항목은 "브라우저 수동 확인 필요"라고 명시한다.
- "테스트 통과"는 실제로 실행한 테스트에 대해서만 말한다.

## 8. Git

- 각자 자기 브랜치에서만 작업한다. 다른 팀원의 브랜치는 수정하지 않는다.
- **force push 금지.**
- commit은 사용자가 요청할 때만 한다. push 중 권한 오류가 나면 반복 시도하지 말고 오류를 그대로 보고한다.
- `.env`, 비밀키, `.claude/settings.local.json`, `CLAUDE.local.md`는 커밋하지 않는다.

## 9. 실행 환경

- Python 3.12, `pip install -r requirements.txt`
- 공공데이터 키는 `.env`(`.env.example` 참고): `HIRA_SERVICE_KEY`, `SBIZ_SERVICE_KEY` — 수집 스크립트에만 필요.
- Ollama: `ollama pull qwen3.5:4b` 후 서버 실행.
- 위치 Agent 기본 모드는 실행 PC에 Claude Code CLI(`claude`) 설치·로그인이 필요하다. 없으면 자동으로 Ollama로 폴백한다.

## 10. 프로젝트 기억과 자율 개발 루프 (`/steward`)

주제·대회 기준을 잃지 않도록 Agent가 스스로 읽고 갱신하는 기억 저장소를 둔다. 사람이 손으로 관리하지 않아도 된다.

| 파일 | 역할 |
|---|---|
| `docs/agent/PROJECT_CHARTER.md` | 프로젝트 헌법 — 문제·사용자·MVP·핵심 Workflow·하지 않을 것·새 작업 채택 6문항 (사용자 승인 시에만 변경) |
| `docs/agent/COMPETITION_REQUIREMENTS.md` | 공식 대회 요구사항(R-*), 항목마다 원본 파일·쪽 |
| `docs/agent/EVAL_MATRIX.md` | 평가항목별 증거와 공백 순위 |
| `docs/agent/CURRENT_STATE.md` | 실제 코드·데이터·테스트 상태 |
| `docs/agent/DECISIONS.md` | 확정 결정(DEC-*)·열린 결정(OPEN-*) — 확정된 것은 다시 논의하지 않는다 |
| `docs/agent/REFERENCE_INDEX.md` | 참고자료 색인(자동 생성) |
| `.agent_state/state.json`, `.agent_state/reference_index.json` | 기계용 상태·자료 해시 |
| `references/` | 공식 대회자료·팀 자료 원본(읽기 전용) |

- 새 기능을 만들기 전에 `PROJECT_CHARTER.md` §7의 6문항으로 주제 안에 있는지 판단한다. 대부분 "아니오"면 구현하지 말고 보고한다.
- 서비스가 부동산 추천·단순 지도 검색·단순 챗봇·지역 순위·관광 추천·범용 비서로 변질되는 변경은 하지 않는다.
- 자료 충돌 시 우선순위: 공식 공고·운영규정 > 사업설명회 > 교육자료 > 사용자 확정 방향 > 코드·데이터 > 기존 기획 문서. 단 구현 상태는 코드·데이터가 우선.
- 발표·제출 문서에 구현하지 않은 기능을 구현된 것처럼 쓰지 않는다. 사실(FACT)·구현(IMPLEMENTED)·계획(PLANNED)·추론(INFERENCE)·미확인(UNKNOWN)을 구분한다.
- 세션 시작 hook(`.claude/hooks/session_status.py`)이 브랜치·새 참고자료·상태 문서 이후 커밋·마감 D-day를 알려준다.
- 자료 추출: `python .claude/scripts/ref_scan.py status | extract | mark | render` (PDF·HWP 추출에는 `pip install -r requirements-dev.txt`).
- `/steward`는 `jhy-next`에서만 코드를 수정한다. 다른 브랜치에서는 점검·보고만 한다.
