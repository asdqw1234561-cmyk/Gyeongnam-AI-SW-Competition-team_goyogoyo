# 실제 LLM(Claude CLI 또는 Ollama)으로 Agent 기능을 직접 실행해 보는 확인용 스크립트
"""
단위 테스트(tests/)는 가짜 AI 응답으로 돌아간다. 이 스크립트는 .env 의 LLM_BACKEND 로
실제 AI를 불러서 결과를 화면에 출력한다. 데이터나 파일은 바꾸지 않는다.

사용법 (프로젝트 루트에서):
    python scripts/try_agents.py status                      # CLI 설치·로그인 상태만 확인 (AI 호출 없음)
    python scripts/try_agents.py ping                        # CLI 응답이 느리거나 멈출 때 단계별 진단
    python scripts/try_agents.py location "가장 가까운 편의점 알려줘"
    python scripts/try_agents.py chat                        # 위치 분석 대화 (후속 질문, 빈 줄로 종료)
    python scripts/try_agents.py recommend                   # 최초 추천 + AI 설명
    python scripts/try_agents.py recommend --weights bus_stop_count=50 hospital_count=50

옵션:
    --backend claude_cli|ollama     .env 대신 이번 실행에만 백엔드 지정
    --model haiku|sonnet|opus       Claude CLI 모델 (기본: .env 의 CLAUDE_CLI_MODEL)
    --lat 35.2280 --lon 128.6811    위치 분석 검색 중심 (기본: 창원시청 부근)
    --radius 300|500|1000           기본 반경 (기본 500)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Windows 콘솔에서 한글이 깨지지 않게
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_CENTER = (35.2280, 128.6811)
TOOL_NAMES = {
    "find_nearby_bus_stops": "버스정류장 조회",
    "find_nearby_convenience_stores": "편의점 조회",
    "compare_nearby_facilities": "반경별 비교",
    "get_region_indicators": "지표 추가 조회",
    "simulate_weights": "가중치 가정 계산",
}


def _line(char="-"):
    print(char * 60)


def _print_steps(steps):
    for s in steps or []:
        text = f"  [판단 {s['round']}회차] {s['action']}"
        if s.get("reason"):
            text += f" - {s['reason']}"
        print(text)
        if s.get("executed_tools"):
            print("     실행:", ", ".join(TOOL_NAMES.get(t, t) for t in s["executed_tools"]))
        if s.get("note"):
            print("    ", s["note"])


def _print_final(final):
    if not final:
        print("  (최종 답변 없음)")
        return
    label = "AI 답변 (숫자 검증 통과)" if final["source"] == "ai_verified" else "Python 요약 (AI 답변 미사용)"
    print(f"  ▶ {label}")
    for line in final["text"].splitlines():
        print("   ", line)
    if final.get("rejected_reason"):
        print("    AI 답변을 쓰지 않은 이유:", final["rejected_reason"])


def run_location(question, center, radius, history=None):
    from agent.location_agent import run_location_agent
    started = time.time()
    result = run_location_agent(question, center, radius, 10, history=history)
    _line()
    print(f"질문: {question}   ({time.time() - started:.1f}초)")
    if result["status"] != "ok":
        print("  입력 거부:", result["message"])
        return result
    print(f"  모드: {result['mode']}  · 반경 {result['resolved_radius_m']}m ({result['radius_source']})")
    if result.get("planner_error"):
        print("  AI 계획 오류:", result["planner_error"])
    for u in result.get("unsupported_requests") or []:
        print(f"  지원하지 않는 요청: {u['request']} - {u['reason']}")
    for e in result["executed_tool_calls"]:
        mark = "OK" if e["executed"] else "실패"
        print(f"  [{e.get('step', 1)}단계 실행] {TOOL_NAMES.get(e['tool'], e['tool'])} ({mark})")
    _print_steps(result.get("agent_steps"))
    if result.get("review_error"):
        print("  결과 검토 오류:", result["review_error"])
    _print_final(result.get("final_answer"))
    return result


def cmd_status(_args):
    from agent import claude_cli, llm
    print("LLM_BACKEND:", llm.get_backend())
    print("Claude CLI :", claude_cli.check_status())
    print("사용량 제한 :", llm.get_usage())


def cmd_ping(args):
    """CLI 호출을 3단계로 나눠 어디서 멈추는지 확인한다(각 단계 최대 args.timeout 초)."""
    import subprocess
    from agent import claude_cli

    exe = claude_cli.find_cli()
    print("claude 경로:", exe)
    if not exe:
        print("→ claude 를 찾지 못했습니다. 설치 후 새 터미널에서 다시 실행하거나 .env 에 CLAUDE_CLI_PATH 를 지정하세요.")
        return
    print("상태:", claude_cli.check_status())

    def timed(label, cmd, stdin_text=None):
        _line()
        print(label)
        print("  명령:", " ".join(f'"{c}"' if (" " in c or c == "") else c for c in cmd))
        started = time.time()
        cp, timed_out, partial = claude_cli._run_process(cmd, stdin_text or "", os.getcwd(), args.timeout)
        took = time.time() - started
        if timed_out:
            print(f"  ✗ {args.timeout}초 안에 응답 없음 (중단함). 받은 출력: {partial or '(없음)'}")
            return False
        print(f"  {'✓' if cp.returncode == 0 else '✗'} 종료코드 {cp.returncode}, {took:.1f}초")
        print("  stdout:", (cp.stdout or "").strip()[:300] or "(없음)")
        if (cp.stderr or "").strip():
            print("  stderr:", cp.stderr.strip()[:300])
        return cp.returncode == 0

    model = ["--model", os.environ["CLAUDE_CLI_MODEL"]] if os.environ.get("CLAUDE_CLI_MODEL") else []
    ok1 = timed("[1] 가장 단순한 호출 (질문을 명령줄 인자로)",
                [exe, "-p", "Reply with just OK", "--output-format", "json"] + model)
    ok2 = timed("[2] 질문을 표준입력으로 전달",
                [exe, "-p", "--output-format", "json"] + model, "Reply with just OK")
    _line()
    print("[3] 앱이 실제로 쓰는 방식 (도구 차단·safe-mode 등 전체 옵션)")
    started = time.time()
    r = claude_cli.run("OK 라고만 답해", timeout=args.timeout)
    print(f"  {'✓' if r['ok'] else '✗'} {time.time() - started:.1f}초 ·", r["text"] if r["ok"] else r["error"])
    _line()
    print("[4] 실제 위치 분석 프롬프트로 호출 (긴 한국어 시스템 프롬프트, CLI 디버그 로그 기록)")
    import tempfile
    from agent import location_agent
    debug_path = os.path.join(tempfile.gettempdir(), "claude_cli_debug.log")
    try:
        os.remove(debug_path)
    except OSError:
        pass
    os.environ["CLAUDE_CLI_DEBUG_FILE"] = debug_path
    user_prompt = location_agent._build_user_prompt("가장 가까운 편의점 알려줘", 500)
    print(f"  시스템 프롬프트 {len(location_agent.LOCATION_AGENT_SYSTEM_PROMPT)}자, 사용자 프롬프트 {len(user_prompt)}자")
    started = time.time()
    r4 = claude_cli.run(user_prompt, location_agent.LOCATION_AGENT_SYSTEM_PROMPT, timeout=args.timeout)
    os.environ.pop("CLAUDE_CLI_DEBUG_FILE", None)
    print(f"  {'✓' if r4['ok'] else '✗'} {time.time() - started:.1f}초 ·", (r4["text"] or r4["error"] or "")[:300])
    try:
        with open(debug_path, encoding="utf-8", errors="replace") as f:
            log_lines = f.read().splitlines()
        print(f"  CLI 디버그 로그: {debug_path} ({len(log_lines)}줄) - 마지막 25줄:")
        for line in log_lines[-25:]:
            print("   |", line[:220])
    except OSError:
        print("  (디버그 로그 파일이 만들어지지 않았습니다)")

    _line("=")
    if ok1 and ok2 and r["ok"] and not r4["ok"]:
        print("진단: 짧은 프롬프트는 정상, 실제 위치 분석 프롬프트에서만 실패 → 위 [4] 결과와 디버그 로그를 공유해 주세요.")
        return
    if not ok1:
        print("진단: 가장 단순한 호출부터 실패/지연 → CLI 설치·로그인·네트워크 문제입니다.")
        print("      터미널에서 `claude` 를 직접 실행해 로그인 상태와 첫 실행 안내가 남아 있는지 확인하세요.")
    elif not ok2:
        print("진단: 표준입력 전달에서 멈춤 → Windows 의 claude 실행 방식 문제일 수 있습니다. 결과를 공유해 주세요.")
    elif not r["ok"]:
        print("진단: 기본 호출은 되지만 앱 옵션 조합에서 실패 → 위 [3] 오류 내용을 공유해 주세요.")
    else:
        print("진단: 세 단계 모두 정상입니다. 앞서의 지연은 일시적인 서버 지연이었을 가능성이 큽니다.")


def cmd_location(args):
    run_location(args.question, (args.lat, args.lon), args.radius)


def cmd_chat(args):
    from agent.agent_state import ConversationMemory
    memory = ConversationMemory({})
    center = (args.lat, args.lon)
    print("위치 분석 대화를 시작합니다. 빈 줄을 입력하면 끝납니다.")
    while True:
        try:
            question = input("\n질문> ").strip()
        except EOFError:
            break
        if not question:
            break
        result = run_location(question, center, args.radius, history=memory.recent(search_center=center))
        memory.add_from_result(result)


def cmd_recommend(args):
    from agent.planner import run_agent_plan
    weights = None
    if args.weights:
        weights = {}
        for item in args.weights:
            code, _, value = item.partition("=")
            weights[code] = float(value)
    conditions = {"bus_stop_count": "교통", "hospital_count": "의료", "convenience_store_count": "생활편의(마트/편의점)"}
    selected = [conditions[c] for c in (weights or {"bus_stop_count": 1, "convenience_store_count": 1}) if c in conditions]
    started = time.time()
    result = run_agent_plan(selected, weights, "창원시", 3)
    _line("=")
    print(f"최초 추천  ({time.time() - started:.1f}초)  모드: {result['mode']}")
    print("  승인된 가중치:", result["approved_weights"])
    if result.get("planner_error"):
        print("  AI 계획 오류:", result["planner_error"])
    for row in result["score_result"].get("region_scores", [])[:5]:
        print(f"  {row['rank']}위 {row['region_name']}  {row['total_score']:.1f}점")
    _print_steps(result.get("agent_steps"))
    for sim in result.get("what_if_results") or []:
        order = " > ".join(r["region_name"] for r in sim["ranking"][:3])
        print(f"  (가정, 미적용) 가중치 {sim['weights_percent']} 이면: {order}")
    if result.get("review_error"):
        print("  결과 검토 오류:", result["review_error"])
    _print_final(result.get("final_answer"))


def main():
    parser = argparse.ArgumentParser(description="실제 AI로 Agent 기능 확인")
    parser.add_argument("--backend", choices=["claude_cli", "ollama"])
    parser.add_argument("--model")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("ping")
    p.add_argument("--timeout", type=int, default=90, help="단계별 최대 대기(초)")
    p.set_defaults(func=cmd_ping)
    for name, func in (("location", cmd_location), ("chat", cmd_chat)):
        p = sub.add_parser(name)
        if name == "location":
            p.add_argument("question")
        p.add_argument("--lat", type=float, default=DEFAULT_CENTER[0])
        p.add_argument("--lon", type=float, default=DEFAULT_CENTER[1])
        p.add_argument("--radius", type=int, default=500, choices=[300, 500, 1000])
        p.set_defaults(func=func)
    p = sub.add_parser("recommend")
    p.add_argument("--weights", nargs="*", help="예: bus_stop_count=60 convenience_store_count=40")
    p.set_defaults(func=cmd_recommend)

    args = parser.parse_args()
    if args.backend:
        os.environ["LLM_BACKEND"] = args.backend
    if args.model:
        os.environ["CLAUDE_CLI_MODEL"] = args.model
    args.func(args)


if __name__ == "__main__":
    main()
