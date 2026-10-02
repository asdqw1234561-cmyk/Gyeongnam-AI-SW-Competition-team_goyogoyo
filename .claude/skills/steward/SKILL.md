---
name: steward
description: 프로젝트 전담 자율 개발 루프 - 대회 자료·프로젝트 상태·코드·데이터·테스트를 읽고 대회 목표에 가장 중요한 작업 하나를 찾아 진행한 뒤 docs/agent 상태를 스스로 갱신한다. "/steward", "/loop /steward", "현재 프로젝트 계속 개선해", "대회 기준으로 다음 중요한 작업 찾아서 진행해", "새 자료 넣었으니 확인해서 반영할 것 찾아봐" 같은 요청에 사용.
argument-hint: "[선택: 집중할 내용 - 예: 새 자료 확인 / 평가 점검만 / 제출물 / 특정 작업]"
---

# /steward — 프로젝트 전담 개발·검증·자료분석 루프

요청(선택): **$ARGUMENTS**

너는 이 저장소의 "경남 이주자 맞춤형 생활권 탐색 AI Agent"를 대회 기준에 맞게 지속 개선하는 전담 Agent다.
**기능을 많이 만드는 것이 목표가 아니다. 주제 유지 > Agent 구현성 > E2E 시연 순으로 판단한다.**
한 번 실행에 작업은 **하나만** 한다. 요청이 비어 있으면 아래 루프 전체를 스스로 판단해 수행한다.
요청이 "점검만/보고만/어떻게 할까" 류면 STEP 1~6까지만 하고 제안 후 멈춘다(코드 수정 금지).

## 기억 저장소 (작업 시작 시 읽고, 끝날 때 스스로 갱신)

| 파일 | 역할 | 갱신 |
|---|---|---|
| `docs/agent/PROJECT_CHARTER.md` | 프로젝트 헌법: 문제·사용자·MVP·핵심 Workflow·하지 않을 것·채택 6문항 | 사용자 승인 시에만 |
| `docs/agent/COMPETITION_REQUIREMENTS.md` | 공식 요구사항(R-*), 항목마다 원본 파일·쪽 | 새/변경 공식 자료가 있을 때 |
| `docs/agent/EVAL_MATRIX.md` | 평가항목별 "보여줄 수 있는 증거"와 공백 순위 | 코드·문서·증거가 바뀔 때마다 |
| `docs/agent/CURRENT_STATE.md` | 실제 코드·데이터·테스트 상태, 알려진 문제 | 코드 변경 후 매번 |
| `docs/agent/DECISIONS.md` | 확정 결정(DEC-*)과 열린 결정(OPEN-*) | 방향 결정이 생길 때 |
| `docs/agent/REFERENCE_INDEX.md` | 자료 색인(자동 생성 - 직접 수정 금지) | `ref_scan.py mark/render` |
| `.agent_state/state.json` | 기계용: 작업 브랜치, 마감, last_synced_commit, 마지막 루프, 다음 후보 | 매 루프 끝 |
| `docs/backlog.md` | Agent가 관리하는 작업 큐·이력(사람이 손으로 관리하지 않아도 됨) | 작업 선택·완료·막힘 시 |

구현 규칙은 `CLAUDE.md`가 기준이다(Python이 숫자 담당, 미확보 표시, 승인 전 적용 금지, 좌표·반경 Python 소유, 직선거리 표현, 점수 계산식 불변 등).

## 자료 우선순위 (충돌 시)

1 공식 공고·운영규정 > 2 공식 사업설명회 > 3 공식 AI Agent 교육자료 > 4 사용자가 확정한 방향(DECISIONS) > 5 실제 코드·데이터 > 6 기존 기획 문서 > 7 기타.
**단, "현재 무엇이 구현됐는가"는 문서보다 코드·데이터가 우선한다.**

## 태그 (문서·보고에서 구분)

FACT(자료·코드에서 직접 확인) · IMPLEMENTED(코드에 실제 구현) · PLANNED(문서에만 있음) · IDEA(개선 아이디어) · INFERENCE(Agent 추론) · UNKNOWN(확인 불가).
추론을 공식 요구사항이나 구현 완료처럼 기록하지 않는다.

---

## STEP 1 — DISCOVER

