"""
S-M1·S-L1: 수집 스크립트가 오류를 출력·저장할 때 serviceKey가 새지 않는지 확인한다.

실제 키는 쓰지 않는다. FAKE_KEY는 테스트 전용 가짜 값이며 URL 인코딩 시 모양이 바뀌도록
'+', '/', '=' 를 넣었다. 네트워크는 쓰지 않는다(requests.get 모킹).
"""

import argparse
import contextlib
import csv
import io
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote, quote_plus

import requests

from scripts import collect_convenience_stores as conv
from scripts import ingest_hira_hospital_data as hira
from scripts import secret_mask

FAKE_KEY = "FAKEtestKEY+abc/def=="
LEAK_MARKERS = (FAKE_KEY, quote(FAKE_KEY, safe=""), quote_plus(FAKE_KEY), quote_plus(FAKE_KEY).lower(), "FAKEtestKEY")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _url_with_key(encoded: str) -> str:
    return f"/B553077/api/open/sdsc2/storeListInDong?serviceKey={encoded}&type=json&pageNo=1"


def _network_errors() -> list[Exception]:
    """requests가 실제로 만드는 메시지처럼 요청 URL(키 포함)이 들어간 예외들."""
    return [
        requests.ConnectTimeout(f"HTTPSConnectionPool(host='apis.data.go.kr', port=443): Max retries "
                                f"exceeded with url: {_url_with_key(quote_plus(FAKE_KEY))} (timeout)"),
        requests.ConnectionError(f"Max retries exceeded with url: {_url_with_key(quote(FAKE_KEY, safe=''))}"),
        requests.ReadTimeout(f"Read timed out. url: {_url_with_key(FAKE_KEY)}"),
    ]


class LeakAssertions(unittest.TestCase):
    def assertNoLeak(self, text: str) -> None:
        for marker in LEAK_MARKERS:
            self.assertNotIn(marker, text)


class SecretMaskTest(LeakAssertions):
    def test_mask_covers_raw_and_url_encoded_forms(self):
        text = " | ".join([FAKE_KEY, quote(FAKE_KEY, safe=""), quote_plus(FAKE_KEY),
                           quote_plus(FAKE_KEY).lower(), quote(FAKE_KEY)])
        masked = secret_mask.mask_secret(text, FAKE_KEY)
        self.assertNoLeak(masked)
        self.assertEqual(masked.count(secret_mask.MASK), 5)

    def test_request_error_kind_drops_url(self):
        kinds = [secret_mask.request_error_kind(e) for e in _network_errors()]
        self.assertEqual(kinds, ["timeout", "connection_error", "timeout"])
        self.assertNoLeak(" ".join(kinds))


