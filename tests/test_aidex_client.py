from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.services.aidex import (
    AidexAuthError,
    AidexClient,
    AidexConfig,
    AidexRateLimitError,
    aidex_cron_user_id,
    load_aidex_environment,
)
from hermes_cgm_agent.services.aidex.client import HTTPResult


class AidexConfigTests(unittest.TestCase):
    def test_defaults_to_official_sandbox_and_requires_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AIDEX_CLIENT_ID"):
                AidexConfig.from_env()
        with patch.dict(
            os.environ,
            {"AIDEX_CLIENT_ID": "client", "AIDEX_CLIENT_SECRET": "secret"},
            clear=True,
        ):
            config = AidexConfig.from_env()
        self.assertEqual(
            config.base_url, "https://sandbox-accesslist-x.microtechmd.com"
        )
        self.assertEqual(config.source_label, "aidex:sandbox")

    def test_loads_only_aidex_settings_from_hermes_env_without_overriding_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text(
                'AIDEX_CLIENT_ID="persisted-id"\n'
                'AIDEX_CLIENT_SECRET="value#with-specials"\n'
                "AIDEX_USE_SANDBOX=false\n"
                "OPENAI_API_KEY=must-not-load\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AIDEX_CLIENT_ID": "process-id"},
                clear=True,
            ):
                loaded = load_aidex_environment(hermes_home=home)
                self.assertEqual(loaded, home / ".env")
                self.assertEqual(os.environ["AIDEX_CLIENT_ID"], "process-id")
                self.assertEqual(
                    os.environ["AIDEX_CLIENT_SECRET"], "value#with-specials"
                )
                self.assertEqual(os.environ["AIDEX_USE_SANDBOX"], "false")
                self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_cron_requires_explicit_single_user_identity(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "CGM_AGENT_USER_ID"):
                aidex_cron_user_id()
        with patch.dict(os.environ, {"CGM_AGENT_USER_ID": "real-user"}, clear=True):
            self.assertEqual(aidex_cron_user_id(), "real-user")


class AidexClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AidexConfig(client_id="client", client_secret="secret")
        self.requests = []

    def _transport(self, request, timeout):
        self.requests.append(request)
        if request.full_url.endswith("/v1/oauth2/token"):
            return HTTPResult(
                200,
                json.dumps(
                    {
                        "accessToken": "access",
                        "refreshToken": "refresh",
                        "expiresIn": 86400,
                    }
                ).encode(),
            )
        return HTTPResult(200, json.dumps({"code": 1, "msg": "success", "data": []}).encode())

    def test_oauth_and_sensor_glucose_follow_official_contract(self) -> None:
        client = AidexClient(self.config, transport=self._transport)
        url = client.build_authorize_url(state="state-1")
        self.assertIn("/v1/oauth2/authorize?", url)
        self.assertIn("clientId=client", url)
        self.assertIn("responseType=code", url)
        self.assertIn("state=state-1", url)

        token = client.exchange_code("code-1")
        self.assertEqual(token.access_token, "access")
        body = self.requests[-1].data.decode()
        self.assertIn("clientSecret=secret", body)
        self.assertIn("grantType=authorization_code", body)

        client.get_sensor_glucose(
            "raw-access-token",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        request = self.requests[-1]
        self.assertIn("/v1/user/glu/sensor-glucose?", request.full_url)
        self.assertEqual(request.headers["Authorization"], "raw-access-token")
        self.assertNotIn("Bearer", request.headers["Authorization"])

    def test_official_error_codes_are_typed(self) -> None:
        auth_client = AidexClient(
            self.config,
            transport=lambda *_: HTTPResult(
                200, json.dumps({"code": 10003, "msg": "invalid token"}).encode()
            ),
        )
        with self.assertRaises(AidexAuthError):
            auth_client.get_data_range("bad")

        limited = AidexClient(
            self.config,
            transport=lambda *_: HTTPResult(
                200, json.dumps({"code": 10010, "msg": "too many"}).encode()
            ),
        )
        with self.assertRaises(AidexRateLimitError):
            limited.get_data_range("token")
