"""Probes the platform's own enforcement, not the SDK's.

Everything else in this suite mocks the gateway, which means it verifies that the SDK behaves
correctly *given* a well-behaved platform. These tests check the other half: that the platform
actually enforces what its guide says it enforces. A gap here is a finding for the platform team.

Skipped unless ``AXONIUM_INTEGRATION=1`` and credentials are set, and never part of default CI:
they hit a real deployment and cost real inference.

    AXONIUM_INTEGRATION=1 uv run --env-file .env pytest tests/integration -v

These probes are deliberately read-mostly and low-volume. They do not attempt to exhaust rate
limits, exhaust spend caps, or otherwise degrade a running deployment.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from axonium import Axonium
from axonium.errors import APIError, ForbiddenError, UnknownModelError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AXONIUM_INTEGRATION") != "1",
        reason="set AXONIUM_INTEGRATION=1 and supply credentials to run platform probes",
    ),
]


@pytest.fixture(scope="module")
def client() -> Axonium:
    with Axonium() as instance:
        yield instance


@pytest.fixture(scope="module")
def gateway() -> str:
    return os.environ["AXONIUM_GATEWAY_BASE_URL"].rstrip("/")


@pytest.fixture(scope="module")
def token(client: Axonium) -> str:
    client.models.mine()
    cached = client._auth.cached_token
    assert cached is not None
    return cached.access_token


@pytest.fixture(scope="module")
def a_model(client: Axonium) -> str:
    for model in client.models.mine():
        if model.modality == "text":
            return model.id
    pytest.skip("this token has no text model to probe with")


def raw() -> httpx.Client:
    """A plain client, so a probe can send something the SDK would refuse to construct."""
    return httpx.Client(timeout=30.0, follow_redirects=False)


def problem_type(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    kind = body.get("type") if isinstance(body, dict) else None
    return kind.rstrip("/").rsplit("/", 1)[-1] if isinstance(kind, str) else None


class TestAuthenticationEnforcement:
    def test_an_unauthenticated_inference_call_is_rejected(self, gateway: str) -> None:
        with raw() as http:
            response = http.post(
                f"{gateway}/v1/chat/completions",
                json={"model": "any", "messages": [{"role": "user", "content": "hi"}]},
            )

        assert response.status_code == 401
        assert problem_type(response) == "missing-credentials"

    def test_a_token_in_the_query_string_is_not_accepted(self, gateway: str, token: str) -> None:
        # The guide is explicit that this is rejected to keep credentials out of server, proxy and
        # browser logs. If it were accepted, the SDK's header-only rule would be the only thing
        # standing between a caller and a leaked token.
        with raw() as http:
            response = http.get(f"{gateway}/v1/models/mine", params={"token": token})

        assert response.status_code == 401
        assert problem_type(response) == "missing-credentials"

    @pytest.mark.parametrize(
        "header",
        # A trailing space is deliberately absent: httpx refuses to put one on the wire
        # ("Illegal header value"), so no conforming client can produce that case.
        ["", "Bearer", "Basic dXNlcjpwYXNz", "bearer-no-space", "Bearer a b c", "Bearer null"],
    )
    def test_a_malformed_authorization_header_is_rejected(self, gateway: str, header: str) -> None:
        with raw() as http:
            response = http.get(f"{gateway}/v1/models/mine", headers={"Authorization": header})

        assert response.status_code == 401

    def test_a_tampered_signature_is_rejected(self, gateway: str, token: str) -> None:
        # The payload is unchanged; only the signature is corrupted. Accepting this would mean
        # signatures are not actually verified.
        head, payload, _ = token.split(".")
        forged = f"{head}.{payload}.{'A' * 40}"

        with raw() as http:
            response = http.get(
                f"{gateway}/v1/models/mine", headers={"Authorization": f"Bearer {forged}"}
            )

        assert response.status_code == 401
        assert problem_type(response) in {"invalid-token", "token-expired"}

    def test_an_alg_none_token_is_rejected(self, gateway: str, token: str) -> None:
        # The classic JWT bypass: swap the algorithm to "none" and drop the signature.
        import base64
        import json

        def segment(data: dict[str, object]) -> str:
            return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

        payload = json.loads(
            base64.urlsafe_b64decode(token.split(".")[1] + "==").decode(errors="replace")
        )
        forged = f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(payload)}."

        with raw() as http:
            response = http.get(
                f"{gateway}/v1/models/mine", headers={"Authorization": f"Bearer {forged}"}
            )

        assert response.status_code == 401

    def test_a_random_token_is_rejected(self, gateway: str) -> None:
        with raw() as http:
            response = http.get(
                f"{gateway}/v1/models/mine",
                headers={"Authorization": f"Bearer {uuid.uuid4()}"},
            )

        assert response.status_code == 401


class TestScopeEnforcement:
    def test_a_model_outside_the_tokens_grants_is_refused(self, client: Axonium) -> None:
        catalog = set(client.models.list().ids)
        granted = set(client.models.mine().ids)
        ungranted = catalog - granted
        if not ungranted:
            pytest.skip("this token can reach every deployed model")

        with pytest.raises(ForbiddenError):
            client.chat.completions.create(
                model=next(iter(ungranted)),
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )

    def test_an_unregistered_model_is_a_400_not_a_403(self, client: Axonium) -> None:
        # The guide states the model check runs before the scope check, so an unknown model must
        # not be reported as an authorization failure — that ordering keeps a 403 meaningful.
        with pytest.raises(UnknownModelError):
            client.chat.completions.create(
                model=f"definitely-not-a-model-{uuid.uuid4()}",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )

    def test_the_public_catalog_needs_no_token(self, gateway: str) -> None:
        with raw() as http:
            response = http.get(f"{gateway}/v1/models")

        assert response.status_code == 200
        assert "data" in response.json()

    def test_mine_requires_a_token(self, gateway: str) -> None:
        with raw() as http:
            response = http.get(f"{gateway}/v1/models/mine")

        assert response.status_code == 401


class TestInputHardening:
    def test_a_remote_image_url_is_refused_server_side(
        self, gateway: str, token: str, a_model: str
    ) -> None:
        # The SDK blocks this client-side, so this probe goes around it: the SSRF mitigation must
        # live in the platform, not only in one of its clients.
        with raw() as http:
            response = http.post(
                f"{gateway}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": a_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "http://169.254.169.254/latest/meta-data/"
                                    },
                                }
                            ],
                        }
                    ],
                    "max_tokens": 1,
                },
            )

        assert response.status_code >= 400, (
            "a link to the cloud metadata endpoint must not be fetched by the gateway"
        )

    @pytest.mark.parametrize(
        "body",
        [
            {"messages": [{"role": "user", "content": "hi"}]},
            {"model": "m"},
            {"model": "m", "messages": []},
            {"model": "m", "messages": "not-a-list"},
            {"model": "m", "messages": [{"role": "wizard", "content": "hi"}]},
            {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 99},
        ],
        ids=[
            "no-model",
            "no-messages",
            "empty-messages",
            "messages-wrong-type",
            "bad-role",
            "temperature-out-of-range",
        ],
    )
    def test_invalid_request_bodies_are_rejected(
        self, gateway: str, token: str, body: dict[str, object]
    ) -> None:
        with raw() as http:
            response = http.post(
                f"{gateway}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )

        assert 400 <= response.status_code < 500

    def test_a_non_json_body_is_rejected(self, gateway: str, token: str) -> None:
        with raw() as http:
            response = http.post(
                f"{gateway}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                content=b"this is not json",
            )

        assert 400 <= response.status_code < 500


class TestErrorHygiene:
    def test_errors_do_not_leak_internals(self, client: Axonium) -> None:
        # A stack trace, file path or dependency version in an error body tells an attacker about
        # the deployment and is the sort of thing that regresses silently.
        try:
            client.chat.completions.create(
                model=f"nope-{uuid.uuid4()}",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
        except APIError as exc:
            rendered = f"{exc} {exc.raw}"
        else:
            pytest.fail("expected the request to be refused")

        lowered = rendered.lower()
        for marker in ("traceback", "site-packages", "/usr/", "/app/", '.py", line', "sqlalchemy"):
            assert marker not in lowered, f"error body leaked {marker!r}"

    def test_every_response_carries_a_request_id(self, client: Axonium) -> None:
        # Without it, correlating a client-visible failure with a platform log is guesswork.
        result = client.models.mine()

        assert result.meta is not None
        assert result.meta.request_id

    def test_an_error_also_carries_a_request_id(self, client: Axonium) -> None:
        with pytest.raises(APIError) as caught:
            client.chat.completions.create(
                model=f"nope-{uuid.uuid4()}",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )

        assert caught.value.request_id


class TestTransportHygiene:
    def test_rate_limit_headers_are_present_before_any_429(self, client: Axonium) -> None:
        client.models.mine()

        assert client.last_rate_limit is not None, (
            "without these a client can only react to a 429, never avoid one"
        )

    def test_the_gateway_does_not_echo_a_forged_trace_id_in_otel_mode(
        self, gateway: str, token: str
    ) -> None:
        # Adoption is deployment-dependent: OTEL mode must ignore a client-supplied value, legacy
        # mode adopts a valid UUID4. Either is acceptable; what matters is that a non-UUID value is
        # never echoed back, since that would let a client forge trace context.
        with raw() as http:
            response = http.get(
                f"{gateway}/v1/models/mine",
                headers={"Authorization": f"Bearer {token}", "X-Trace-ID": "../../etc/passwd"},
            )

        assert response.headers.get("X-Trace-ID") != "../../etc/passwd"
