"""Supabase JWT verification for the API.

Supports both signing schemes Supabase projects use: legacy HS256 (shared
JWT secret) and the newer asymmetric keys published at the project's JWKS
endpoint. AUTH_DISABLED=1 bypasses auth entirely for local development.
"""

import os
import time

import httpx
import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"

_jwks_client = None


def _jwks():
    global _jwks_client
    if _jwks_client is None:
        if not SUPABASE_URL:
            raise HTTPException(500, "SUPABASE_URL not configured")
        _jwks_client = PyJWKClient(
            f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True
        )
    return _jwks_client


def verify_token(authorization: str | None = Header(default=None)) -> dict:
    if AUTH_DISABLED:
        return {"sub": "00000000-0000-0000-0000-000000000000", "email": "dev@local"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        alg = jwt.get_unverified_header(token).get("alg", "HS256")
        if alg == "HS256":
            if not JWT_SECRET:
                raise HTTPException(500, "SUPABASE_JWT_SECRET not configured")
            return jwt.decode(
                token, JWT_SECRET, algorithms=["HS256"], audience="authenticated"
            )
        key = _jwks().get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=[alg], audience="authenticated")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, f"Invalid token: {exc}")


# profiles.role lookups go through PostgREST with the service key; cached so
# repeated admin calls don't hit the database every time.
_role_cache: dict[str, tuple[str, float]] = {}
_ROLE_TTL_S = 60


def _fetch_role(user_id: str) -> str:
    cached = _role_cache.get(user_id)
    if cached and time.time() - cached[1] < _ROLE_TTL_S:
        return cached[0]
    if not SUPABASE_URL or not SERVICE_ROLE_KEY:
        raise HTTPException(500, "Supabase admin credentials not configured")
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        params={"id": f"eq.{user_id}", "select": "role"},
        headers={
            "apikey": SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(502, "Could not look up user role")
    rows = resp.json()
    role = rows[0]["role"] if rows else "member"
    _role_cache[user_id] = (role, time.time())
    return role


def require_admin(claims: dict = Depends(verify_token)) -> dict:
    if AUTH_DISABLED:
        return claims
    if _fetch_role(claims["sub"]) != "admin":
        raise HTTPException(403, "Admin only")
    return claims
