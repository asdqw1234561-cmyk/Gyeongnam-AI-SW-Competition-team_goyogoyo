# Claude CLI(claude -p) / Ollama(Qwen3.5) 연동 - 위치 기반 주변 시설 분석 Agent
"""
pages/user.py에서 사용자가 지도로 "확정한" 검색 중심 좌표 주변의 생활시설을
자연어로 물으면, Claude Code CLI(`claude -p`, 기본) 또는 로컬 Ollama(qwen3.5:4b,
LOCATION_AGENT_BACKEND=ollama 또는 CLI 실패 시)가 어떤 조회 도구를 쓸지 계획하고
Python이 그 계획을 검증한 뒤 허용된 도구만 실제로 실행하는 작은 Agent다.

agent/planner.py(창원시 5개 구 상대 비교 추천용 Agent)와는 별개의 독립된 모듈
이다. 두 Agent 모두 "LLM의 계획을 그대로 신뢰하지 않고 Python이 검증한다"는
같은 철학을 공유하지만, 이 모듈은 planner.py를 억지로 확장하거나 하나로
합치지 않는다 - 다루는 도구·입력·검증 규칙이 서로 다르기 때문이다
(planner.py: 승인된 가중치로 5개 구 점수 계산 / 이 모듈: 확정된 좌표 주변의
개별 시설 조회).

[가장 중요한 원칙 - 검색 중심 좌표는 항상 Python이 통제]
    AI는 좌표를 전혀 제안하지 않는다(시스템 프롬프트에 좌표 필드 자체가 없다).
    실행 함수(tool_find_nearby_bus_stops 등)는 항상 run_location_agent()의
    search_center 인자로 받은 좌표만 쓴다 - AI의 JSON 응답에 lat/lon이 섞여
    들어와도 애초에 정규화 과정에서 복사하지 않으므로 실행에 영향을 줄 수 없다.
    map_click_candidate(지도에서 클릭했지만 아직 승인하지 않은 좌표)는 이 모듈에
    절대 전달하면 안 된다 - 호출 측(pages/user.py)이 반드시 승인된
    st.session_state.search_center만 넘겨야 한다.

[검색 반경도 항상 Python이 결정]
    사용자의 자연어 문장에 "300m"/"500m"/"1km"처럼 명시적인 반경이 있으면 그
    값을, 없으면 화면에서 현재 선택된 반경을 "해석된 반경"(resolved_radius_m)
    으로 미리 정하고, AI에게도 이 값을 알려준다. AI가 tool_calls에 다른
    radius_m을 적더라도 Python이 항상 해석된 반경으로 강제 치환하며(무시했다는
    사실은 notes에 기록), 자연어에 적힌 반경이 300/500/1000 중 하나가 아니면
    AI 호출 자체를 생략하고 바로 "지원하지 않는 반경"이라고 안내한다(다른 값으로
    조용히 대체하지 않음).

[허용된 도구 - 전부 기존 서비스 함수를 그대로 감싼 얇은 래퍼]
    find_nearby_bus_stops          -> services.bus_stops.find_nearby_bus_stops()
    find_nearby_convenience_stores -> services.convenience.find_nearby_stores()
    compare_nearby_facilities      -> services.bus_stops.count_nearby_by_radius() +
                                       services.convenience.count_nearby_by_radius()
    새로운 시설 수 계산식이나 데이터 조회 경로를 만들지 않는다.

[지원하지 않는 요청]
    실제 버스 이동시간·배차 간격·실시간 도착정보, 실제 도보 경로, 의료기관
    위치 검색, 주거비·매물, 범죄율·안전도, 종합적인 거주 적합도 확정 등은 현재
    데이터로 답할 수 없다. AI는 이런 요청에 관계없는 도구를 억지로 실행하지
    않고 unsupported_requests에만 담아야 한다(Python은 이 목록을 구조만
    검증하고 표시용으로만 쓴다 - 내용 자체를 신뢰해 다른 동작을 하지 않는다).

[Ollama 실패 시]
    연결 실패·JSON 해석 실패·계획 검증 실패는 모두 "기본 절차"로 안전하게
    전환한다 - compare_nearby_facilities(반경별 전체 비교)를 실행해 최소한의
    유용한 정보는 제공하되, 화면에는 "AI 분석 계획을 생성하지 못해 기본 조회
    절차를 사용했습니다"라고 명확히 표시한다(AI가 실행한 것처럼 보이지 않게).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import ollama

from agent.llm_json import extract_json_object, sanitize_goals, sanitize_unsupported_requests
from services import bus_stops, convenience
from services.bus_stops import PRESET_RADII_M as SUPPORTED_RADII_M

OLLAMA_MODEL = "qwen3.5:4b"

# 백엔드 (LOCATION_AGENT_BACKEND)
#   "claude_agent"(기본): claude -p + location MCP 서버 - Claude가 도구를 직접 반복 호출.
#                         실패하면 로컬 Ollama 계획 → 기본 절차 순으로 폴백.
#   "claude_cli": claude -p가 계획 JSON만 세우고 Python이 실행(실패 시 Ollama 폴백).
#   "ollama": 로컬 Ollama 계획만 사용.
PLANNER_BACKEND_ENV = "LOCATION_AGENT_BACKEND"
CLAUDE_CLI_MODEL_ENV = "LOCATION_AGENT_CLAUDE_MODEL"  # 비어 있으면 CLI 기본 모델 사용
CLAUDE_CLI_TIMEOUT_S = 120
CLAUDE_AGENT_TIMEOUT_S = 240

ALLOWED_TOOLS: tuple[str, ...] = (
    "find_nearby_bus_stops",
    "find_nearby_convenience_stores",
    "compare_nearby_facilities",
)

TOOL_LABELS: dict[str, str] = {
    "find_nearby_bus_stops": "주변 버스정류장 조회",
    "find_nearby_convenience_stores": "주변 편의점 조회",
    "compare_nearby_facilities": "반경별(300m/500m/1km) 시설 수 비교",
}

DEFAULT_RADIUS_M = 500
MAX_RESULTS_LIMIT = 30  # pages/user.py 슬라이더 상한과 동일하게 맞춤
MAX_TOOL_CALLS = 4
_UNSUPPORTED_REQUEST_MAX_LEN = 150  # 위치 요청 설명이 길어 planner(100자)보다 길게 허용

# ---------------------------------------------------------------------------
# 요청 키워드 패턴 - 도구 선택 범위 검증(guardrail)에만 사용하며 AI 계획을 대체하지 않음
# ---------------------------------------------------------------------------
_BUS_PATTERN = re.compile(r"버스|정류장|정류소")
_CONV_PATTERN = re.compile(r"편의점")
_NEAREST_PATTERN = re.compile(r"가장\s*가까운|제일\s*가까운")
_COMPARE_PATTERN = re.compile(r"비교|반경별|거리별")


# ---------------------------------------------------------------------------
# 도구 실행 함수 - 전부 기존 서비스 함수를 그대로 호출(새 계산식 없음)
# ---------------------------------------------------------------------------
def tool_find_nearby_bus_stops(lat: float, lon: float, radius_m: int, max_results: int) -> dict:
    return bus_stops.find_nearby_bus_stops(lat, lon, radius_m=radius_m, max_results=max_results)


def tool_find_nearby_convenience_stores(lat: float, lon: float, radius_m: int, max_results: int) -> dict:
    return convenience.find_nearby_stores(lat, lon, radius_m=radius_m, max_results=max_results)


def tool_compare_nearby_facilities(lat: float, lon: float) -> dict:
    return {
        "bus_stops": bus_stops.count_nearby_by_radius(lat, lon),
        "convenience_stores": convenience.count_nearby_by_radius(lat, lon),
    }


# ---------------------------------------------------------------------------
# 반경 해석 - 자연어에 명시된 반경을 Ollama 호출 전에 결정적으로 뽑아낸다
# ---------------------------------------------------------------------------
_RADIUS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(킬로미터|킬로|km|미터|m)\b", re.IGNORECASE)


def extract_explicit_radius_m(text: str) -> dict:
    """
    사용자 문장에서 "300m", "500미터", "1km"처럼 명시적인 반경 표현을 찾는다.

    Returns:
        {"found": False, "radius_m": None, "raw_text": None}  # 반경 언급 없음 - 화면 기본값 사용
        {"found": True, "radius_m": 500, "raw_text": "500m"}   # 지원하는 반경을 명시함
        {"found": True, "radius_m": None, "raw_text": "200m"}  # 반경을 명시했지만 지원하지 않는 값
    """
    match = _RADIUS_PATTERN.search(text or "")
    if not match:
        return {"found": False, "radius_m": None, "raw_text": None}

    value = float(match.group(1))
    unit = match.group(2).lower()
    meters = value * 1000 if unit in ("km", "킬로미터", "킬로") else value
    meters_int = int(round(meters))

    if meters_int in SUPPORTED_RADII_M:
        return {"found": True, "radius_m": meters_int, "raw_text": match.group(0)}
    return {"found": True, "radius_m": None, "raw_text": match.group(0)}


# ---------------------------------------------------------------------------
# 사용자 요청 제약조건 추출 - AI 계획의 guardrail (AI를 대체하지 않음)
# ---------------------------------------------------------------------------
def extract_request_constraints(text: str) -> dict:
    """
    사용자 문장에서 도구 선택 검증에 필요한 제약조건을 추출한다.
    이 함수는 AI의 도구 선택을 대체하지 않고, AI 계획이 사용자 요청 범위에
    맞는지 Python이 검증할 때만 쓰는 guardrail이다.

    Returns:
        {
            "requested_facilities": ["bus_stop"] | ["convenience"] | ["bus_stop","convenience"] | [],
            "explicit_radius_m": int | None,
            "wants_radius_comparison": bool,
            "nearest_only": bool,
        }
    """
    text = text or ""
    facilities: list[str] = []
    if _BUS_PATTERN.search(text):
        facilities.append("bus_stop")
    if _CONV_PATTERN.search(text):
        facilities.append("convenience")

    radius_info = extract_explicit_radius_m(text)

    return {
        "requested_facilities": facilities,
        "explicit_radius_m": radius_info["radius_m"],
        "wants_radius_comparison": bool(_COMPARE_PATTERN.search(text)),
        "nearest_only": bool(_NEAREST_PATTERN.search(text)),
    }


# ---------------------------------------------------------------------------
# 계획 수립 (Ollama 호출)
# ---------------------------------------------------------------------------
LOCATION_AGENT_SYSTEM_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 위치 기반 분석 도우미입니다.
사용자가 이미 지도에서 확정한 위치 주변의 생활시설을 조회하기 위해, 어떤 조회 도구를
사용할지 "계획"만 세우세요. 실제 데이터 조회는 Python이 담당합니다.

[사용 가능한 도구 - 이 3개뿐, 그 외 도구는 존재하지 않습니다]
1. find_nearby_bus_stops: 확정된 위치 주변의 버스정류장을 가까운 순으로 조회합니다.
   정류소명·소속 구·직선거리·데이터 품질 주의사항을 알 수 있습니다.
2. find_nearby_convenience_stores: 확정된 위치 주변의 편의점을 가까운 순으로 조회합니다.
   상호명·주소·직선거리를 알 수 있습니다.
3. compare_nearby_facilities: 300m/500m/1km 반경별 버스정류장·편의점 "전체 건수"를
   한 번에 비교합니다. 특정 반경 하나만 묻는 게 아니라 여러 반경을 비교하고 싶어하는
   요청에 적합합니다.

[규칙 - 반드시 지킬 것]
- 검색 위치(위도·경도)는 이미 사용자가 지도에서 확정했습니다. 당신은 좌표를 전혀
  언급하거나 만들어낼 수 없습니다 - tool_calls에 lat/lon 필드를 넣지 마세요.
- 검색 반경은 이미 결정되어 있습니다(아래 사용자 메시지에 안내됩니다). radius_m
  필드를 적어도 참고용일 뿐이며, 실제 조회에는 항상 그 결정된 값만 쓰입니다.
- max_results(표시 개수)는 선택 사항입니다. "가장 가까운 정류장"처럼 1곳만 필요하면
  max_results를 1로 제안하세요. 생략하면 화면에 설정된 기본값이 쓰입니다.
- 사용자가 요청한 것이 위 3개 도구로 답할 수 없는 내용이면(예: 실제 버스 이동시간·
  배차 간격·실시간 도착정보, 실제 도보 경로, 의료기관 위치, 주거비·매물, 범죄율·
  안전도, 종합적인 거주 적합도 확정 등) 그 도구를 억지로 호출하지 말고
  unsupported_requests에 요청 내용과 짧은 이유만 적으세요. 절대로 시설 수·거리·
  주소·이동시간 등 실제 데이터를 지어내지 마세요.
- 같은 도구를 중복해서 제안하지 마세요.
- 추론 과정을 출력하지 마세요. 각 도구 호출의 reason은 한 문장으로 간단히만 적으세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 설명이나 markdown은 포함하지 마세요.

[지원 가능 예시]
{"goals": ["주변 버스정류장 조회"], "tool_calls": [{"tool": "find_nearby_bus_stops", "reason": "사용자가 버스정류장 수를 물었음"}], "unsupported_requests": []}

[지원 불가 예시 - 이동시간, 월세, 배차간격 등]
{"goals": [], "tool_calls": [], "unsupported_requests": [{"request": "버스 이동시간", "reason": "이 서비스는 정류장 개수만 조회할 수 있으며 이동시간·배차간격·경로 데이터가 없습니다"}]}
"""


