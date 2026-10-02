# Claude Code CLI로 Agent 호출하기

담당: 황승구 · 브랜치: `hwang2`

API 키 없이, PC에 설치·로그인된 **Claude Code CLI(`claude -p`)**를 LLM으로 쓰는 모듈입니다.
기존 Ollama 호출(`ollama.chat`)과 같은 모양으로 쓸 수 있어서 `.env` 설정 하나로 바꿔 끼울 수 있어요.

| 파일 | 역할 |
|---|---|
| `agent/claude_cli.py` | `claude -p` 실행, 결과 해석, 오류 처리. `ollama.chat`과 같은 모양의 `chat()` 제공 |
| `agent/llm.py` | `LLM_BACKEND` 값으로 Ollama / Claude CLI 중 선택 |
| `tests/test_claude_cli.py` | 오프라인 단위 테스트 20개 (CLI를 실제로 부르지 않음) |

`agent/ollama_agent.py`, `agent/planner.py`, `agent/location_agent.py`는 Ollama 호출 4곳만 `llm.chat()`으로 바꿨고(2026-10-02), 나머지 로직과 `app.py`는 수정하지 않았습니다.

## 1. 준비 (PC마다 한 번)

```powershell
# 1) Claude Code 설치 (공식 설치 안내: https://code.claude.com/docs/en/setup)
# 2) 터미널에서 한 번 실행해 Claude 계정으로 로그인
claude
# 3) 설치·로그인 확인
claude --version
claude auth status        # "loggedIn": true 이면 준비 완료
```

`.env`에 추가:

```
LLM_BACKEND=claude_cli     # ollama 로 두면 기존처럼 Ollama 사용
CLAUDE_CLI_MODEL=sonnet    # 선택: sonnet / haiku / opus (비우면 계정 기본 모델)
CLAUDE_CLI_TIMEOUT=200     # 선택: 호출 제한 시간(초)
# CLAUDE_CLI_PATH=         # 선택: claude 를 PATH에서 못 찾을 때 실행 파일 경로
```

동작 확인:

```powershell
python -m agent.claude_cli                  # 설치·로그인 상태 + 짧은 테스트 호출
python -m unittest tests.test_claude_cli -v # 단위 테스트(실제 호출 없음)
```

## 2. 기존 Agent 코드에 연결하는 방법

아래 4곳은 `hwang2` 브랜치에서 이미 `ollama.chat(` → `llm.chat(`으로 바꿨습니다. 이제 `.env`의 `LLM_BACKEND`만 바꾸면 화면 전체가 Ollama/Claude CLI 중 하나로 동작합니다.

| 파일 | 위치 | 쓰이는 화면 |
|---|---|---|
| `agent/ollama_agent.py` | `generate_followup_questions()` | 메인 화면 "📝 AI의 추가 질문" |
| `agent/ollama_agent.py` | `interpret_weight_feedback()` | 가중치 확인, "🔄 조건 조정 후 다시 비교하기"의 자연어 입력 |
| `agent/planner.py` | `call_planner()` | 최초 추천 시 AI 분석 계획 |
| `agent/location_agent.py` | `call_location_planner()` | `pages/user.py` "5. 🤖 AI에게 주변 생활시설 분석 요청하기" |

바뀐 모양:

```python
import ollama                 # ← 지우지 말고 그대로 둔다 (기존 테스트가 이 이름으로 모킹함)
from agent import llm         # ← 추가

response = llm.chat(model=OLLAMA_MODEL, messages=[...], options={"temperature": 0.0})  # ← ollama.chat 에서 변경
```

`import ollama`를 남겨두는 이유: 기존 테스트(`tests/test_ollama_agent.py` 등)는 `mock.patch("agent.ollama_agent.ollama.chat")`으로 Ollama를 가짜로 바꿉니다.
`llm.chat()`은 Ollama 백엔드일 때 같은 `ollama.chat`을 부르므로 이 모킹이 그대로 통합니다.
단, `.env`에 `LLM_BACKEND=claude_cli`가 있으면 테스트도 실제 CLI를 부르게 되니, **기존 테스트는 Ollama 백엔드로 실행**하세요.

```powershell
$env:LLM_BACKEND="ollama"; python -m unittest discover tests -v; Remove-Item Env:LLM_BACKEND
```

- 반환값 `response["message"]["content"]`는 그대로 쓰면 됩니다.
- 실패하면 예외(`ClaudeCLIError`, `RuntimeError`의 하위 클래스)를 던집니다. 기존 `except Exception` 처리와 그대로 맞습니다.
- `model="qwen3.5:4b"` 같은 Ollama 모델명은 무시하고 `CLAUDE_CLI_MODEL`을 씁니다. `options`(temperature), `think` 같은 Ollama 전용 인자도 무시합니다.
- `format`에 JSON Schema(dict)를 넘기면 CLI의 `--json-schema`로 검증된 JSON을 받습니다.

직접 호출할 때는 예외 없이 결과 dict를 주는 `run()`이 편합니다.

```python
from agent import claude_cli
r = claude_cli.run("창원시 성산구를 한 문장으로 소개해줘.", system="너는 경남 이주 상담사다.")
if r["ok"]:
    print(r["text"])
else:
    print(r["error"])        # 미설치, 미로그인, 시간초과 등 안내 문구
```

Streamlit 화면에 연결 상태를 보여주려면 `claude_cli.check_status()`를 쓰면 됩니다(모델 호출 없음).

