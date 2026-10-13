"""Username + PIN accounts. PINs are salted and hashed; sessions are random bearer tokens."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import defaultdict

from fastapi import Header, HTTPException

from . import config, game
from .db import connect, one, utcnow

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PIN_RE = re.compile(r"^\d{4,8}$")
_failures: dict[str, list[float]] = defaultdict(list)  # username -> recent failed login times


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def register(username: str, pin: str) -> dict:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username must be 3-20 letters, numbers or underscores.")
    if not PIN_RE.match(pin or ""):
        raise HTTPException(400, "PIN must be 4-8 digits.")
    salt = secrets.token_hex(16)
    with connect() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, "That username is taken.")
        cur = con.execute(
            "INSERT INTO users (username, pin_hash, salt, points, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, _hash(pin, salt), salt, config.START_POINTS,
             int(username.lower() in config.ADMIN_USERNAMES), utcnow()))
        game.ensure_ratings(con, cur.lastrowid)
        return _new_session(con, cur.lastrowid)


def login(username: str, pin: str) -> dict:
    key = (username or "").strip().lower()
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < 600]
    if len(_failures[key]) >= 5:
        raise HTTPException(429, "Too many wrong PINs. Try again in 10 minutes.")
    with connect() as con:
        user = one(con.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)))
        if not user or not secrets.compare_digest(user["pin_hash"], _hash(pin or "", user["salt"])):
            _failures[key].append(now)
            raise HTTPException(401, "Wrong username or PIN.")
        _failures.pop(key, None)
        return _new_session(con, user["id"])


def _new_session(con, user_id: int) -> dict:
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, utcnow()))
    return {"token": token, "user_id": user_id}


def logout(token: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))


def _token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = _token(authorization)
    if not token:
        raise HTTPException(401, "Please log in.")
    with connect() as con:
        user = one(con.execute(
            "SELECT u.id, u.username, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,)))
    if not user:
        raise HTTPException(401, "Your session expired. Please log in again.")
    return user


def optional_user(authorization: str | None = Header(default=None)) -> dict | None:
    try:
        return current_user(authorization)
    except HTTPException:
        return None
