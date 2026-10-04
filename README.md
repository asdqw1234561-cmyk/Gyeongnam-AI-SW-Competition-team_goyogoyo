# Gyeongnam-AI-SW-Competition-team_goyogoyo

경남 이주자 맞춤형 생활권 탐색 AI — **경상남도 22개 지역**(창원시 5개 구 + 시 7곳 + 군 10곳)을 대상으로, 공공데이터로 확보한 시설 수 기준 생활권 비교(같은 유형끼리, 인구 1만 명당)와 위치 기반 주변 시설 탐색, 행정용 시설 현황 분석을 제공한다.

> 이 서비스는 AI가 지역 정보를 지어내는 추천 서비스가 아니다. 시설 수·점수 계산은 Python이 하고, AI는 요청 해석·분석 계획·도구 선택·설명만 담당한다. 데이터가 없는 항목은 "미확보"로 표시한다. 자세한 원칙은 [`CLAUDE.md`](CLAUDE.md) 참고.

## 화면

| 화면 | 파일 | 내용 |
|---|---|---|
| 이주자용 생활권 비교 | `app.py` | 비교 범위 선택(창원시 5개 구 / 경남 시 7곳 / 경남 군 10곳) → AI 추가질문·가중치 확인(사용자 승인) → Agent Planner → 인구 1만 명당 상대 비교 점수 → 성격이 다른 후보·Critic 점검 → 자연어·슬라이더 피드백 재평가 |
| 관심 위치 주변 탐색 | `pages/user.py` | 지도 클릭·좌표 입력으로 경남 안의 위치 확정 → 300m/500m/1km 주변 버스정류장·편의점, Folium 지도, 위치 AI Agent |
| 정부용 시설 현황 분석 | `pages/government.py` | 비교 범위별 지역 시설 수 비교·차이, 가상 시설 증감 시뮬레이션(실제 정책 효과 예측 아님) |

## 데이터

| 지표 | 상태 | 출처 · 기준일 |
|---|---|---|
| 버스정류장 수 (`bus_stop_count`) | 확보 — 22개 지역 18,207개(각 시·군이 직접 등록한 정류장 중 경계 안) | 국토교통부 전국 버스정류장 위치정보(data.go.kr 15067528) 2025-10-31 + SGIS 시군구 경계 2025-06-30 |
| 의료기관 수 (`hospital_count`) | 확보 — 4,256개, 의원·치과·한의원·보건소 등 전 종별 합산 | 건강보험심사평가원 병원정보서비스(data.go.kr 15001698) 2026-10-04 |
| 편의점 수 (`convenience_store_count`) | 확보 — 상가정보 등록 업소 3,305개 | 소상공인 상가(상권)정보(data.go.kr 15012005) 2026-06 |
| 주민등록 인구 (`population`) | 확보 — 3,191,645명(인구 1만 명당 비교의 분모, 점수 축 아님) | 행정안전부 주민등록 인구(data.go.kr 15097972) 2026-08-31 |
| 초·중·고 학교 수 | 확보 — 986교, **참고정보로만 표시**(점수 미사용) | 전국초중등학교위치표준데이터(data.go.kr 15021148) 2026-03-20 |
| 대중교통 소요시간, 응급실 운영 병원, 대형마트, 월세·전세 | **미확보** | — |

- 운영 지표: `data/region_indicators.csv` — `scripts/build_gyeongnam_indicators.py`가 `data/gyeongnam/` 집계로 생성 (`data/region_indicators_sample_dev.csv`는 개발용 더미)
- 경남 22개 지역 수집·집계: `scripts/gyeongnam_regions.py`(지역 마스터) → `scripts/ingest_gyeongnam_*.py` → `data/gyeongnam/`
- 위치 데이터: 창원시는 `data/raw/changwon_bus_stops.csv`(창원시 정류소 원본, 공식 2,926건)·`data/convenience/changwon_convenience_stores.csv`, 그 외 17개 시·군은 `data/gyeongnam/gyeongnam_bus_stops.csv`·`data/gyeongnam/convenience_stores.csv` (의료기관 위치 데이터는 없음)
- 편의점 수집 과정: [`docs/convenience_data.md`](docs/convenience_data.md)

**해석 주의**
- **같은 유형끼리만 비교한다**(창원시 구끼리 / 시끼리 / 군끼리). 22개를 한 표에서 점수로 비교하면 시설 수 그대로는 큰 시가, 인구당은 넓은 군이 늘 상위가 되는 왜곡이 있어서다.
- 점수는 시설 수를 **인구 1만 명당**으로 바꿔 비교 지역 사이 min-max 정규화(0~100) 후 승인된 가중치로 합산한 **시설 수 기반 상대 비교**다. 100점은 "비교 지역 중 그 지표의 인구 1만 명당 값이 가장 높다"는 뜻일 뿐이며, 면적·실제 접근성·통근시간을 보정하지 않았다. 인구당 버스정류장 수는 넓게 흩어진 지역에서 크게 나오므로 교통 편의로 단정하지 않는다.
- 창원시 버스정류장 수는 지역 비교 점수(국토교통부 전국 파일 2025-10-31, 2,918개)와 위치 탐색(창원시 원본 2025-12-31, 2,926개)의 출처가 다르다. 22개 지역을 같은 출처로 비교하기 위해서이며, 화면에 각각의 출처를 표시한다.
- 위치 기반 거리는 **직선거리**(하버사인)다. 도보 거리나 이동시간이 아니다.
- 직장·학교 위치와 주거비 예산은 입력받지만, 대응 데이터가 없어 점수 계산에 쓰지 않는다.

