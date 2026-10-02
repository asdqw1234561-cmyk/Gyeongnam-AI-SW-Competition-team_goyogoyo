# Claude Code CLI(claude -p) 호출 모듈
"""
로컬 PC에 설치·로그인된 Claude Code CLI를 `claude -p`(비대화형 모드)로 호출한다.
API 키 없이, CLI에 로그인한 Claude 구독 계정으로 동작한다.

[ollama.chat 과 같은 모양]
    agent/ 의 기존 코드는 `ollama.chat(model=..., messages=[...], options=...)` 를 부르고
    `response["message"]["content"]` 를 읽는다. 이 모듈의 chat() 도 같은 인자를 받고 같은
    모양의 dict를 돌려주므로, 호출부를 거의 고치지 않고 바꿔 끼울 수 있다.
    (백엔드 선택은 agent/llm.py 참고)

        from agent import claude_cli
        response = claude_cli.chat(messages=[
            {"role": "system", "content": "JSON만 출력하라"},
            {"role": "user", "content": "..."},
        ])
        text = response["message"]["content"]

[이 모듈이 CLI를 '글만 쓰는 모델'로 만드는 방법]
    Claude Code는 원래 파일 수정·터미널 명령 도구를 가진 코딩 에이전트다. 웹 화면의 사용자
    입력이 그대로 들어가므로 아래처럼 막는다. (모든 옵션은 공식 CLI 레퍼런스 기준)
      --tools ""                      내장 도구(Bash, Read, Edit ...) 전부 비활성화
      --disallowedTools "mcp__*"      MCP 도구도 제거
      --strict-mcp-config             PC에 설정된 MCP 서버를 불러오지 않음
      --permission-mode dontAsk       혹시 남은 도구 호출이 있어도 묻지 않고 거부
      --system-prompt-file            Claude Code 기본(코딩용) 시스템 프롬프트를 우리 지시로 교체
      --safe-mode                     CLAUDE.md·스킬·플러그인·훅 등 PC 개인 설정을 불러오지 않음
                                      (--bare와 달리 로그인 인증은 그대로 사용)
      --no-session-persistence        호출 기록을 디스크에 남기지 않음
      --no-chrome                     브라우저(Chrome) 연동 끔
      시스템 프롬프트 끝 안내 문구     "도구 없음, 결과를 지어내지 말 것" 자동 추가
      작업 폴더(cwd)                  매 호출마다 빈 임시 폴더에서 실행
    프롬프트 본문(한글)은 명령줄이 아니라 표준입력(UTF-8)으로 넘긴다. Windows에서 claude가
    배치 파일(claude.cmd)로 설치된 경우 명령줄의 한글·특수문자가 깨질 수 있기 때문이다.

[제약]
    - temperature 같은 샘플링 옵션은 CLI에 없어서 무시된다.
    - 호출마다 프로세스를 새로 띄우므로 한 번에 수 초가 걸린다. Ollama보다 느릴 수 있다.
    - 사용량은 CLI에 로그인한 계정의 구독 한도에서 차감된다.

[환경변수 (.env)]
    CLAUDE_CLI_PATH      claude 실행 파일 경로 (기본: PATH에서 자동 탐색)
    CLAUDE_CLI_MODEL     모델 별칭: sonnet / haiku / opus (기본: 계정 기본 모델)
    CLAUDE_CLI_TIMEOUT   호출 제한 시간(초, 기본 200)
    CLAUDE_CLI_DEBUG_FILE  (진단용) 지정하면 CLI 디버그 로그를 이 파일에 남긴다
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

DEFAULT_TIMEOUT_S = 200
DEFAULT_MAX_TURNS = 2  # 도구를 모두 껐으므로 한 번의 답변이면 충분. 여유분 1

# 버전에 따라 없을 수 있는 옵션. 'unknown option' 오류가 나면 빼고 한 번 더 시도한다.
_OPTIONAL_FLAGS = ("--safe-mode", "--no-session-persistence", "--no-chrome")

_DEFAULT_SYSTEM = (
    "You are a helpful assistant for a Korean public-service web app. "
    "Answer only from the instructions and data in the user message. "
    "Reply in Korean unless told otherwise."
)

# 어떤 시스템 프롬프트를 쓰든 끝에 항상 붙인다. 도구를 꺼도 모델이 명령 실행 결과나
# 파일 내용을 지어내는 경우가 실측으로 확인되어 명시적으로 막는다.
_GUARD = (
    "\n\n[실행 환경 안내] 이 응답에서는 어떤 도구도 사용할 수 없다. 명령 실행, 파일·웹 조회 "
    "결과를 지어내지 말고, 사용자 메시지에 주어진 정보만으로 답하라. 필요한 정보가 없으면 "
    "없다고 말하라."
)


class ClaudeCLIError(RuntimeError):
    """CLI 미설치·미로그인·시간초과·응답 오류 등. ollama.chat 이 던지는 예외와 같은 용도."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def find_cli() -> Optional[str]:
    """claude 실행 파일 경로. 없으면 None."""
    configured = os.environ.get("CLAUDE_CLI_PATH")
    if configured:
        return configured if os.path.exists(configured) else shutil.which(configured)
    return shutil.which("claude")


