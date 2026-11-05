"""Demo identities. A workspace comes with four personas so one visitor can play every role; production would
take identities from the company's IdP (OIDC) and map groups to the same three roles."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import jwt

from countersign.agent.runtime import Principal, Role
from countersign.settings import Settings

ISSUER = "countersign"
AUDIENCE = "countersign-demo"


@dataclass(frozen=True)
class Persona:
    key: str
    name: str
    role: Role
    description: str


PERSONAS: tuple[Persona, ...] = (
    Persona("viewer", "Riley", Role.VIEWER, "Reads dashboards, runs and the audit log"),
    Persona("operator", "Sam", Role.OPERATOR, "Asks the agent for work, answers its questions, cancels runs"),
    Persona("approver", "Alex", Role.APPROVER, "Countersigns change requests"),
    Persona("approver2", "Jordan", Role.APPROVER, "Second approver, required for high-risk change requests"),
)
BY_KEY = {p.key: p for p in PERSONAS}


class AuthError(PermissionError):
    pass


def issue(settings: Settings, workspace_id: str, persona: Persona, now: dt.datetime) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": f"{workspace_id}:{persona.key}",
        "ws": workspace_id,
        "persona": persona.key,
        "role": persona.role.value,
        "name": persona.name,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()) - 5,
        "exp": int((now + dt.timedelta(hours=settings.token_ttl_hours)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def principal(settings: Settings, token: str) -> Principal:
    """Fail closed: any missing claim, wrong audience, issuer, algorithm or signature is a 401."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        persona = BY_KEY[str(claims["persona"])]
        if claims["role"] != persona.role.value or claims["sub"] != f"{claims['ws']}:{persona.key}":
            raise AuthError("token claims are inconsistent")
    except (jwt.PyJWTError, KeyError, TypeError) as exc:
        raise AuthError(f"invalid token: {type(exc).__name__}") from exc
    return Principal(
        sub=str(claims["sub"]),
        workspace_id=str(claims["ws"]),
        persona=persona.key,
        role=persona.role,
        name=persona.name,
    )
