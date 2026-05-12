import os
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from guardian.guardian_http_gateway import require_bearer_token_from_env
from guardian.http_auth import BearerAuthMiddleware


async def protected(_):
    return JSONResponse({"ok": True})


async def health(_):
    return JSONResponse({"ok": True, "version": "1.0", "backends": 0})


def make_app(token="secret"):
    app = Starlette(routes=[Route("/protected", protected, methods=["GET"]), Route("/health", health, methods=["GET"])])
    app.add_middleware(BearerAuthMiddleware, token=token, health_paths=("/health",))
    return app


class BearerAuthMiddlewareTests(unittest.TestCase):
    def test_valid_token_returns_200(self):
        with TestClient(make_app()) as client:
            response = client.get("/protected", headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)

    def test_invalid_token_returns_401(self):
        with TestClient(make_app()) as client:
            response = client.get("/protected", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], "UNAUTHORIZED")

    def test_missing_token_returns_401(self):
        with TestClient(make_app()) as client:
            response = client.get("/protected")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["ok"], False)

    def test_empty_configured_token_rejected(self):
        async def dummy(scope, receive, send):
            return None
        with self.assertRaises(ValueError):
            BearerAuthMiddleware(dummy, token="")

    def test_empty_env_token_refuses_startup(self):
        with patch.dict(os.environ, {"MCPGUARDIAN_BEARER_TOKEN": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                require_bearer_token_from_env()

    def test_health_without_auth_returns_200(self):
        with TestClient(make_app()) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "version": "1.0", "backends": 0})


if __name__ == "__main__":
    unittest.main()