def _split_messages(messages: list[dict]) -> tuple[str, str]:
    """ollama 형식 messages -> (system 텍스트, 표준입력으로 보낼 본문)."""
    system_parts, convo = [], []
    for msg in messages or []:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        if role == "system":
            system_parts.append(content)
        else:
            convo.append((role, content))

    if len(convo) == 1 and convo[0][0] == "user":
        body = convo[0][1]
    else:  # 여러 턴이면 대화 기록 형태로 펼친다
        label = {"user": "사용자", "assistant": "어시스턴트"}
        lines = ["아래는 지금까지의 대화입니다. 마지막 사용자 메시지에 답하세요.", ""]
        for role, content in convo:
            lines.append(f"[{label.get(role, role)}]")
            lines.append(content)
            lines.append("")
        body = "\n".join(lines).rstrip()
    return "\n\n".join(p for p in system_parts if p.strip()), body


def _build_command(exe: str, system_file: str, model: Optional[str],
                   json_schema: Optional[dict], max_turns: int,
                   skip_flags: tuple[str, ...] = ()) -> list[str]:
    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--system-prompt-file", system_file,
        "--tools", "",
        "--disallowedTools", "mcp__*",
        "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--max-turns", str(max_turns),
    ]
    for flag in _OPTIONAL_FLAGS:
        if flag not in skip_flags:
            cmd.append(flag)
    if model:
        cmd += ["--model", model]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema, ensure_ascii=True)]
    debug_file = os.environ.get("CLAUDE_CLI_DEBUG_FILE")
    if debug_file:  # 문제 진단용: CLI 내부 동작 로그를 파일로 남긴다
        cmd += ["--debug-file", debug_file]
    return cmd


