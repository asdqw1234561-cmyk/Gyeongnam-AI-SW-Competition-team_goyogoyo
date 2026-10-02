"""
agent/claude_cli.py, agent/llm.py 오프라인 테스트 (실제 claude CLI를 호출하지 않음).

    python -m unittest tests.test_claude_cli -v

subprocess.run 을 가짜로 바꿔서 명령 구성, 출력 해석, 오류 처리, 백엔드 선택을 검증한다.
"""

import json
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import claude_cli as cc  # noqa: E402
from agent import llm  # noqa: E402


def _proc(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def _ok_json(result="답변", **extra):
    data = {"type": "result", "subtype": "success", "is_error": False, "result": result,
            "session_id": "sess-1", "total_cost_usd": 0.001}
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


class FakeRun:
    """subprocess.run 대체. 호출 기록과 시스템 프롬프트 파일 내용을 남긴다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.system_texts = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        if "--system-prompt-file" in cmd:
            path = cmd[cmd.index("--system-prompt-file") + 1]
            with open(path, encoding="utf-8") as f:
                self.system_texts.append(f.read())
        resp = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


class ClaudeCliTest(unittest.TestCase):
    def setUp(self):
        self.which = mock.patch.object(cc, "find_cli", return_value="/usr/bin/claude")
        self.which.start()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for key in ("CLAUDE_CLI_MODEL", "CLAUDE_CLI_TIMEOUT", "LLM_BACKEND"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.which.stop()
        self.env.stop()

    def _run(self, fake, *args, **kwargs):
        with mock.patch.object(cc.subprocess, "run", fake):
            return cc.run(*args, **kwargs)

    def test_success_and_safety_flags(self):
        fake = FakeRun(_proc(_ok_json("안녕하세요")))
        r = self._run(fake, "한글 질문입니다", "시스템 지시")
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "안녕하세요")
        self.assertEqual(r["session_id"], "sess-1")
        call = fake.calls[0]
        cmd = call["cmd"]
        self.assertEqual(call["input"], "한글 질문입니다")       # 본문은 표준입력으로
        self.assertNotIn("한글 질문입니다", " ".join(cmd))       # 명령줄에는 없음
        self.assertEqual(call["encoding"], "utf-8")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--disallowedTools") + 1], "mcp__*")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        for flag in ("-p", "--strict-mcp-config", "--safe-mode", "--no-session-persistence",
                     "--no-chrome"):
            self.assertIn(flag, cmd)
        self.assertNotIn("--bare", cmd)                          # 구독 로그인 사용
        self.assertTrue(fake.system_texts[0].startswith("시스템 지시"))
        self.assertIn("지어내지 말", fake.system_texts[0])      # 안내 문구 자동 추가
        self.assertTrue(os.path.basename(call["cwd"]).startswith("claude_cli_"))
        self.assertFalse(os.path.exists(call["cwd"]))            # 임시 폴더 정리됨

    def test_cli_missing(self):
        with mock.patch.object(cc, "find_cli", return_value=None):
            r = cc.run("q")
        self.assertFalse(r["ok"])
        self.assertIn("찾을 수 없습니다", r["error"])

    def test_timeout(self):
        r = self._run(FakeRun(subprocess.TimeoutExpired(cmd="claude", timeout=5)), "q", timeout=5)
        self.assertFalse(r["ok"])
        self.assertIn("5초", r["error"])

    def test_os_error(self):
        r = self._run(FakeRun(OSError("권한 없음")), "q")
        self.assertFalse(r["ok"])
        self.assertIn("실행 실패", r["error"])

    def test_not_logged_in_error_result(self):
        out = _ok_json("Invalid API key · Please run /login", is_error=True)
        r = self._run(FakeRun(_proc(out, code=1)), "q")
        self.assertFalse(r["ok"])
        self.assertIn("/login", r["error"])

    def test_max_turns_subtype(self):
        out = _ok_json(None, subtype="error_max_turns")
        r = self._run(FakeRun(_proc(out, code=1)), "q")
        self.assertFalse(r["ok"])
        self.assertIn("error_max_turns", r["error"])

    def test_non_json_output(self):
        r = self._run(FakeRun(_proc("Error: something", stderr="boom", code=1)), "q")
        self.assertFalse(r["ok"])
        self.assertIn("해석하지 못했습니다", r["error"])

    def test_unknown_option_retry_without_flag(self):
        fake = FakeRun(_proc("", stderr="error: unknown option '--safe-mode'", code=1),
                       _proc(_ok_json("ok")))
        r = self._run(fake, "q")
        self.assertTrue(r["ok"])
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("--safe-mode", fake.calls[0]["cmd"])
        self.assertNotIn("--safe-mode", fake.calls[1]["cmd"])
        self.assertIn("--no-session-persistence", fake.calls[1]["cmd"])

    def test_json_schema(self):
        schema = {"type": "object", "properties": {"questions": {"type": "array"}}}
        fake = FakeRun(_proc(_ok_json("", structured_output={"questions": ["Q1"]})))
        r = self._run(fake, "q", json_schema=schema)
        self.assertTrue(r["ok"])
        self.assertEqual(r["structured"], {"questions": ["Q1"]})
        cmd = fake.calls[0]["cmd"]
        self.assertEqual(json.loads(cmd[cmd.index("--json-schema") + 1]), schema)

    def test_json_schema_missing_structured(self):
        r = self._run(FakeRun(_proc(_ok_json("text only"))), "q", json_schema={"type": "object"})
        self.assertFalse(r["ok"])

    def test_model_from_env(self):
        os.environ["CLAUDE_CLI_MODEL"] = "haiku"
        fake = FakeRun(_proc(_ok_json()))
        self._run(fake, "q")
        cmd = fake.calls[0]["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")

    # ------------------------------------------------------------ chat (ollama 호환)
    def _chat(self, fake, **kwargs):
        with mock.patch.object(cc.subprocess, "run", fake):
            return cc.chat(**kwargs)

    def test_chat_ollama_compatible(self):
        fake = FakeRun(_proc(_ok_json('{"questions": ["Q"]}')))
        resp = self._chat(fake, model="qwen3.5:4b",
                          messages=[{"role": "system", "content": "규칙"},
                                    {"role": "user", "content": "입력"}],
                          options={"temperature": 0.0}, think=False)
        self.assertEqual(resp["message"]["content"], '{"questions": ["Q"]}')
        self.assertEqual(resp["backend"], "claude_cli")
        cmd = fake.calls[0]["cmd"]
        self.assertNotIn("--model", cmd)                 # qwen 모델명은 전달하지 않음
        self.assertEqual(fake.calls[0]["input"], "입력")
        self.assertTrue(fake.system_texts[0].startswith("규칙"))

    def test_chat_passes_claude_model(self):
        fake = FakeRun(_proc(_ok_json()))
        self._chat(fake, model="sonnet", messages=[{"role": "user", "content": "q"}])
        cmd = fake.calls[0]["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")

    def test_chat_multi_turn(self):
        fake = FakeRun(_proc(_ok_json()))
        self._chat(fake, messages=[{"role": "user", "content": "첫 질문"},
                                   {"role": "assistant", "content": "첫 답"},
                                   {"role": "user", "content": "두 번째"}])
        body = fake.calls[0]["input"]
        self.assertIn("[사용자]\n첫 질문", body)
        self.assertIn("[어시스턴트]\n첫 답", body)
        self.assertTrue(body.rstrip().endswith("두 번째"))

    def test_chat_raises_on_failure(self):
        with mock.patch.object(cc, "find_cli", return_value=None):
            with self.assertRaises(cc.ClaudeCLIError):
                cc.chat(messages=[{"role": "user", "content": "q"}])
        self.assertTrue(issubclass(cc.ClaudeCLIError, RuntimeError))

    def test_chat_format_schema(self):
        fake = FakeRun(_proc(_ok_json("", structured_output={"a": "가"})))
        resp = self._chat(fake, messages=[{"role": "user", "content": "q"}],
                          format={"type": "object"})
        self.assertEqual(json.loads(resp["message"]["content"]), {"a": "가"})

    def test_check_status(self):
        fake = FakeRun(_proc("2.1.287 (Claude Code)"),
                       _proc(json.dumps({"loggedIn": True, "authMethod": "claude.ai"})))
        with mock.patch.object(cc.subprocess, "run", fake):
            st = cc.check_status()
        self.assertTrue(st["installed"] and st["logged_in"])
        self.assertEqual(st["auth_method"], "claude.ai")
        with mock.patch.object(cc, "find_cli", return_value=None):
            self.assertFalse(cc.check_status()["installed"])


class BackendTest(unittest.TestCase):
    def test_default_is_ollama(self):
        fake_ollama = types.ModuleType("ollama")
        fake_ollama.chat = mock.Mock(return_value={"message": {"content": "from ollama"}})
        with mock.patch.dict(os.environ, {"LLM_BACKEND": ""}), \
             mock.patch.dict(sys.modules, {"ollama": fake_ollama}):
            self.assertEqual(llm.get_backend(), "ollama")
            out = llm.chat(model="qwen", messages=[])
        self.assertEqual(out["message"]["content"], "from ollama")

    def test_claude_cli_backend(self):
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "Claude_CLI"}), \
             mock.patch.object(cc, "chat", return_value={"message": {"content": "from cli"}}) as m:
            self.assertEqual(llm.get_backend(), "claude_cli")
            out = llm.chat(model="qwen", messages=[{"role": "user", "content": "q"}])
        self.assertEqual(out["message"]["content"], "from cli")
        m.assert_called_once()

    def test_unknown_backend_falls_back(self):
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "gpt"}):
            self.assertEqual(llm.get_backend(), "ollama")


if __name__ == "__main__":
    unittest.main()