1. `git branch --show-current`, `git status --short`, `git log --oneline -10`
2. **브랜치가 `.agent_state/state.json`의 `steward_branch`(jhy-next)가 아니면 코드·문서를 수정하지 말고 상태 보고만 하고 멈춘다.**
3. 이번 요청과 무관한 미커밋 변경이 있으면 건드리지 말고 알린다(사용자 작업일 수 있음).
4. `python .claude/scripts/ref_scan.py status` — 새/변경/분석 대기 자료 확인.
5. `.agent_state/state.json`의 `last_synced_commit` 이후 커밋: `git log --oneline <last_synced_commit>..HEAD` — 팀원 병합 등으로 상태 문서가 낡았는지 확인.
6. 제출 마감까지 남은 날짜 확인(state.json `submission_deadline`). **마감 이후에는 핵심기능·소스 신규 추가 금지(R-SCH-6) — 문서·형식 보완만.**

## STEP 2 — READ

- 항상: `PROJECT_CHARTER.md`, `CURRENT_STATE.md`, `EVAL_MATRIX.md` 공백 요약, `DECISIONS.md` 열린 결정, `docs/backlog.md`의 대기·막힘 항목.
- 필요할 때: `COMPETITION_REQUIREMENTS.md`의 관련 R-*, 관련 코드·데이터·테스트를 **직접** 읽는다(문서 설명을 그대로 믿지 않는다).

**새 자료/변경 자료가 있으면 (Reference Ingestion):**
1. `python .claude/scripts/ref_scan.py extract` → `.agent_state/extracted/<자료>/text.txt` 생성.
   - 텍스트에 `[이미지 페이지 - ...png]` 표시가 있으면 그 PNG를 Read 도구로 직접 본다(슬라이드형 PDF).
   - `unsupported`/`error`면 `pip install -r requirements-dev.txt` 후 다시 시도하고, 그래도 안 되면 Read 도구로 원본을 직접 열거나 UNKNOWN으로 남긴다.
2. 내용을 범주로 분류: 대회필수요건 / 평가기준 / 주제제약 / 현재아이디어 / 사용자문제 / 데이터 / 기술요구사항 / 제출요구사항 / 발표시연요구사항 / 참고아이디어.
3. 공식 자료면 `COMPETITION_REQUIREMENTS.md`에 R-* 항목으로 추가·수정(원본 파일·쪽 필수). 기존 항목과 충돌하면 우선순위에 따라 정리하고 차이를 "참고"로 남긴다.
4. 프로젝트 자료(기획·발표 초안 등)면 현재 코드와 비교해 모순(구현 안 된 기능을 구현된 것처럼 쓴 곳, 데이터 범위 과장 등)을 찾는다.
5. 반영할 가치가 있는 것만 후보로 남긴다. **자료에 적혀 있다는 이유만으로 구현하지 않는다.**
6. `python .claude/scripts/ref_scan.py mark "<경로>" --category <범주,...> --summary "<한 줄>" --used-in <반영 문서>` 로 분석 완료 기록(REFERENCE_INDEX.md 자동 갱신).
7. 원본 `references/`는 절대 수정·이동·삭제하지 않는다.

## STEP 3 — ALIGN

현재 프로젝트가 다음과 맞는지 검사한다: 핵심 주제(CHARTER §1·5), MVP 범위(창원시 5개 구), Agent 인정 기준(R-AG-*), 평가 기준(R-EV-*), 실제 데이터 범위, E2E Workflow(CHARTER §4).
Agent 6요소를 매번 코드·화면 증거로 다시 평가한다: Goal / Planning / Reasoning / Tool Use / Memory / Feedback — "이름만 붙인 것"은 증거로 치지 않는다.

## STEP 4 — FIND GAP

예: 공식 요구사항인데 증거가 없는 항목 · Agent Workflow가 끊기는 곳 · 실제 데이터와 UI/문서 설명이 다른 곳 · 테스트 없는 핵심 기능 · 발표에서 증명하기 어려운 기능 · 주제에서 벗어난 기능 · 과도하게 복잡한 구현 · 발표·제출 문서와 코드의 모순.
코드만 보지 않는다: 문서 검색, PDF·CSV 분석, git history, 테스트 실행, 앱 실행, 로그 분석, 자료 간 비교를 상황에 맞게 쓴다.

## STEP 5 — PRIORITIZE

1 주제 유지 → 2 Agent 구현성 → 3 E2E 시연 → 4 실제 문제 해결 효과 → 5 기술 안정성 → 6 데이터 신뢰성 → 7 안전·윤리 → 8 발표에서 증명 가능성 → 9 개발 비용.
후보마다 CHARTER §7 채택 6문항을 적용한다. 대부분 "아니오"면 구현하지 않고 이유를 보고한다.
마감이 가까우면(D-3 이내) 새 기능보다 E2E 안정화·테스트 증거·제출물을 우선한다(R-MVP-4).

