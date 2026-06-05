from __future__ import annotations

import json
import unittest
import urllib.parse
from datetime import datetime, timezone

from hermes_cgm_agent.services.dexcom import (
    DexcomAPIError,
    DexcomAuthError,
    DexcomClient,
    DexcomConfig,
    DexcomRateLimitError,
    RateLimiter,
)
from hermes_cgm_agent.services.dexcom.client import HTTPResult


def _config(**overrides) -> DexcomConfig:
    base = dict(
        client_id="cid",
        client_secret="csecret",
        redirect_uri="https://example.test/cb",
        use_sandbox=True,
    )
    base.update(overrides)
    return DexcomConfig(**base)


class FakeTransport:
    def __init__(self, responder) -> None:
        self.requests: list = []
        self._responder = responder

    def __call__(self, request, timeout) -> HTTPResult:
        self.requests.append(request)
        return self._responder(request)


def _json_result(status: int, payload: dict) -> HTTPResult:
    return HTTPResult(status=status, body=json.dumps(payload).encode("utf-8"))


class DexcomClientOAuthTests(unittest.TestCase):
    def test_build_authorize_url_contains_required_params(self) -> None:
        client = DexcomClient(_config(), transport=FakeTransport(lambda r: _json_result(200, {})))
        url = client.build_authorize_url(state="xyz")
        self.assertIn("https://sandbox-api.dexcom.com/v3/oauth2/login?", url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["client_id"], ["cid"])
        self.assertEqual(query["redirect_uri"], ["https://example.test/cb"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["offline_access"])
        self.assertEqual(query["state"], ["xyz"])

    def test_exchange_code_posts_form_and_returns_token(self) -> None:
        transport = FakeTransport(
            lambda r: _json_result(
                200,
                {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_in": 7200,
                    "token_type": "Bearer",
                },
            )
        )
        client = DexcomClient(_config(), transport=transport)
        token = client.exchange_code("the-code")

        self.assertEqual(token.access_token, "at")
        self.assertEqual(token.refresh_token, "rt")
        self.assertEqual(token.expires_in, 7200)
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url, "https://sandbox-api.dexcom.com/v3/oauth2/token")
        body = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(body["grant_type"], ["authorization_code"])
        self.assertEqual(body["code"], ["the-code"])
        self.assertEqual(body["client_id"], ["cid"])
        self.assertEqual(body["client_secret"], ["csecret"])
        self.assertEqual(body["redirect_uri"], ["https://example.test/cb"])

    def test_refresh_token_uses_refresh_grant(self) -> None:
        transport = FakeTransport(
            lambda r: _json_result(
                200, {"access_token": "at2", "refresh_token": "rt2", "expires_in": 7200}
            )
        )
        client = DexcomClient(_config(), transport=transport)
        token = client.refresh_token("old-refresh")

        self.assertEqual(token.access_token, "at2")
        body = urllib.parse.parse_qs(transport.requests[0].data.decode("utf-8"))
        self.assertEqual(body["grant_type"], ["refresh_token"])
        self.assertEqual(body["refresh_token"], ["old-refresh"])

    def test_invalid_grant_raises_auth_error_with_code(self) -> None:
        transport = FakeTransport(lambda r: _json_result(400, {"error": "invalid_grant"}))
        client = DexcomClient(_config(), transport=transport)
        with self.assertRaises(DexcomAuthError) as ctx:
            client.exchange_code("stale")
        self.assertEqual(ctx.exception.oauth_error, "invalid_grant")


class DexcomClientDataTests(unittest.TestCase):
    def test_get_egvs_builds_request_with_dates_and_bearer(self) -> None:
        captured = {}

        def responder(request):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            return _json_result(200, {"records": []})

        client = DexcomClient(_config(), transport=FakeTransport(responder))
        client.get_egvs(
            "tok",
            start=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
        )

        self.assertIn("/v3/users/self/egvs?", captured["url"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)
        self.assertEqual(query["startDate"], ["2026-05-01T00:00:00"])
        self.assertEqual(query["endDate"], ["2026-05-08T00:00:00"])
        self.assertEqual(captured["auth"], "Bearer tok")

    def test_401_raises_auth_error(self) -> None:
        client = DexcomClient(_config(), transport=FakeTransport(lambda r: HTTPResult(401, b"nope")))
        with self.assertRaises(DexcomAuthError):
            client.get_data_range("tok")

    def test_429_raises_rate_limit_error_with_retry_after(self) -> None:
        client = DexcomClient(
            _config(),
            transport=FakeTransport(lambda r: HTTPResult(429, b"slow down", {"retry-after": "12"})),
        )
        with self.assertRaises(DexcomRateLimitError) as ctx:
            client.get_data_range("tok")
        self.assertEqual(ctx.exception.retry_after, 12.0)

    def test_500_raises_api_error(self) -> None:
        client = DexcomClient(_config(), transport=FakeTransport(lambda r: HTTPResult(500, b"boom")))
        with self.assertRaises(DexcomAPIError) as ctx:
            client.get_data_range("tok")
        self.assertEqual(ctx.exception.status_code, 500)


class RateLimiterTests(unittest.TestCase):
    def test_sleeps_when_window_is_full(self) -> None:
        clock = {"t": 0.0}
        slept: list[float] = []

        limiter = RateLimiter(
            2,
            monotonic=lambda: clock["t"],
            sleep=lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)),
        )

        limiter.acquire()  # t=0, 1 call
        limiter.acquire()  # t=0, 2 calls -> window full
        limiter.acquire()  # 3rd within 60s -> must sleep ~60s

        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 60.0, places=3)

    def test_does_not_sleep_when_calls_age_out(self) -> None:
        clock = {"t": 0.0}
        slept: list[float] = []
        limiter = RateLimiter(
            2,
            monotonic=lambda: clock["t"],
            sleep=lambda s: slept.append(s),
        )
        limiter.acquire()
        limiter.acquire()
        clock["t"] = 61.0  # both prior calls fall out of the 60s window
        limiter.acquire()
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
