# 보안·개인정보 점검 (2026-10-04, jhy-next `963ef4e`)

> 범위: 비밀정보·사용자 정보가 노출·저장될 가능성. 새 기능·로그인·암호화는 범위 밖.
> 실제 Secret 값은 읽거나 출력하지 않았다(`.env`는 열지 않음). 존재 여부·경로만 기록한다.
> 결과: **HIGH 0 · MEDIUM 1 · LOW 7** → 코드 변경 없음, 권고만 기록(backlog S1).

## 위험 항목

| ID | 등급 | 위치 | 내용 | 최소 수정안 (미적용) |
|---|---|---|---|---|
| S-M1 | MEDIUM | `scripts/collect_convenience_stores.py` `_call_with_retry` → `fetch_district` → `cmd_collect` | 네트워크 오류 시 `requests` 예외 문자열을 그대로 `reason`에 넣는다. `requests` 예외 메시지에는 **serviceKey가 든 요청 URL이 포함**된다(가짜 키로 `ConnectTimeout` 재현해 확인). 이 `reason`은 콘솔에 출력되고, `--save --allow-partial`이면 git 추적 파일 `data/convenience/changwon_convenience_counts.csv`의 note 열에 저장된다 → 커밋·push 시 키가 GitHub에 올라갈 수 있다. 발생 조건(네트워크 오류 + 두 옵션)이 좁아 MEDIUM. 현재 커밋된 CSV에는 `serviceKey`·`http` 문자열 0건 | `collect_rent_transactions.py:120`과 같게 `requests.RequestException`은 `type(exc).__name__`만 남긴다(1~2줄) |
| S-L1 | LOW | `scripts/ingest_hira_hospital_data.py` `_mask` | 원문 키만 치환한다. `requests`가 URL에 넣을 때 `+`·`/`·`=`를 `%2B` 등으로 인코딩하면 예외 메시지 속 키가 가려지지 않는다. 콘솔 출력만(파일 저장 없음) | `_mask`에서 `urllib.parse.quote(secret, safe="")` 형태도 함께 치환, 또는 예외 유형만 출력 |
| S-L2 | LOW | `.gitignore` | `.env`·`.streamlit/secrets.toml`·`database/app.db`는 제외됨. 그러나 **`CLAUDE.local.md`**(CLAUDE.md §8에서 커밋 금지로 정한 파일), `.env.*`(예: `.env.local`), `credentials*.json`·`*.pem`은 제외되지 않는다. `git add -A`로 실수 커밋 가능 | `.gitignore`에 `CLAUDE.local.md`, `.env.*`, `!.env.example`, `*.pem`, `credentials*.json` 추가 |
| S-L3 | LOW | Streamlit 설정 없음(`.streamlit/config.toml` 부재) | 처리되지 않은 예외가 나면 Streamlit 기본값으로 traceback(파일 경로·코드 줄)이 화면에 보인다. 환경변수 값은 포함되지 않음 | 배포 시 `.streamlit/config.toml`에 `[client] showErrorDetails = "none"` |
| S-L4 | LOW | `agent/claude_cli.py:258,268` → `app.py:314`, `pages/user.py:417` | Claude CLI가 실패하면 stderr 최대 500자가 오류 문구로 화면 경고에 표시된다(로컬 경로·CLI 내부 메시지 가능, API 키는 CLI 로그인 방식이라 해당 없음) | 화면에는 "AI 호출 실패 - 기본 절차로 진행"만, 상세는 실행 과정 접기 영역 또는 콘솔로 |
| S-L5 | LOW | `agent/llm.py:37` `load_dotenv()` + `agent/claude_cli.py`·`agent/location_agent.py` 하위 프로세스 | 앱이 `.env` 전체를 `os.environ`에 올리고 `claude` 하위 프로세스(및 MCP 서버)가 이를 상속한다. 수집용 키(`*_SERVICE_KEY`)는 앱에 필요 없지만 하위 프로세스 환경에 존재. 모델은 `--tools ""`(위치 Agent는 지정 MCP 도구 3개만)라 환경변수를 읽을 수 없음. `.env`에 `ANTHROPIC_API_KEY`가 있으면 CLI가 로그인 대신 API 과금으로 동작할 수 있음 | 하위 프로세스에 `env={k: v for k, v in os.environ.items() if not k.endswith("_SERVICE_KEY")}` 전달 |
| S-L6 | LOW | `LLM_BACKEND=claude_cli`일 때 | 사용자 입력(희망지역·직장/학교 구·주거비 구간·자가용·추가 요청사항 자유문장), 위치 질문 자연어, 같은 위치 최근 5개 대화가 외부(Anthropic) 모델로 전송된다. **좌표는 프롬프트에 넣지 않는다**(MCP 서버 환경변수로만 전달, `agent/location_agent.py` `_build_user_prompt`, `agent/agent_state.py` `history_to_prompt` 확인). Ollama 백엔드는 로컬. 입력 길이는 `LLM_MAX_INPUT_CHARS`로 제한 | 화면에 "자유 입력은 AI 분석에 전달되니 개인정보(이름·연락처·상세주소)를 적지 마세요" 한 줄 |
| S-L7 | LOW | `app.py` `st.session_state.feedback_history` | 피드백마다 항목이 추가되고 상한이 없다(세션 메모리 안에서만, 파일 저장 없음). `ConversationMemory`는 최근 5턴·질문/답변 길이 제한 | 필요 시 최근 N개만 유지 |