def _kill_tree(proc: subprocess.Popen) -> None:
    """시간 초과 시 claude 와 그 하위 프로세스(node 등)를 모두 종료한다.
    Windows 에서 claude.cmd 로 실행되면 proc.kill() 은 cmd.exe 만 끝내고 node 가 남아
    출력 파이프를 붙잡는 바람에 제한 시간이 지나도 한참 더 기다리게 된다."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)  # start_new_session 으로 만든 그룹 전체
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_process(cmd: list[str], prompt: str, cwd: str, timeout: int):
    """(CompletedProcess | None, 시간초과 여부, 시간초과 시 받은 stderr 일부)"""
    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
        text=True, encoding="utf-8", errors="replace", **popen_kwargs,
    )
    try:
        out, err = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
        return None, True, (err or out or "").strip()[-400:]
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err), False, ""


def _unknown_flag(stderr: str) -> Optional[str]:
    text = (stderr or "").lower()
    if "unknown option" not in text and "unknown argument" not in text:
        return None
    for flag in _OPTIONAL_FLAGS:
        if flag in text:
            return flag
    return None


def run(prompt: str, system: Optional[str] = None, *, model: Optional[str] = None,
        json_schema: Optional[dict] = None, timeout: Optional[int] = None,
        max_turns: int = DEFAULT_MAX_TURNS) -> dict:
    """
    claude -p 를 한 번 실행한다. 예외를 던지지 않고 결과 dict를 돌려준다.

    반환: {"ok": bool, "text": str|None, "structured": dict|None, "error": str|None,
           "session_id": str|None, "cost_usd": float|None, "duration_ms": int|None}
    json_schema를 주면 응답이 그 스키마에 맞는 JSON으로 검증되어 "structured"에 담긴다.
    """
    result = {"ok": False, "text": None, "structured": None, "error": None,
              "session_id": None, "cost_usd": None, "duration_ms": None}

    exe = find_cli()
    if not exe:
        result["error"] = ("Claude Code CLI(claude)를 찾을 수 없습니다. 설치 후 터미널에서 "
                           "`claude` 를 한 번 실행해 로그인하세요.")
        return result

    model = model or os.environ.get("CLAUDE_CLI_MODEL") or None
    timeout = timeout or _env_int("CLAUDE_CLI_TIMEOUT", DEFAULT_TIMEOUT_S)

    with tempfile.TemporaryDirectory(prefix="claude_cli_") as workdir:
        system_file = os.path.join(workdir, "system_prompt.txt")
        with open(system_file, "w", encoding="utf-8") as f:
            f.write((system or _DEFAULT_SYSTEM) + _GUARD)

        skip: tuple[str, ...] = ()
        started = time.monotonic()
        for _ in range(len(_OPTIONAL_FLAGS) + 1):
            cmd = _build_command(exe, system_file, model, json_schema, max_turns, skip)
            try:
                proc, timed_out, partial = _run_process(cmd, prompt, workdir, timeout)
            except OSError as exc:
                result["error"] = f"Claude CLI 실행 실패: {exc}"
                return result
            if timed_out:
                result["error"] = f"Claude CLI 응답이 {timeout}초 안에 오지 않았습니다."
                if partial:
                    result["error"] += f" (CLI 출력: {partial})"
                return result

            bad_flag = _unknown_flag(proc.stderr)
            if proc.returncode != 0 and bad_flag and bad_flag not in skip:
                skip += (bad_flag,)  # 구버전 CLI: 해당 옵션 없이 재시도
                continue
            break

    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    stdout = (proc.stdout or "").strip()
    try:
        data = json.loads(stdout.splitlines()[-1] if stdout else "")
    except (json.JSONDecodeError, IndexError):
        detail = (proc.stderr or stdout or "").strip()[:500]
        result["error"] = f"Claude CLI 출력을 해석하지 못했습니다(exit {proc.returncode}): {detail}"
        return result

    result["session_id"] = data.get("session_id")
    result["cost_usd"] = data.get("total_cost_usd")
    result["text"] = data.get("result")
    result["structured"] = data.get("structured_output")

    if proc.returncode != 0 or data.get("is_error") or data.get("subtype") not in (None, "success"):
        reason = data.get("result") or data.get("subtype") or proc.stderr.strip()[:300]
        result["error"] = f"Claude CLI 오류: {reason}"
        return result
    if json_schema is not None and result["structured"] is None:
        result["error"] = "Claude CLI가 스키마에 맞는 JSON(structured_output)을 돌려주지 않았습니다."
        return result

    result["ok"] = True
    return result


def chat(model: Optional[str] = None, messages: Optional[list[dict]] = None,
         options: Optional[dict] = None, format=None, **_ignored) -> dict:
    """
    ollama.chat() 과 같은 호출 모양. 실패하면 ClaudeCLIError를 던진다
    (기존 코드가 `except Exception` 으로 Ollama 오류를 처리하는 방식과 같게).

    - model: Ollama 모델명(qwen 등)이 넘어오면 무시하고 CLAUDE_CLI_MODEL/계정 기본값을 쓴다.
      "sonnet", "haiku", "opus" 또는 "claude-..."로 시작하는 이름만 CLI에 전달한다.
    - options(temperature 등), think 같은 Ollama 전용 인자는 무시한다.
    - format: dict(JSON Schema)를 주면 --json-schema 로 검증된 JSON 문자열을 content에 담는다.

    반환: {"message": {"role": "assistant", "content": str},
           "session_id", "cost_usd", "duration_ms", "backend": "claude_cli"}
    """
    cli_model = None
    if model and (model in ("sonnet", "haiku", "opus") or str(model).startswith("claude-")):
        cli_model = model

    system, body = _split_messages(messages or [])
    schema = format if isinstance(format, dict) else None
    out = run(body, system or None, model=cli_model, json_schema=schema)
    if not out["ok"]:
        raise ClaudeCLIError(out["error"])

    content = out["text"] or ""
    if schema is not None:
        content = json.dumps(out["structured"], ensure_ascii=False)
    return {
        "message": {"role": "assistant", "content": content},
        "session_id": out["session_id"],
        "cost_usd": out["cost_usd"],
        "duration_ms": out["duration_ms"],
        "backend": "claude_cli",
    }


def check_status(timeout: int = 20) -> dict:
    """설치·로그인 상태 점검(모델 호출 없음). 화면에 '연결 상태' 표시용."""
    exe = find_cli()
    if not exe:
        return {"installed": False, "logged_in": False, "version": None,
                "auth_method": None, "message": "claude CLI가 설치되어 있지 않습니다."}
    status = {"installed": True, "logged_in": False, "version": None,
              "auth_method": None, "message": ""}
    try:
        ver = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
        status["version"] = (ver.stdout or "").strip() or None
        auth = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        try:
            info = json.loads(auth.stdout)
            status["logged_in"] = bool(info.get("loggedIn"))
            status["auth_method"] = info.get("authMethod")
        except json.JSONDecodeError:
            status["logged_in"] = auth.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        status["message"] = f"상태 확인 실패: {exc}"
        return status
    status["message"] = ("사용 가능" if status["logged_in"]
                         else "로그인이 필요합니다. 터미널에서 `claude` 를 실행해 로그인하세요.")
    return status


if __name__ == "__main__":
    # 동작 확인: python -m agent.claude_cli
    print(check_status())
    reply = run("한 문장으로 자기소개 해줘.")
    print(reply)
