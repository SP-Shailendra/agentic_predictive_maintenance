import asyncio
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)
KNOWN_ROLES = frozenset({"operator", "reliability_engineer", "approver", "manager", "technician"})
WORKFLOW_READ_ROLES = ("operator", "reliability_engineer", "approver", "manager", "technician")


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    roles: frozenset[str]
    verified: bool
    auth_method: str
    plant_ids: frozenset[uuid.UUID] | None = None
    groups: frozenset[str] | None = None


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=300)


def _claim(claims: dict[str, object], path: str) -> object:
    value: object = claims
    for segment in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _role_set(value: object) -> frozenset[str]:
    if isinstance(value, str):
        roles = {role.strip().lower() for role in value.replace(",", " ").split()}
    elif isinstance(value, list):
        roles = {role.strip().lower() for role in value if isinstance(role, str) and role.strip()}
    else:
        roles = set()
    return frozenset(roles & KNOWN_ROLES)


def _plant_id_set(value: object) -> frozenset[uuid.UUID]:
    if not isinstance(value, list):
        return frozenset()
    try:
        return frozenset(uuid.UUID(item) for item in value if isinstance(item, str))
    except ValueError:
        return frozenset()


def _group_set(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(group.strip() for group in value.replace(",", " ").split() if group.strip())
    if isinstance(value, list):
        return frozenset(group.strip() for group in value if isinstance(group, str) and group.strip())
    return frozenset()


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if credentials is not None and settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url:
        try:
            key = await asyncio.to_thread(
                _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt,
                credentials.credentials,
            )
            claims = jwt.decode(
                credentials.credentials,
                key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                options={"require": ["exp", "sub"]},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token is invalid or expired.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        subject = claims.get("sub")
        roles = _role_set(_claim(claims, settings.oidc_roles_claim))
        plant_ids = _plant_id_set(_claim(claims, settings.oidc_plants_claim))
        groups = _group_set(_claim(claims, settings.oidc_groups_claim))
        if not isinstance(subject, str) or not subject:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token has no subject.")
        if not roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="A recognized Fieldnote role is required.")
        return Principal(
            subject=subject,
            roles=roles,
            verified=True,
            auth_method="oidc",
            plant_ids=plant_ids,
            groups=groups,
        )

    if settings.api_key:
        if not secrets.compare_digest(x_api_key or "", settings.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid X-API-Key header or OIDC bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Principal(subject="api-key-client", roles=frozenset({"service"}), verified=False, auth_method="api_key")

    if settings.environment.lower() in {"production", "prod"}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC authentication is not configured.")
    return Principal(
        subject="local-development",
        roles=KNOWN_ROLES,
        verified=False,
        auth_method="development",
    )


def require_roles(*roles: str, allow_service: bool = False):
    required_roles = frozenset(role.lower() for role in roles)

    async def dependency(principal: Annotated[Principal, Depends(get_current_principal)]) -> Principal:
        if principal.auth_method == "development":
            return principal
        if principal.auth_method == "api_key" and allow_service:
            return principal
        if not principal.roles.intersection(required_roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your role cannot perform this action.")
        return principal

    return dependency


def reject_self_approval(created_by_sub: str | None, principal: Principal) -> None:
    if principal.verified and created_by_sub == principal.subject:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The person who raised this investigation cannot approve it.",
        )


def plant_scope_filter(principal: Principal, column, requested_plant_id: uuid.UUID | None = None):
    if principal.plant_ids is None:
        return column == requested_plant_id if requested_plant_id is not None else None
    if not principal.plant_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No plant entitlements are assigned.")
    if requested_plant_id is not None:
        if requested_plant_id not in principal.plant_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not entitled to this plant.")
        return column == requested_plant_id
    return column.in_(principal.plant_ids)


def require_plant_assignment(principal: Principal, plant_id: uuid.UUID | None) -> None:
    if principal.plant_ids is None:
        return
    if plant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="A plant assignment is required.")
    if plant_id not in principal.plant_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not entitled to this plant.")


async def require_api_key(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    return principal