## 3. 안전장치 — CLI를 "글만 쓰는 모델"로 제한

Claude Code는 원래 파일 수정·명령 실행 도구가 있는 코딩 에이전트입니다. 웹 화면의 사용자 입력이 그대로 들어가므로 호출할 때마다 아래 옵션을 붙입니다(공식 CLI 레퍼런스 기준).

| 옵션 | 효과 |
|---|---|
| `--tools ""` | 내장 도구(Bash, Read, Edit 등) 전부 비활성화 |
| `--disallowedTools "mcp__*"`, `--strict-mcp-config` | MCP 도구·서버 제외 |
| `--permission-mode dontAsk` | 남은 도구 호출이 있어도 묻지 않고 거부 |
| `--system-prompt-file` | 기본(코딩용) 시스템 프롬프트를 우리 지시로 교체 |
| `--safe-mode` | CLAUDE.md, 스킬, 플러그인, 훅 등 PC 개인 설정을 불러오지 않음 (로그인은 유지) |
| `--no-session-persistence`, `--no-chrome` | 대화 기록 저장 안 함, 브라우저 연동 끔 |
| 빈 임시 폴더에서 실행 | 프로젝트 파일에 접근할 여지 없음 |

- 질문 본문은 명령줄이 아니라 표준입력(UTF-8)으로 넘깁니다. Windows에서 한글·따옴표가 깨지지 않게 하기 위해서입니다.
- 구버전 CLI에 `--safe-mode` 등이 없으면 해당 옵션만 빼고 자동으로 한 번 더 시도합니다.
- 도구를 꺼도 모델이 "명령을 실행한 척" 결과를 지어낸 경우가 실험에서 확인됐습니다. 그래서 모든 시스템 프롬프트 끝에 "도구 없음, 결과를 지어내지 말 것" 안내를 자동으로 붙입니다.

`--bare`는 쓰지 않습니다. `--bare`는 로그인 정보를 읽지 않고 `ANTHROPIC_API_KEY`만 쓰기 때문입니다.

## 4. 실제 CLI로 확인한 결과 (2026-10-02, Claude Code 2.1.287, haiku)

| 확인 항목 | 결과 |
|---|---|
| 설치·로그인 상태 확인 | 정상 |
| 짧은 질문 1회 | 정상 응답, 약 3.8초 |
| JSON Schema 응답 (`format=`) | 스키마에 맞는 JSON 반환, 약 4.3초 |
| 도구 차단 | "ls 실행, 파일 읽기" 요청에 도구 사용 0회. 안내 문구 추가 후에는 "도구를 쓸 수 없다"고 정직하게 답함 |
| `location_agent.call_location_planner` (Ollama 대신 CLI로 바꿔 끼움) | 팀원 코드의 파서가 그대로 계획 JSON을 읽음: 편의점·버스정류장 조회 도구 2개 선택 |
| `ollama_agent.generate_followup_questions` | `{"questions": []}` 반환. 질문 0개도 허용하는 프롬프트 규칙에 맞는 정상 응답 |

### 화면 기능 4가지를 Claude CLI 모드로 실행한 결과 (haiku)

| 기능 | 결과 |
|---|---|
| `plan_followup_questions` (추가 질문) | 정상. AI 질문은 0개로 판단, 가중치 질문은 기존 규칙대로 생성 |
| `interpret_weight_feedback("편의점 70, 교통 30")` | `set_weights` → 편의점 70 / 버스정류장 30, 약 4초 |
| `run_agent_plan` (최초 추천 분석 계획) | `mode=ai_planned`, 교통·생활편의 지표 조회 도구 실행, 약 9초 |
| `run_location_agent` ("500m 안 가장 가까운 편의점") | `mode=ai_planned`, 편의점 조회 도구 1개 실행(최대 1개), 약 7.5초 |

기존 테스트 246개(`python -m unittest discover tests`, Ollama 모킹)는 변경 후에도 모두 통과했습니다.

## 5. 주의사항

- **속도:** 호출마다 CLI 프로세스를 새로 띄워서 1회에 보통 2~9초가 걸립니다. 서버 상황에 따라 같은 요청이 20~90초까지 걸린 적도 있어서, 기본 제한 시간은 200초(`CLAUDE_CLI_TIMEOUT`)로 두었습니다. 한 화면에서 여러 번 부르면 체감이 큽니다. `CLAUDE_CLI_MODEL=haiku`가 가장 빠릅니다.
- **사용량:** 사용량은 CLI에 로그인한 계정의 구독 한도에서 차감됩니다. 별도 결제는 없지만 무제한은 아닙니다.
- **이용 정책:** Anthropic 정책상 구독 로그인(Free/Pro/Max)은 본인의 일반적인 사용을 위한 것입니다. 다른 사람이 쓰는 서비스를 만들어 **다른 사용자의 요청을 내 구독 계정으로 처리하는 것은 허용되지 않고**, 그런 경우 API 키를 써야 합니다. 팀원 각자가 자기 PC에서 자기 계정으로 개발·테스트하는 용도로 쓰고, 외부에 배포하는 단계에서는 인증 방식을 다시 정해야 합니다.
  - 근거: [Claude Code Legal and compliance — Authentication and credential use](https://code.claude.com/docs/en/legal-and-compliance)

## 참고 문서

- [Run Claude Code programmatically (`claude -p`)](https://code.claude.com/docs/en/headless)
- [CLI reference](https://code.claude.com/docs/en/cli-reference)