## AI Agent

| 모듈 | 역할 |
|---|---|
| `agent/ollama_agent.py` | 추가질문 생성, 자연어 가중치 해석(로컬 Ollama `qwen3.5:4b`, 적용은 사용자 승인 후) |
| `agent/planner.py` | 비교 범위 분석 도구 계획(Ollama) → Python 검증 후 실행, 가중치는 승인값으로 강제 |
| `agent/location_agent.py` + `agent/location_mcp_server.py` | 위치 주변 시설 Agent. `LLM_BACKEND=claude_cli`면 Claude Code CLI(`claude -p`) + MCP 도구 반복 호출, `ollama`면 계획형 + 결과 검토 루프, 실패 시 기본 조회로 폴백. 좌표·반경은 Python이 고정하고, 두 경로 모두 답변 숫자를 같은 기준(`agent_loop.verify_answer`)으로 검사해 통과 못 하면 Python 요약으로 대체 |
| `agent/llm_json.py` | LLM 응답 JSON 추출·정리 공용 헬퍼 |
| `agent/llm.py`, `agent/claude_cli.py` | AI 호출 공통 창구. `LLM_BACKEND`로 로컬 Ollama(기본) / Claude Code CLI 선택, 세션·일일 호출 수와 입력 길이 제한 |
| `agent/planner_loop.py` | 비교 지역 점수 계산 뒤 AI가 결과 설명·참고 지표 조회·가중치 가정 계산 판단(실제 추천 불변) |
| `agent/agent_loop.py`, `agent/agent_state.py` | 위치 Agent 계획형 경로의 결과 검토 루프(관찰 → 판단 → 행동)와 같은 위치 대화 기억 |

AI 백엔드는 `.env`의 `LLM_BACKEND`(`ollama` 기본 / `claude_cli`) 하나로 정한다. 위치 Agent도 이를 따라 `claude_cli`면 MCP 반복형, `ollama`면 계획형으로 동작하며, 다르게 쓰고 싶을 때만 `LOCATION_AGENT_BACKEND`(`claude_agent`/`claude_cli`/`ollama`)로 덮어쓴다([`.env.example`](.env.example), [`docs/claude_cli_agent.md`](docs/claude_cli_agent.md), [`docs/agent_loop.md`](docs/agent_loop.md)).

## 실행

```bash
pip install -r requirements.txt
ollama pull qwen3.5:4b           # Ollama 서버 실행 필요
streamlit run app.py             # 사이드바에서 user / government 화면으로 이동
```

- 위치 Agent 기본 모드는 실행 PC에 Claude Code CLI(`claude`) 설치·로그인이 필요하다. 없으면 자동으로 Ollama로 폴백한다.
- 공공데이터 키(`.env`, `.env.example` 참고)는 `scripts/`의 수집 스크립트에만 필요하다. 앱 실행에는 필요 없다.

## 테스트

```bash
python -m unittest discover -s tests
```

- 실제 AI 호출 없이 동작한다(`ollama.chat`, claude CLI 실행은 모킹).
- `tests/test_pages_smoke.py`가 3개 화면 첫 렌더링을 Streamlit AppTest로 확인한다. 버튼 클릭 흐름은 브라우저 확인이 필요하다.
- `tests/test_bus_stops.py`는 원본 CSV로 버스정류장 구별 공식 집계(831/452/760/365/518)를 매번 다시 검증한다.

## Claude Code로 개발하기

이 저장소는 Claude Code 개발 Agent 설정을 함께 커밋한다.

| 파일 | 역할 |
|---|---|
| `CLAUDE.md` | 매 세션 자동으로 읽히는 프로젝트 원칙·구조·작업 절차 |
| `.claude/settings.json` | 팀 공용 권한(테스트·조회 자동 허용, force push·`.env`·원본 데이터 직접 수정 차단) |
| `.claude/hooks/run_tests_on_stop.py` | 작업 종료 시 바뀐 `.py`가 있으면 전체 테스트 자동 실행, 실패하면 종료를 막음 |
| `.claude/skills/dev/SKILL.md` | `/dev <요청>` — 확인 → 구현 → 테스트 → diff 검토 → 보고 |
| `.claude/skills/steward/SKILL.md` | `/steward` — 대회 자료·상태·코드를 읽고 가장 중요한 작업 하나를 찾아 진행, 상태 문서 자동 갱신 (`/loop /steward`로 반복) |
| `.claude/hooks/session_status.py` | 세션 시작 시 브랜치·새 참고자료·상태 문서 이후 커밋·제출 마감 D-day 안내 |
| `.claude/scripts/ref_scan.py` | `references/` 자료 변화 감지·텍스트 추출(PDF·HWP·DOCX 등)·색인 (`requirements-dev.txt`) |
| `docs/agent/` | Agent 기억 저장소 — 프로젝트 헌법, 대회 요구사항(출처·쪽), 평가 증거 매트릭스, 현재 상태, 결정 기록, 자료 색인 |
| `references/` | 공식 대회자료·팀 자료 원본(읽기 전용) |
| `docs/backlog.md` | Agent가 관리하는 작업 큐·처리 이력 |

개인 설정은 `.claude/settings.local.json`, `CLAUDE.local.md`에 두고 커밋하지 않는다.