def _build_user_prompt(user_text: str, resolved_radius_m: int) -> str:
    return (
        f"[사용자 요청]\n{user_text}\n\n"
        f"[이번 조회에 실제로 적용될 반경(이미 결정됨)]\n{resolved_radius_m}m\n\n"
        "위 요청을 분석해 JSON 계획을 작성하세요."
    )


def _planner_backend() -> str:
    return os.environ.get(PLANNER_BACKEND_ENV, "claude_agent").strip().lower()


def _call_claude_cli_planner(user_text: str, resolved_radius_m: int) -> dict:
    """Claude Code CLI(`claude -p`)를 비대화형으로 호출해 계획(JSON)을 받는다.

    - 시스템 프롬프트와 사용자 요청은 전부 stdin으로 넘긴다(Windows의 claude.cmd
      를 거치면 여러 줄·한글 인자가 깨질 수 있어 인자는 ASCII 옵션만 쓴다).
    - --tools "" 로 내장 도구(파일 수정·Bash 등)를 모두 끄고, --system-prompt로
      Claude Code 기본 코딩 프롬프트를 대체해 "계획 JSON만 출력"하게 한다.
    - 프로젝트 CLAUDE.md·메모리가 섞이지 않도록 임시 디렉터리에서 실행한다.
    """
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("claude CLI를 찾을 수 없습니다(PATH에 claude가 없음).")

    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--effort", "low",
        "--system-prompt",
        "You are a planning assistant. Follow the instructions in the user message "
        "and reply with a single JSON object only.",
    ]
    model = os.environ.get(CLAUDE_CLI_MODEL_ENV, "").strip()
    if model:
        cmd += ["--model", model]

    stdin_text = (
        f"{LOCATION_AGENT_SYSTEM_PROMPT}\n\n"
        f"{_build_user_prompt(user_text, resolved_radius_m)}"
    )
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_CLI_TIMEOUT_S,
            cwd=tempfile.gettempdir(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"claude CLI 실행에 실패했습니다: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"claude CLI가 오류로 종료했습니다(code={proc.returncode}): {detail}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("claude CLI 출력(JSON)을 해석하지 못했습니다.") from exc
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        detail = str(envelope.get("result") if isinstance(envelope, dict) else envelope)[:300]
        raise RuntimeError(f"claude CLI가 오류 결과를 반환했습니다: {detail}")

    plan = extract_json_object(str(envelope.get("result") or ""))
    if plan is None:
        raise RuntimeError("AI가 반환한 위치 분석 계획을 JSON으로 해석하지 못했습니다.")
    return plan


# ---------------------------------------------------------------------------
# 반복형 Agent (claude -p + MCP 서버) - Claude가 도구를 직접 반복 호출
# ---------------------------------------------------------------------------
LOCATION_AGENT_LOOP_PROMPT = """당신은 경남 이주자 생활권 탐색 서비스의 위치 기반 분석 Agent입니다.
사용자가 이미 지도에서 확정한 위치 주변의 생활시설에 대한 질문에, 제공된 도구를 직접
호출해 실제 데이터를 확인한 뒤 답하세요.

[도구 - location MCP 서버의 3개뿐]
- find_nearby_bus_stops: 확정 위치 주변 버스정류장(가까운 순, 이미 결정된 반경)
- find_nearby_convenience_stores: 확정 위치 주변 편의점(가까운 순, 이미 결정된 반경)
- compare_nearby_facilities: 300m/500m/1km 반경별 두 시설 전체 건수 비교

[규칙]
- 검색 위치와 반경은 이미 Python이 정했습니다. 도구에는 좌표·반경 인자가 없습니다.
- 필요한 도구만 호출하세요. 도구 결과를 보고 정보가 부족하면 다른 도구를 더 호출해도
  됩니다. 도구가 error를 돌려주면 그 안내를 따라 다시 고르세요.
- 숫자·이름·거리·주소는 반드시 도구 결과에 있는 값만 쓰세요. 절대 지어내지 마세요.
- 실제 버스 이동시간·배차 간격·실시간 도착정보, 도보 경로, 의료기관 위치, 주거비·매물,
  범죄율·안전도, 종합 거주 적합도 확정 등은 데이터가 없습니다. 이런 요청은 도구를
  억지로 호출하지 말고 unsupported_requests에 적으세요.
- 직선거리 기준 조회 결과일 뿐이라는 점을 답변에서 과장하지 마세요.

[최종 출력 - 도구 호출을 모두 마친 뒤, 아래 JSON 객체 하나만 출력. markdown 금지]
{"goals": ["분석 목표", ...],
 "answer": "사용자 질문에 대한 한국어 답변(3~6문장, 도구 결과 수치 인용)",
 "unsupported_requests": [{"request": "...", "reason": "..."}]}
"""


def _run_location_mcp_agent(
    user_text: str,
    search_center: tuple[float, float],
    resolved_radius_m: int,
    ui_max_results: int,
    constraints: dict,
) -> dict:
    """claude -p에 location MCP 서버를 붙여 실행한다. Claude가 도구를 반복 호출
    하며 최종 답변 JSON을 만들고, MCP 서버가 남긴 호출 로그로 실제 실행 내역을
    복원한다. 실패는 모두 RuntimeError로 통일한다.

    Returns: {"final": dict, "steps": [MCP 호출 로그 entry, ...]}
    """
    from agent import location_mcp_server as srv  # 순환 import 방지를 위해 지연 import

    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("claude CLI를 찾을 수 없습니다(PATH에 claude가 없음).")

    lat, lon = search_center
    with tempfile.TemporaryDirectory(prefix="location_agent_") as work_dir:
        log_path = os.path.join(work_dir, "tool_calls.jsonl")
        mcp_config_path = os.path.join(work_dir, "mcp.json")
        mcp_config = {
            "mcpServers": {
                srv.SERVER_NAME: {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [os.path.abspath(srv.__file__)],
                    "env": {
                        srv.ENV_LAT: repr(lat),
                        srv.ENV_LON: repr(lon),
                        srv.ENV_RADIUS_M: str(resolved_radius_m),
                        srv.ENV_MAX_RESULTS: str(ui_max_results),
                        srv.ENV_CONSTRAINTS: json.dumps(constraints, ensure_ascii=False),
                        srv.ENV_LOG: log_path,
                        "PYTHONIOENCODING": "utf-8",
                    },
                }
            }
        }
        with open(mcp_config_path, "w", encoding="utf-8") as f:
            json.dump(mcp_config, f, ensure_ascii=False)

        allowed = ",".join(f"mcp__{srv.SERVER_NAME}__{t}" for t in ALLOWED_TOOLS)
        cmd = [
            exe, "-p",
            "--output-format", "json",
            "--tools", "",
            "--mcp-config", mcp_config_path,
            "--strict-mcp-config",
            "--allowedTools", allowed,
            "--no-session-persistence",
            "--disable-slash-commands",
            "--effort", "low",
            "--system-prompt",
            "You are a location analysis agent. Use the provided MCP tools as instructed "
            "in the user message, then reply with a single JSON object only.",
        ]
        model = os.environ.get(CLAUDE_CLI_MODEL_ENV, "").strip()
        if model:
            cmd += ["--model", model]

        stdin_text = (
            f"{LOCATION_AGENT_LOOP_PROMPT}\n\n"
            f"[사용자 요청]\n{user_text}\n\n"
            f"[이번 조회에 적용되는 반경(이미 결정됨)]\n{resolved_radius_m}m"
        )
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=CLAUDE_AGENT_TIMEOUT_S,
                cwd=work_dir,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"claude CLI Agent 실행에 실패했습니다: {exc}") from exc

        steps: list[dict] = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                steps = [json.loads(line) for line in f if line.strip()]

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"claude CLI Agent가 오류로 종료했습니다(code={proc.returncode}): {detail}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("claude CLI Agent 출력(JSON)을 해석하지 못했습니다.") from exc
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        detail = str(envelope.get("result") if isinstance(envelope, dict) else envelope)[:300]
        raise RuntimeError(f"claude CLI Agent가 오류 결과를 반환했습니다: {detail}")

    final = extract_json_object(str(envelope.get("result") or ""))
    if final is None or not str(final.get("answer") or "").strip():
        raise RuntimeError("AI Agent의 최종 답변을 JSON으로 해석하지 못했습니다.")
    return {"final": final, "steps": steps}


