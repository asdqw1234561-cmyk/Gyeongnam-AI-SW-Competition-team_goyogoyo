# Gyeongnam-AI-SW-Competition-team_goyogoyo

경남 이주자 맞춤형 생활권 탐색 AI — 현재 **창원시 5개 구**(의창구·성산구·마산합포구·마산회원구·진해구)를 대상으로, 공공데이터로 확보한 시설 수 기준 생활권 비교와 위치 기반 주변 시설 탐색, 행정용 시설 현황 분석을 제공한다.

> 이 서비스는 AI가 지역 정보를 지어내는 추천 서비스가 아니다. 시설 수·점수 계산은 Python이 하고, AI는 요청 해석·분석 계획·도구 선택·설명만 담당한다. 데이터가 없는 항목은 "미확보"로 표시한다. 자세한 원칙은 [`CLAUDE.md`](CLAUDE.md) 참고.

## 화면

| 화면 | 파일 | 내용 |
|---|---|---|
| 이주자용 생활권 비교 | `app.py` | 조건 입력 → AI 추가질문·가중치 확인(사용자 승인) → Agent Planner → 5개 구 상대 비교 점수 → 가중치 피드백 재계산 |
| 관심 위치 주변 탐색 | `pages/user.py` | 지도 클릭·좌표 입력으로 위치 확정 → 300m/500m/1km 주변 버스정류장·편의점, Folium 지도, 위치 AI Agent |
| 정부용 시설 현황 분석 | `pages/government.py` | 구별 시설 수 비교·차이, 가상 시설 증감 시뮬레이션(실제 정책 효과 예측 아님) |

## 데이터

| 지표 | 상태 | 출처 · 기준일 |
|---|---|---|
| 버스정류장 수 (`bus_stop_count`) | 확보 — 5개 구 합계 2,926개 | 창원시 버스정류소 위치정보(data.go.kr 15037805) 2025-12-31 + SGIS 행정경계 2025-06-30 |
| 의료기관 수 (`hospital_count`) | 확보 — 의원·치과·한의원·보건소 등 전 종별 합산 | 건강보험심사평가원 병원정보서비스(data.go.kr 15001698) 2026-10-01 |
| 편의점 수 (`convenience_store_count`) | 확보 — 상가정보 등록 업소 950개 | 소상공인 상가(상권)정보(data.go.kr 15012005) 2026-06 |
| 대중교통 소요시간, 응급실 운영 병원, 대형마트, 월세·전세 | **미확보** | — |

- 운영 지표: `data/region_indicators.csv` (`data/region_indicators_sample_dev.csv`는 개발용 더미)
- 위치 데이터: `data/raw/changwon_bus_stops.csv`, `data/convenience/changwon_convenience_stores.csv` (의료기관 위치 데이터는 없음)
- 편의점 수집 과정: [`docs/convenience_data.md`](docs/convenience_data.md)

**해석 주의**
- 점수는 5개 구 사이 min-max 정규화(0~100) 후 승인된 가중치로 합산한 **시설 수 기반 상대 비교**다. 100점은 "5개 구 중 그 지표가 가장 높다"는 뜻일 뿐이며, 인구·면적·실제 접근성·통근시간을 보정하지 않았다.
- 위치 기반 거리는 **직선거리**(하버사인)다. 도보 거리나 이동시간이 아니다.
- 직장·학교 위치와 주거비 예산은 입력받지만, 대응 데이터가 없어 점수 계산에 쓰지 않는다.

## AI Agent

| 모듈 | 역할 |
|---|---|
| `agent/llm.py`, `agent/claude_cli.py` | 모든 AI 호출의 단일 창구. `LLM_BACKEND`로 로컬 Ollama(`qwen3.5:4b`, 기본) / Claude Code CLI 선택, 사용량 제한 |
| `agent/ollama_agent.py` | 추가질문 생성, 자연어 가중치 해석(적용은 사용자 승인 후) |
| `agent/planner.py` + `agent/planner_loop.py` | 5개 구 분석 도구 계획 → Python 검증 후 실행(가중치는 승인값으로 강제) → AI가 결과 설명·참고 지표 조회·가정 계산 판단(실제 추천 불변) |
| `agent/location_agent.py` + `agent/agent_loop.py` | 위치 주변 시설 Agent. 계획 → Python 검증·실행 → 결과를 본 AI가 추가 조회/답변 판단(관찰 → 판단 → 행동). 좌표·반경은 Python이 고정하고, 답변 숫자를 검사해 통과 못 하면 Python 요약으로 대체 |
| `agent/agent_state.py` | 같은 위치에서의 대화 기억("그럼 버스는?" 같은 후속 질문) |
| `agent/llm_json.py` | LLM 응답 JSON 추출·정리 공용 헬퍼 |

AI 백엔드는 `.env`의 `LLM_BACKEND`(`ollama` 기본 / `claude_cli`)로 바꾼다. 설정·사용량 제한은 [`.env.example`](.env.example), [`docs/claude_cli_agent.md`](docs/claude_cli_agent.md), 반복 루프 설계는 [`docs/agent_loop.md`](docs/agent_loop.md) 참고.

## 실행

```bash
pip install -r requirements.txt
ollama pull qwen3.5:4b           # Ollama 서버 실행 필요
streamlit run app.py             # 사이드바에서 user / government 화면으로 이동
```

- `LLM_BACKEND=claude_cli`를 쓰려면 실행 PC에 Claude Code CLI(`claude`) 설치·로그인이 필요하다.
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
| `docs/backlog.md` | `/loop`가 위에서부터 처리하는 작업 목록 |

개인 설정은 `.claude/settings.local.json`, `CLAUDE.local.md`에 두고 커밋하지 않는다.