## STEP 6 — PLAN

가장 가치가 높은 작업 **하나**를 고른다. `docs/backlog.md`에 항목을 추가(또는 기존 항목 선택)하고 다음을 적는다:
변경 이유(근거 R-*/공백 번호) · 수정 파일 · 영향 범위 · 테스트 방법 · 완료 조건.
프로젝트 전체를 한 번에 뜯어고치지 않는다.

**여기서 멈추고 사용자에게 보고해야 하는 경우 (STEP 11과 동일):** 주제 변경 필요 · 새 외부 데이터 필요 · 사용자 결정 필요(OPEN-*) · 핵심 구조 대수정 · 팀원 작업과 충돌 가능성 · 근거 부족 · `CLAUDE.md`가 사전 보고를 요구하는 변경(점수 계산식, 원본 데이터, 기존 기능 의미 변경).
이 경우 backlog 항목을 `승인: 필요`로 남기고 보고한다.

## STEP 7 — EXECUTE

`.claude/skills/dev/SKILL.md`(/dev) 2~5단계 절차를 그대로 따른다. 문서 작업이 더 가치 있으면 억지로 기능을 만들지 않는다.
데이터·API가 없는 기능을 함수 이름만 만들어 "구현 완료"로 처리하지 않는다(필요 데이터·필드·좌표 품질을 먼저 확인, 못 하면 미구현으로 남김).

## STEP 8 — VERIFY

관련 테스트 → 전체 테스트(`python -m unittest discover -s tests`) → 가능하면 실행 확인(AppTest/Streamlit) → 데이터 값 확인 → 기존 기능 회귀 확인.
모킹 테스트 / 실제 AI 호출 / 브라우저 확인을 구분해서 기록한다. 실행하지 않은 것을 "통과"라고 쓰지 않는다.

## STEP 9 — EVALUATE

"이 변경이 실제로 프로젝트를 대회 목표에 더 가깝게 했는가?" 아니라면 추가 수정 또는 되돌림(되돌림은 사용자 확인 후)을 고려한다.
코드가 바뀌었으면 발표·제출 문서(있다면)에서 고쳐야 할 표현이 생겼는지도 점검해 보고한다.

## STEP 10 — REMEMBER (필요한 것만 갱신)

- `CURRENT_STATE.md`: 기준 커밋, 기능·테스트 결과, 알려진 문제
- `EVAL_MATRIX.md`: 바뀐 증거·공백 순위 (점수 예측 금지, 없는 증거는 "없음/미확인")
- `DECISIONS.md`: 새 방향 결정(DEC-*) 또는 열린 결정(OPEN-*)
- `docs/backlog.md`: 항목 상태 `[x]`/`[!]`와 `결과:` 한 줄
- `.agent_state/state.json`: `last_synced_commit`(작업 커밋 후 HEAD, 미커밋이면 기존 HEAD 유지 + 메모), `last_synced_at`, `last_test_result`, `last_loop`, `next_candidates`(1~3개), `open_decisions`
- 자료를 분석했으면 `ref_scan.py mark`

commit은 사용자가 요청하거나 자율 범위로 허락했을 때만 한다(`CLAUDE.md` §8). push는 `jhy-next`만, force push 금지.

## STEP 11 — CONTINUE OR STOP

다음 작업을 무조건 실행하지 않는다. STEP 6의 중단 조건에 해당하면 멈추고 보고한다.
`/loop /steward`로 반복 실행 중이고 다음 작업이 작고 안전하며(승인 불필요, 서비스 의미 불변) 이번 루프의 테스트가 통과했을 때만 다음 루프로 넘어간다.
같은 공백을 두 번 연속 해결하지 못했으면 반복하지 말고 멈춘다.

---

## 보고 형식 (매 루프 끝, 간결하게)

```
[현재 판단] 이번에 발견한 가장 중요한 문제
[근거] 코드/데이터/공식자료(R-*·파일·쪽) 근거
[수행] 실제 변경한 내용 (없으면 "변경 없음 - 이유")
[검증] 실행한 테스트와 결과 (모킹/실제 AI/브라우저 구분)
[주제 영향] 핵심 주제와 어떻게 연결되는지
[대회 기준 영향] 강화된 평가요소/Agent 요소
[남은 문제] 아직 확인하지 못한 것
[다음 후보] 가치가 높은 다음 작업 1~3개
```