def call_location_planner(user_text: str, resolved_radius_m: int) -> dict:
    """설정된 백엔드로 위치 분석 계획(JSON)을 받는다. 기본은 claude CLI이며,
    실패하면 로컬 Ollama로 한 번 더 시도한다. 모든 실패는 RuntimeError 하나로
    통일한다 - run_location_agent()가 이 예외 하나만 잡으면 "기본 절차"
    폴백으로 안전하게 전환할 수 있다."""
    if _planner_backend() != "claude_cli":
        return _call_ollama_planner(user_text, resolved_radius_m)

    try:
        return _call_claude_cli_planner(user_text, resolved_radius_m)
    except RuntimeError as claude_exc:
        try:
            return _call_ollama_planner(user_text, resolved_radius_m)
        except RuntimeError as ollama_exc:
            raise RuntimeError(f"{claude_exc} / Ollama 대체 호출도 실패: {ollama_exc}") from ollama_exc


def _call_ollama_planner(user_text: str, resolved_radius_m: int) -> dict:
    """로컬 Ollama(qwen3.5:4b)를 호출해 위치 분석 계획(JSON)을 받는다."""
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": LOCATION_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(user_text, resolved_radius_m)},
            ],
            options={"temperature": 0.0},
            # qwen3.5는 기본적으로 "생각 과정"(thinking)을 먼저 길게 생성한다 - 실측
            # 결과, 이 시스템 프롬프트처럼 긴 지시문에서는 생각 과정만으로 응답 토큰
            # 예산을 다 써버려 최종 JSON(content)이 아예 비어서 돌아오는 경우가
            # 있었다(검증 단계에서 안전하게 기본 절차로 폴백되긴 하지만, AI가 실제로
            # 계획을 세울 기회조차 못 받는 것은 이 기능의 취지와 맞지 않는다).
            # think=False로 생각 과정 생성을 끄면 모델이 곧바로 JSON을 출력한다.
            think=False,
        )
    except Exception as exc:  # Ollama 서버 미실행 등
        raise RuntimeError(f"Ollama 위치 분석 계획 호출에 실패했습니다: {exc}") from exc

    raw_text = response["message"]["content"]
    plan = extract_json_object(raw_text)
    if plan is None:
        raise RuntimeError("AI가 반환한 위치 분석 계획을 JSON으로 해석하지 못했습니다.")
    return plan


