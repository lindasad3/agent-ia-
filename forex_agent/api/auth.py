"""Authentification de l'application : mot de passe haché (PBKDF2) + jeton JWT."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALG = "HS256"
bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, salt).split("$")[2], h)


class Auth:
    def __init__(self, user: str, password_hash: str, secret: str, ttl_hours: float = 12):
        self.user, self.password_hash, self.secret, self.ttl = user, password_hash, secret, ttl_hours * 3600
        self._failures: dict[str, list[float]] = {}

    def login(self, user: str, pw: str, ip: str = "-") -> str:
        now = time.time()
        recent = [t for t in self._failures.get(ip, []) if now - t < 900]
        if len(recent) >= 8:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives, réessayez dans 15 minutes")
        if not (hmac.compare_digest(user, self.user) and verify_password(pw, self.password_hash)):
            self._failures[ip] = recent + [now]
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
        self._failures.pop(ip, None)
        return jwt.encode({"sub": user, "exp": int(now + self.ttl), "iat": int(now)}, self.secret, algorithm=ALG)

    def dependency(self):
        def dep(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
            if cred is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise")
            try:
                payload = jwt.decode(cred.credentials, self.secret, algorithms=[ALG])
            except jwt.PyJWTError as e:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée ou invalide") from e
            return payload["sub"]
        return dep
