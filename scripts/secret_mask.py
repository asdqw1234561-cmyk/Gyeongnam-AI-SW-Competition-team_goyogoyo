# 수집 스크립트 공용: 오류·로그 문자열에서 API 키 가리기 (S-M1, S-L1)
"""
data.go.kr 수집 스크립트가 오류를 출력·저장할 때 serviceKey가 새지 않게 한다.

- requests 예외 문자열에는 serviceKey가 든 요청 URL이 그대로 들어간다. 예외 문자열은 쓰지 않고
  request_error_kind()로 종류(timeout / connection_error / ...)만 남긴다.
- 응답 본문 등 어쩔 수 없이 출력하는 문자열은 mask_secret()으로 원문 키와 URL 인코딩된 키
  (quote / quote_plus, 대·소문자 %xx)를 모두 가린다.
"""

from __future__ import annotations

from urllib.parse import quote, quote_plus

import requests

MASK = "****(masked)****"


def secret_variants(secret: str) -> list[str]:
    """원문과 URL 인코딩 형태들. 긴 것부터 바꿔야 부분 치환이 남지 않는다."""
    if not secret:
        return []
    variants = {secret, quote(secret, safe=""), quote(secret), quote_plus(secret)}
    variants |= {v.lower() for v in variants if "%" in v}
    return sorted(variants, key=len, reverse=True)


def mask_secret(text: str, *secrets: str) -> str:
    text = str(text)
    for secret in secrets:
        for variant in secret_variants(secret):
            text = text.replace(variant, MASK)
    return text


def request_error_kind(exc: BaseException) -> str:
    """requests 예외의 종류만. URL·메시지는 버린다."""
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    return f"request_error({type(exc).__name__})"