# ---------------------------------------------------------------------------
# 도구 선택 범위 보정 - AI가 제안한 도구가 사용자 요청 범위보다 넓으면 최소 도구로 교체
# ---------------------------------------------------------------------------
def _apply_scope_corrections(
    tool_calls: list[dict],
    constraints: dict,
    resolved_radius_m: int,
    ui_max_results: int,
) -> tuple[list[dict], list[dict]]:
    """
    검증·정규화된 tool_calls를 사용자 요청 범위에 맞게 최소 보정한다.

    보정 조건:
    - compare_nearby_facilities + wants_radius_comparison=False + 특정 시설 요청
      → 단일 시설 도구(들)로 교체
    - 단일 시설 도청에 반대 시설 도구가 제안된 경우 교체
    - nearest_only=True 시 단일 시설 도구의 max_results=1 강제

    Returns:
        (보정된 tool_calls, corrections 기록 목록)
        corrections 항목: {"original_tool", "corrected_to": [str,...], "note"}
    """
    facilities = constraints.get("requested_facilities", [])
    wants_comparison = constraints.get("wants_radius_comparison", False)
    nearest_only = constraints.get("nearest_only", False)

    if not facilities and not nearest_only:
        return tool_calls, []

    corrections: list[dict] = []
    result: list[dict] = []
    added_tools: set[str] = set()

    for call in tool_calls:
        tool = call["tool"]

        # compare_nearby_facilities → 단일 시설 도구로 보정
        if tool == "compare_nearby_facilities" and not wants_comparison and facilities:
            new_calls: list[dict] = []
            if "bus_stop" in facilities and "find_nearby_bus_stops" not in added_tools:
                new_calls.append({
                    "tool": "find_nearby_bus_stops",
                    "reason": call.get("reason", ""),
                    "radius_m": resolved_radius_m,
                    "max_results": 1 if nearest_only else ui_max_results,
                })
            if "convenience" in facilities and "find_nearby_convenience_stores" not in added_tools:
                new_calls.append({
                    "tool": "find_nearby_convenience_stores",
                    "reason": call.get("reason", ""),
                    "radius_m": resolved_radius_m,
                    "max_results": 1 if nearest_only else ui_max_results,
                })
            if new_calls:
                to_labels = " + ".join(TOOL_LABELS.get(c["tool"], c["tool"]) for c in new_calls)
                corrections.append({
                    "original_tool": tool,
                    "corrected_to": [c["tool"] for c in new_calls],
                    "note": (
                        f"AI가 '{TOOL_LABELS[tool]}'을 제안했지만 사용자는 반경별 비교가 아닌 "
                        f"단일 조회를 요청했습니다. '{to_labels}'으로 보정했습니다."
                    ),
                })
                for c in new_calls:
                    result.append(c)
                    added_tools.add(c["tool"])
            continue

        # 버스정류장 도구가 제안됐지만 편의점만 요청한 경우
        if (
            tool == "find_nearby_bus_stops"
            and facilities
            and "bus_stop" not in facilities
            and "convenience" in facilities
        ):
            if "find_nearby_convenience_stores" not in added_tools:
                corrected = {**call, "tool": "find_nearby_convenience_stores"}
                corrections.append({
                    "original_tool": tool,
                    "corrected_to": ["find_nearby_convenience_stores"],
                    "note": "AI가 버스정류장 조회를 제안했지만 사용자는 편의점을 요청했습니다. 편의점 조회로 보정했습니다.",
                })
                result.append(corrected)
                added_tools.add("find_nearby_convenience_stores")
            continue

        # 편의점 도구가 제안됐지만 버스정류장만 요청한 경우
        if (
            tool == "find_nearby_convenience_stores"
            and facilities
            and "convenience" not in facilities
            and "bus_stop" in facilities
        ):
            if "find_nearby_bus_stops" not in added_tools:
                corrected = {**call, "tool": "find_nearby_bus_stops"}
                corrections.append({
                    "original_tool": tool,
                    "corrected_to": ["find_nearby_bus_stops"],
                    "note": "AI가 편의점 조회를 제안했지만 사용자는 버스정류장을 요청했습니다. 버스정류장 조회로 보정했습니다.",
                })
                result.append(corrected)
                added_tools.add("find_nearby_bus_stops")
            continue

        # nearest_only: 단일 시설 도구의 max_results=1 강제
        if nearest_only and tool in ("find_nearby_bus_stops", "find_nearby_convenience_stores"):
            if call.get("max_results", ui_max_results) != 1:
                corrections.append({
                    "original_tool": tool,
                    "corrected_to": [tool],
                    "note": (
                        f"'가장 가까운' 요청으로 max_results를 "
                        f"{call.get('max_results', ui_max_results)}에서 1로 보정했습니다."
                    ),
                })
                call = {**call, "max_results": 1}

        # 보정 없음 - 중복 방지 후 추가
        if tool == "compare_nearby_facilities" or tool not in added_tools:
            result.append(call)
            if tool != "compare_nearby_facilities":
                added_tools.add(tool)

    return result, corrections


