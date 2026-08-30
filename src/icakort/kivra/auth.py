"""Inloggning mot Kivra med BankID (OAuth2 authorization_code + PKCE).

Flödet är detsamma som webbklienten på inbox.kivra.com använder:

1. GET app.kivra.com för att etablera en session
2. POST /v2/oauth2/authorize -> QR-kod + poll-URL
3. Visa QR, polla tills BankID-signeringen är klar
4. POST /v2/oauth2/token -> access_token + id_token
5. actor_key läses ur id_token-JWT:ns ``kivra_user_id``

Kivra returnerar ingen refresh_token, så när access_token gått ut krävs en
ny BankID-signering.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .. import config


class AuthError(RuntimeError):
    """Inloggningen kunde inte slutföras."""


@dataclass
class Token:
    access_token: str
    actor_key: str
    expires_at: float

    @property
    def is_valid(self) -> bool:
        # Marginal så vi inte startar en lång synk på en token som dör om 10 s.
        return bool(self.access_token) and time.time() < self.expires_at - 60

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "actor_key": self.actor_key,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Token":
        return cls(
            access_token=data["access_token"],
            actor_key=data["actor_key"],
            expires_at=float(data.get("expires_at", 0)),
        )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
    except IndexError as exc:  # pragma: no cover - trasig token
        raise AuthError("id_token har inte JWT-format") from exc
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def render_qr(payload: str) -> str:
    """Rendera BankID-QR:en som ASCII för terminalen."""
    import io

    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def _default_show_qr(payload: str) -> None:
    # Rulla upp QR-koden i stället för att spamma terminalen: Kivras QR
    # roterar varje sekund och måste ritas om vid varje poll.
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("Skanna med BankID-appen (uppdateras automatiskt):\n\n")
    sys.stdout.write(render_qr(payload))
    sys.stdout.flush()


def load_token() -> Token | None:
    path = config.token_path()
    if not path.exists():
        return None
    try:
        return Token.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError):
        return None


def save_token(token: Token) -> None:
    path = config.token_path()
    path.write_text(json.dumps(token.to_dict(), indent=2))
    os.chmod(path, 0o600)


def authenticate(
    show_qr: Callable[[str], None] | None = None,
    timeout_seconds: int = 180,
) -> Token:
    """Kör BankID-flödet och returnera en giltig token."""
    show_qr = show_qr or _default_show_qr
    verifier, challenge = _pkce_pair()

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        client.get(config.KIVRA_APP_URL)

        response = client.post(
            f"{config.KIVRA_API_BASE}/v2/oauth2/authorize",
            json={
                "response_type": "bankid_all",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "openid profile",
                "client_id": config.KIVRA_CLIENT_ID,
                "redirect_uri": config.KIVRA_REDIRECT_URI,
            },
            headers={"Origin": "https://inbox.kivra.com"},
        )
        if response.status_code >= 400:
            raise AuthError(f"authorize misslyckades: {response.status_code} {response.text[:200]}")
        payload = response.json()

        poll_url = payload.get("next_poll_url")
        auth_code = payload.get("code")
        if not poll_url:
            raise AuthError("Kivra returnerade ingen poll-URL")
        if payload.get("qr_code"):
            show_qr(payload["qr_code"])

        deadline = time.time() + timeout_seconds
        while True:
            if time.time() > deadline:
                raise AuthError("Tiden gick ut i väntan på BankID-signering")
            time.sleep(1)
            poll = client.get(f"{config.KIVRA_API_BASE}{poll_url}")
            if poll.status_code >= 400:
                raise AuthError(f"polling misslyckades: {poll.status_code} {poll.text[:200]}")
            state = poll.json()
            status = state.get("status", "pending")
            if state.get("next_poll_url"):
                poll_url = state["next_poll_url"]
            if state.get("code"):
                auth_code = state["code"]
            if status == "pending":
                if state.get("qr_code"):
                    show_qr(state["qr_code"])
                continue
            if status in {"complete", "completed", "success"}:
                break
            raise AuthError(f"BankID avbröts: status={status}")

        if not auth_code:
            raise AuthError("Ingen authorization code returnerades")

        token_response = client.post(
            f"{config.KIVRA_API_BASE}/v2/oauth2/token",
            json={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": config.KIVRA_CLIENT_ID,
                "redirect_uri": config.KIVRA_REDIRECT_URI,
                "code_verifier": verifier,
            },
            headers={"Origin": "https://inbox.kivra.com"},
        )
        if token_response.status_code >= 400:
            raise AuthError(
                f"token-utbytet misslyckades: {token_response.status_code} "
                f"{token_response.text[:200]}"
            )
        data = token_response.json()

    access_token = data.get("access_token")
    id_token = data.get("id_token")
    if not access_token or not id_token:
        raise AuthError("Svaret saknade access_token eller id_token")

    claims = _decode_jwt_payload(id_token)
    actor_key = claims.get("kivra_user_id")
    if not actor_key:
        raise AuthError("Hittade inte kivra_user_id i id_token")

    token = Token(
        access_token=access_token,
        actor_key=actor_key,
        expires_at=time.time() + float(data.get("expires_in", 3600)),
    )
    save_token(token)
    return token


def get_token(interactive: bool = True) -> Token:
    """Återanvänd cachad token, logga annars in på nytt."""
    token = load_token()
    if token and token.is_valid:
        return token
    if not interactive:
        raise AuthError("Ingen giltig token. Kör `icakort auth` först.")
    return authenticate()