## 문제 없음으로 확인한 항목 (FACT)

| 점검 항목 | 결과 |
|---|---|
| 1. `.env`·API 키 Git 추적 | `.env`는 `.gitignore` 대상, `git ls-files`에 env·credential류는 `.env.example`뿐. `.env.example`은 빈 값과 자리표시자(`sk-ant-...`)만 |
| 2. Git history 비밀키 | 전체 78개 커밋에서 `.env` 커밋 0회. 추가된 줄의 `*KEY/TOKEN/SECRET/PASSWORD=값` 패턴은 `.env.example`의 자리표시자 1건(길이 10, `sk-ant-...`). 64자리 hex·80자 이상 base64 문자열 3건은 모두 `.agent_state/reference_index.json`의 참고자료 SHA-256 해시 |
| 3. URL·serviceKey 출력 | serviceKey는 `scripts/` 수집 스크립트에서만 사용(앱 코드는 사용 안 함). `collect_rent_transactions.py`는 예외 유형만 출력, `ingest_hira_hospital_data.py`는 마스킹(S-L1 한계) — 예외는 S-M1 |
| 4. 화면의 키·환경변수 표시 | `app.py`·`pages/`에서 `os.environ`·키 값을 `st.*`로 출력하는 곳 없음. `claude_cli.check_status()`는 `loggedIn`·`authMethod`만 추출(계정 이메일 미추출)하고 화면에서 쓰지 않음 |
| 5. 사용자 입력 영구 저장 | 사용자 입력·지도 좌표·직장/학교·자연어 질문을 파일·DB·로그에 쓰는 코드 없음. `database/db.py`는 미사용 자리표시자. Claude CLI 시스템 프롬프트·MCP 설정·도구 로그는 `tempfile.TemporaryDirectory` 안에서 생성·자동 삭제. Claude CLI는 `--no-session-persistence`. `CLAUDE_CLI_DEBUG_FILE`은 환경변수를 직접 설정한 경우에만 로그 파일 생성(개발용) |
| 6. feedback_history·ConversationMemory | 둘 다 `st.session_state`(브라우저 세션 메모리)에만 있고 세션이 끝나면 사라짐. 서버 파일·DB 저장 없음(상한은 S-L7) |
| 7. 프롬프트 Secret | 프롬프트에 환경변수·키를 넣는 코드 없음. 좌표는 프롬프트에 없음(S-L6) |
| 8. 오류 화면 노출 | 앱 코드에서 `st.exception`·traceback 출력 없음. AI 오류 문구 노출은 S-L4, Streamlit 기본 traceback은 S-L3 |
| 9. `.gitignore` | 핵심(`.env`, secrets.toml, app.db, `.claude/settings.local.json`)은 제외됨. 보완은 S-L2 |
| 10. 하드코딩 키 | 소스(`data/`·`references/`·`.venv` 제외)에서 키·토큰·비밀번호 리터럴 패턴 0건 |

## 확인하지 못한 것 (UNKNOWN)
- GitHub 저장소 공개 여부(공개면 S-M1·S-L2 영향이 커짐).
- 팀원 브랜치(main/jhy/hwang3)의 현재 파일 — 이번 점검은 `jhy-next`와 `git log --all`로 가져온 이력 범위.
- `.env`의 실제 키 목록(읽기 거부로 열지 않음 - 점검 결과에는 영향 없음).