# ---------------------------------------------------------------------------
# 계획 검증 - LLM의 계획을 그대로 신뢰하지 않는다
# ---------------------------------------------------------------------------
def validate_and_normalize_plan(plan: object, resolved_radius_m: int, ui_max_results: int, constraints: dict | None = None) -> dict:
    """
    AI가 제안한 계획(plan)을 검증해 Python이 실제로 실행할 수 있는 형태로
    정규화한다. tool_calls가 빈 리스트인 것 자체는 유효하다(지원하지 않는
    요청만 들어와서 실행할 도구가 없는 정상적인 경우) - 그 경우는 run_location_
    agent()가 unsupported_requests 존재 여부로 다시 한 번 판단한다.

    확인하는 것:
    1. plan이 올바른 구조(dict + tool_calls가 리스트)인지
    2. 각 tool_call의 "tool"이 ALLOWED_TOOLS 안에 있는지(없으면 전체 계획 거부)
    3. 도구 호출 수가 MAX_TOOL_CALLS를 넘지 않는지
    4. radius_m은 AI가 뭘 제안했든 항상 resolved_radius_m으로 강제 치환(다르면
       notes에 기록)
    5. max_results는 AI가 제안했다면 1~MAX_RESULTS_LIMIT로 clamp, 아니면 화면
       기본값(ui_max_results) 사용
    6. lat/lon 필드가 섞여 있으면 완전히 무시하고 notes에 기록(검색 중심 보호)
    7. 동일한 (도구, 반경, 표시개수) 조합이 중복 제안되면 한 번만 실행

    Returns:
        {"status": "ok", "tool_calls": [...], "notes": [...]} | {"status": "rejected", "reason": str}
    """
    if not isinstance(plan, dict):
        return {"status": "rejected", "reason": "AI 응답이 JSON 객체가 아닙니다."}

    raw_calls = plan.get("tool_calls")
    if not isinstance(raw_calls, list):
        return {"status": "rejected", "reason": "tool_calls가 리스트가 아닙니다."}
    if len(raw_calls) > MAX_TOOL_CALLS:
        return {"status": "rejected", "reason": f"제안된 도구 호출이 너무 많습니다(최대 {MAX_TOOL_CALLS}개)."}

    notes: list[str] = []
    normalized: list[dict] = []
    seen_signatures: set[tuple] = set()

    for i, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            return {"status": "rejected", "reason": f"{i + 1}번째 도구 호출 형식이 올바르지 않습니다."}

        tool = call.get("tool")
        if tool not in ALLOWED_TOOLS:
            return {"status": "rejected", "reason": f"허용되지 않은 도구 '{tool}'입니다."}

        if "lat" in call or "lon" in call:
            notes.append("AI가 좌표를 제안했지만 무시하고 승인된 검색 중심 좌표만 사용했습니다.")

        reason = str(call.get("reason") or "").strip()[:200]
        entry: dict = {"tool": tool, "reason": reason}

        if tool != "compare_nearby_facilities":
            proposed_radius = call.get("radius_m")
            if proposed_radius is not None and proposed_radius != resolved_radius_m:
                notes.append(
                    f"AI가 제안한 반경({proposed_radius}m)을 무시하고 실제 적용 반경"
                    f"({resolved_radius_m}m)을 사용했습니다."
                )
            entry["radius_m"] = resolved_radius_m

            max_results = ui_max_results
            proposed_mr = call.get("max_results")
            if isinstance(proposed_mr, (int, float)) and not isinstance(proposed_mr, bool):
                max_results = max(1, min(int(proposed_mr), MAX_RESULTS_LIMIT))
            entry["max_results"] = max_results

        sig = (tool, entry.get("radius_m"), entry.get("max_results"))
        if sig in seen_signatures:
            notes.append(f"'{TOOL_LABELS.get(tool, tool)}' 도구가 중복 제안되어 한 번만 실행합니다.")
            continue
        seen_signatures.add(sig)
        normalized.append(entry)

    corrections: list[dict] = []
    if constraints is not None:
        normalized, corrections = _apply_scope_corrections(normalized, constraints, resolved_radius_m, ui_max_results)

    return {"status": "ok", "tool_calls": normalized, "notes": notes, "corrections": corrections}


