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
- `agent/location_agent.py` + `agent/location_mcp_server.py`: 위치 기반 Agent. 기본은 `claude -p` + stdio MCP 서버(Claude가 도구를 반복 호출), 실패 시 Ollama 계획 → 기본 절차로 폴백. `LOCATION_AGENT_BACKEND`(`claude_agent`/`claude_cli`/`ollama`)로 전환.
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