class ConvenienceCollectorTest(LeakAssertions):
    def test_network_error_not_in_console_or_partial_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            stores_csv, counts_csv = os.path.join(tmp, "stores.csv"), os.path.join(tmp, "counts.csv")
            out, err = io.StringIO(), io.StringIO()
            errors = iter(_network_errors() * 10)
            with mock.patch.dict(os.environ, {conv.SERVICE_KEY_ENV: FAKE_KEY}), \
                    mock.patch.object(conv.requests, "get", side_effect=lambda *a, **k: (_ for _ in ()).throw(next(errors))), \
                    mock.patch.object(conv.time, "sleep"), \
                    mock.patch.object(conv, "STORES_CSV", stores_csv), \
                    mock.patch.object(conv, "COUNTS_CSV", counts_csv), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                conv.main(["collect", "--save", "--allow-partial", "--retries", "1"])
            console = out.getvalue() + err.getvalue()
            self.assertIn("미확보", console)
            self.assertIn("timeout", console)
            self.assertNoLeak(console)
            with open(counts_csv, encoding="utf-8-sig") as f:
                saved = f.read()
            self.assertIn("수집 불완전", saved)
            self.assertNoLeak(saved)
            with open(counts_csv, encoding="utf-8-sig") as f:
                self.assertTrue(all(row["data_status"] == "미확보" for row in csv.DictReader(f)
                                    if row.get("facility_type", "편의점") == "편의점"))

    def test_api_error_exception_has_no_key_or_chained_url(self):
        with mock.patch.object(conv.requests, "get", side_effect=_network_errors()[0]):
            with self.assertRaises(conv.ApiError) as ctx:
                conv._call("storeListInDong", {"pageNo": 1}, FAKE_KEY)
        self.assertNoLeak(str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_error_body_echoing_key_is_masked(self):
        resp = mock.Mock(status_code=500, text=f"error for serviceKey={quote_plus(FAKE_KEY)}")
        with mock.patch.object(conv.requests, "get", return_value=resp):
            with self.assertRaises(conv.ApiError) as ctx:
                conv._call("storeListInDong", {"pageNo": 1}, FAKE_KEY)
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertNoLeak(str(ctx.exception))

    def test_inspect_command_exit_message_has_no_key(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {conv.SERVICE_KEY_ENV: FAKE_KEY}), \
                mock.patch.object(conv.requests, "get", side_effect=_network_errors()[1]), \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                conv.main(["inspect"])
        self.assertIn("connection_error", str(ctx.exception.code))
        self.assertNoLeak(str(ctx.exception.code) + err.getvalue())


class HiraCollectorTest(LeakAssertions):
    def test_inspect_network_error_has_no_raw_or_encoded_key(self):
        out = io.StringIO()
        args = argparse.Namespace(endpoint="https://example.invalid/getHospBasisList")
        with mock.patch.dict(os.environ, {hira.SERVICE_KEY_ENV: FAKE_KEY}), \
                mock.patch.object(hira.requests, "get", side_effect=_network_errors()[0]), \
                contextlib.redirect_stdout(out):
            hira.cmd_inspect(args)
        self.assertIn("timeout", out.getvalue())
        self.assertNoLeak(out.getvalue())

    def test_inspect_unknown_body_with_encoded_key_is_masked(self):
        out = io.StringIO()
        args = argparse.Namespace(endpoint="https://example.invalid/getHospBasisList")
        body = f"<html>bad request {quote_plus(FAKE_KEY)} {quote(FAKE_KEY, safe='')} {FAKE_KEY}</html>"
        resp = mock.Mock(status_code=400, text=body, headers={"Content-Type": "text/html"})
        with mock.patch.dict(os.environ, {hira.SERVICE_KEY_ENV: FAKE_KEY}), \
                mock.patch.object(hira.requests, "get", return_value=resp), \
                contextlib.redirect_stdout(out):
            hira.cmd_inspect(args)
        self.assertIn(secret_mask.MASK, out.getvalue())
        self.assertNoLeak(out.getvalue())

    def test_ingest_network_error_exits_without_key(self):
        with mock.patch.object(hira.requests, "get", side_effect=_network_errors()[1]):
            with self.assertRaises(SystemExit) as ctx:
                list(hira._iter_all_items("https://example.invalid/x", FAKE_KEY, 10, 1))
        self.assertIn("connection_error", str(ctx.exception.code))
        self.assertNoLeak(str(ctx.exception.code))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


class GitignoreTest(unittest.TestCase):
    """저장소 .gitignore만으로(개인 전역 ignore 설정 없이) 로컬 Secret·개인 설정 파일이 제외되는지."""

    def _ignored(self, path: str) -> bool:
        result = subprocess.run(
            ["git", "-c", f"core.excludesFile={os.devnull}", "check-ignore", "--no-index", "-q", path],
            cwd=PROJECT_ROOT, capture_output=True,
        )
        return result.returncode == 0

    def test_local_secret_files_ignored_and_examples_kept(self):
        for path in (".env", ".env.local", ".env.production", "CLAUDE.local.md", ".claude/settings.local.json",
                     ".streamlit/secrets.toml"):
            self.assertTrue(self._ignored(path), path)
        for path in (".env.example", ".claude/settings.json", "app.py", "CLAUDE.md"):
            self.assertFalse(self._ignored(path), path)


if __name__ == "__main__":
    unittest.main()