# ---------------------------------------------------------------------------
# 도구 실행 - 검증을 통과한 계획만, 승인된 좌표로만 실행
# ---------------------------------------------------------------------------
def _execute_tool_calls(lat: float, lon: float, tool_calls: list[dict]) -> list[dict]:
    log: list[dict] = []
    for call in tool_calls:
        tool = call["tool"]
        entry: dict = {"tool": tool, "reason": call.get("reason", "")}
        try:
            if tool == "find_nearby_bus_stops":
                entry["radius_m"] = call["radius_m"]
                entry["max_results"] = call["max_results"]
                entry["result"] = tool_find_nearby_bus_stops(lat, lon, call["radius_m"], call["max_results"])
            elif tool == "find_nearby_convenience_stores":
                entry["radius_m"] = call["radius_m"]
                entry["max_results"] = call["max_results"]
                entry["result"] = tool_find_nearby_convenience_stores(lat, lon, call["radius_m"], call["max_results"])
            elif tool == "compare_nearby_facilities":
                entry["result"] = tool_compare_nearby_facilities(lat, lon)
            entry["executed"] = True
            entry["error"] = None
        except Exception as exc:  # 허용 도구는 안정적이어야 하지만 방어적으로 한 번 더 감싼다
            entry["executed"] = False
            entry["error"] = str(exc)
        log.append(entry)
    return log


# ---------------------------------------------------------------------------
# 답변 숫자 검증 - Claude 답변의 숫자가 실제 도구 결과에 있는지 Python이 확인
# ---------------------------------------------------------------------------
_NUMBER_PATTERN = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _numbers_in_text(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUMBER_PATTERN.findall(text or "")}


def _collect_result_numbers(value: object, out: set[float]) -> None:
    """도구 결과 안의 모든 숫자(값, 숫자 형태의 dict 키, 문자열 속 숫자 - 주소·상호명·
    기준일 등)를 모은다. 결과는 MCP 로그(JSON)를 거쳐 와서 반경별 counts의 키가
    문자열("300")일 수 있으므로 키도 함께 본다."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        out.update(_numbers_in_text(value))
        # 기준년월 "202606"·기준일 "20251231"처럼 붙어 있는 날짜는 답변에서 "2026년 6월"로
        # 풀어 쓰는 경우가 많아 연·월·일로도 나눠 허용한다.
        for token in re.findall(r"\d+", value):
            if len(token) in (6, 8):
                out.update({float(token[:4]), float(token[4:6])})
                if len(token) == 8:
                    out.add(float(token[6:8]))
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect_result_numbers(k, out)
            _collect_result_numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect_result_numbers(v, out)


def verify_answer_numbers(
    answer: str, executed_tool_calls: list[dict], user_text: str, resolved_radius_m: int
) -> dict:
    """
    Claude가 쓴 답변 문장 속 숫자가 "실제로 실행된 도구 결과"에서 확인되는지 검사한다.
    답변 문장을 고치지 않고 검사 결과만 돌려준다(화면이 경고를 띄우는 데 쓴다).

    허용하는 숫자: 실행된 도구 결과의 모든 숫자, 사용자 질문의 숫자, 적용 반경과
    지원 반경(m 및 km 표기).

    Returns:
        {"status": "ok" | "no_numbers" | "unverified_numbers", "unverified": [str, ...]}
    """
    answer_numbers = _numbers_in_text(answer)
    if not answer_numbers:
        return {"status": "no_numbers", "unverified": []}

    allowed: set[float] = set()
    for entry in executed_tool_calls:
        if entry.get("executed"):
            _collect_result_numbers(entry.get("result"), allowed)
    allowed |= _numbers_in_text(user_text)
    for r in (resolved_radius_m, *SUPPORTED_RADII_M):
        allowed |= {float(r), r / 1000}

    unverified = sorted(n for n in answer_numbers if n not in allowed)
    if not unverified:
        return {"status": "ok", "unverified": []}
    return {"status": "unverified_numbers", "unverified": [f"{n:g}" for n in unverified]}


def _build_agent_result(
    agent_out: dict,
    user_text: str,
    search_center: tuple[float, float],
    resolved_radius_m: int,
    radius_source: str,
    constraints: dict,
) -> dict:
    """반복형 Agent 결과를 run_location_agent()의 공통 반환 형식으로 맞춘다.
    executed_tool_calls에는 MCP 서버가 실제로 실행한 호출만 담고(차단된 호출은
    agent_steps와 notes로만 표시), 화면이 기존 렌더러를 그대로 쓸 수 있게 한다."""
    final = agent_out["final"]
    steps = agent_out["steps"]

    executed = [
        {k: s.get(k) for k in ("tool", "reason", "radius_m", "max_results", "result", "executed", "error")}
        for s in steps
        if not s.get("blocked")
    ]
    notes: list[str] = []
    for s in steps:
        label = TOOL_LABELS.get(s.get("tool"), s.get("tool"))
        if s.get("blocked"):
            notes.append(f"Python이 '{label}' 호출을 차단하고 AI에게 다시 고르게 했습니다: {s.get('error')}")
        elif s.get("note"):
            notes.append(f"'{label}': {s['note']}")

    answer = str(final.get("answer") or "").strip()[:2000]
    verification = verify_answer_numbers(answer, executed, user_text, resolved_radius_m)
    if verification["status"] == "unverified_numbers":
        notes.append(
            "AI 답변의 일부 수치(" + ", ".join(verification["unverified"]) + ")를 실제 조회 결과에서 "
            "확인하지 못했습니다."
        )

    return {
        "status": "ok",
        "message": None,
        "mode": "ai_agent",
        "user_text": user_text,
        "search_center": search_center,
        "resolved_radius_m": resolved_radius_m,
        "radius_source": radius_source,
        "goals": sanitize_goals(final.get("goals")),
        "unsupported_requests": sanitize_unsupported_requests(
            final.get("unsupported_requests"), request_max_len=_UNSUPPORTED_REQUEST_MAX_LEN
        ),
        "planned_tool_calls": None,
        "executed_tool_calls": executed,
        "notes": notes,
        "planner_error": None,
        "constraints": constraints,
        "corrections": [],
        "agent_answer": answer,
        "answer_verification": verification,
        "agent_steps": [
            {"tool": s.get("tool"), "reason": s.get("reason", ""), "blocked": bool(s.get("blocked")),
             "executed": bool(s.get("executed")), "error": s.get("error")}
            for s in steps
        ],
    }


_DEFAULT_FALLBACK_CALL = {
    "tool": "compare_nearby_facilities",
    "reason": "(기본 절차) AI 계획을 사용할 수 없어 반경별 기본 비교를 실행",
}


# ---------------------------------------------------------------------------
# 최상위 진입점
# ---------------------------------------------------------------------------
def run_location_agent(
    user_text: str,
    search_center: tuple[float, float],
    ui_radius_m: int,
    ui_max_results: int,
) -> dict:
    """
    pages/user.py의 '🤖 AI 분석 실행' 버튼 클릭 시에만 호출해야 한다(지도 이동·
    레이어 토글·반경 변경 등 다른 재실행에서는 호출하면 안 됨 - 호출 측이 보장).

    Args:
        user_text: 사용자가 입력한 자연어 요청.
        search_center: 반드시 사용자가 "승인"한 확정 좌표(st.session_state.
            search_center)만 넘긴다. map_click_candidate(미확정 좌표)는 여기
            넘기면 안 된다.
        ui_radius_m: 화면에서 현재 선택된 반경(300/500/1000). 자연어에 명시적
            반경이 없을 때 기본값으로 쓰인다.
        ui_max_results: 화면에서 현재 선택된 최대 표시 개수. AI가 max_results를
            제안하지 않았을 때 기본값으로 쓰인다.

    Returns:
        {
            "status": "ok" | "rejected_input",
            "message": str | None,              # status=="rejected_input"일 때만(예: 지원 안 하는 반경)
            "mode": "ai_agent" | "ai_planned" | "fallback_default" | None,
            "agent_answer": str,                 # mode=="ai_agent"일 때만 - Claude의 최종 답변
            "agent_steps": [...],                # mode=="ai_agent"일 때만 - 차단 포함 호출 순서
            "answer_verification": {"status", "unverified"},  # mode=="ai_agent"일 때만 - 답변 숫자 검증
            "user_text": str,
            "search_center": (lat, lon),         # 이 결과가 어느 좌표에서 실행됐는지(이후 비교용)
            "resolved_radius_m": int | None,
            "radius_source": "explicit_text" | "ui_default" | None,
            "goals": [str, ...],
            "unsupported_requests": [{"request","reason"}, ...],
            "planned_tool_calls": [...] | None,  # AI가 "제안"한 원본(검증 전) - 표시 전용
            "executed_tool_calls": [...],         # Python이 실제로 실행한 도구 호출 로그만
            "notes": [str, ...],
            "planner_error": str | None,
        }
    """
    lat, lon = search_center

    extract = extract_explicit_radius_m(user_text)
    if extract["found"] and extract["radius_m"] is None:
        supported_labels = ", ".join(f"{r}m" if r != 1000 else "1km" for r in SUPPORTED_RADII_M)
        return {
            "status": "rejected_input",
            "message": f"'{extract['raw_text']}'은(는) 현재 지원하지 않는 반경입니다. {supported_labels}만 지원합니다.",
            "mode": None,
            "user_text": user_text,
            "search_center": search_center,
            "resolved_radius_m": None,
            "radius_source": None,
            "goals": [],
            "unsupported_requests": [],
            "planned_tool_calls": None,
            "executed_tool_calls": [],
            "notes": [],
            "planner_error": None,
            "constraints": None,
            "corrections": [],
        }

    if extract["found"]:
        resolved_radius_m = extract["radius_m"]
        radius_source = "explicit_text"
    else:
        resolved_radius_m = ui_radius_m if ui_radius_m in SUPPORTED_RADII_M else DEFAULT_RADIUS_M
        radius_source = "ui_default"

    constraints = extract_request_constraints(user_text)

    agent_error: str | None = None
    if _planner_backend() == "claude_agent":
        try:
            agent_out = _run_location_mcp_agent(
                user_text, search_center, resolved_radius_m, ui_max_results, constraints
            )
        except RuntimeError as exc:
            agent_error = str(exc)
        else:
            return _build_agent_result(
                agent_out, user_text, search_center, resolved_radius_m, radius_source, constraints
            )

    planner_error: str | None = None
    plan: dict | None = None
    try:
        plan = call_location_planner(user_text, resolved_radius_m)
    except RuntimeError as exc:
        planner_error = str(exc)
    if agent_error is not None and planner_error is not None:
        planner_error = f"{agent_error} / {planner_error}"

    if planner_error is None:
        validated = validate_and_normalize_plan(plan, resolved_radius_m, ui_max_results, constraints=constraints)
    else:
        validated = {"status": "rejected", "reason": planner_error}

    notes: list[str] = []
    corrections: list[dict] = []
    if validated["status"] == "ok":
        tool_calls = validated["tool_calls"]
        notes = list(validated["notes"])
        corrections = list(validated.get("corrections", []))
        unsupported = sanitize_unsupported_requests(
            plan.get("unsupported_requests") if isinstance(plan, dict) else None,
            request_max_len=_UNSUPPORTED_REQUEST_MAX_LEN,
        )
        goals = sanitize_goals(plan.get("goals") if isinstance(plan, dict) else None)
        if not tool_calls and not unsupported:
            # AI가 실행할 도구도, 지원 불가 사유도 제시하지 않은 빈 응답 - 기본 절차로.
            mode = "fallback_default"
            tool_calls = [_DEFAULT_FALLBACK_CALL]
            notes.append("AI가 실행할 도구나 지원 불가 사유를 제시하지 않아 기본 절차로 전환했습니다.")
        else:
            mode = "ai_planned"
    else:
        mode = "fallback_default"
        tool_calls = [_DEFAULT_FALLBACK_CALL]
        unsupported = []
        goals = []
        if planner_error is None:
            notes.append(f"AI 계획을 검증하지 못해 기본 절차로 전환했습니다: {validated['reason']}")

    if agent_error is not None and planner_error is None:
        notes.insert(0, f"Claude Agent 실행에 실패해 로컬 Ollama 계획으로 전환했습니다: {agent_error}")

    executed = _execute_tool_calls(lat, lon, tool_calls)

    return {
        "status": "ok",
        "message": None,
        "mode": mode,
        "user_text": user_text,
        "search_center": search_center,
        "resolved_radius_m": resolved_radius_m,
        "radius_source": radius_source,
        "goals": goals,
        "unsupported_requests": unsupported,
        "planned_tool_calls": plan.get("tool_calls") if isinstance(plan, dict) else None,
        "executed_tool_calls": executed,
        "notes": notes,
        "planner_error": planner_error,
        "constraints": constraints,
        "corrections": corrections,
    }
