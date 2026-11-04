"""Comptes utilisateurs et sessions.

- Mots de passe : scrypt (bibliothèque standard), sel aléatoire, jamais stockés en clair.
- Sessions : jeton aléatoire dans un cookie HttpOnly ; la base ne garde que son SHA-256,
  ce qui rend une copie de la table inutilisable pour se connecter.
- Limitation : après 5 échecs pour un même identifiant (et une même adresse), attente
  croissante avant un nouvel essai.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg

from . import db

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 256  # au-delà, refus (évite de hacher des mégaoctets)

# scrypt : ~16 Mo de mémoire et quelques dizaines de ms par hachage.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


# --- Mots de passe -------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Haché factice : vérifié quand l'identifiant n'existe pas, pour que la durée de réponse
# ne révèle pas quels comptes existent.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# --- Validation --------------------------------------------------------------------------


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("Identifiant invalide : 3 à 32 caractères parmi lettres, chiffres, « . », « _ » et « - ».")
    return username


def validate_password(password: str) -> str:
    if not password or len(password) < MIN_PASSWORD:
        raise ValueError(f"Mot de passe trop court : {MIN_PASSWORD} caractères au moins.")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"Mot de passe trop long : {MAX_PASSWORD} caractères au plus.")
    return password


# --- Comptes -----------------------------------------------------------------------------


def create_user(conn: psycopg.Connection, username: str, password: str) -> dict:
    """Crée un compte ; ValueError si l'identifiant est invalide ou déjà pris.

    Le premier compte créé reprend les favoris enregistrés avant l'arrivée des comptes.
    """
    username = validate_username(username)
    validate_password(password)
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id, username, created_at",
                (username, hash_password(password)),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise ValueError("Cet identifiant est déjà pris.") from None
    if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1:
        db.adopt_legacy_favorites(conn, row["id"])
    return {"id": row["id"], "username": row["username"]}


def authenticate(conn: psycopg.Connection, username: str, password: str) -> dict | None:
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE lower(username) = lower(%s)",
        ((username or "").strip(),),
    ).fetchone()
    if row is None:
        verify_password(password or "", _DUMMY_HASH)
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


# --- Sessions ----------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: psycopg.Connection, user_id: int, days: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at < now()")  # ménage des sessions expirées
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (_token_hash(token), user_id, datetime.now(timezone.utc) + timedelta(days=days)),
    )
    return token


def user_from_session(conn: psycopg.Connection, token: str | None) -> dict | None:
    if not token or len(token) > 200:
        return None
    row = conn.execute(
        """
        SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %s AND s.expires_at > now()
        """,
        (_token_hash(token),),
    ).fetchone()
    return {"id": row["id"], "username": row["username"]} if row else None


def delete_session(conn: psycopg.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))


# --- Limitation des essais -------------------------------------------------------------------


class LoginThrottle:
    """Compte les échecs par (adresse, identifiant) ; au-delà de `free` échecs, impose
    une attente qui double à chaque nouvel échec (plafonnée à `max_wait` secondes)."""

    def __init__(self, free: int = 5, base_wait: float = 15.0, max_wait: float = 900.0):
        self.free, self.base_wait, self.max_wait = free, base_wait, max_wait
        self._state: dict[tuple[str, str], tuple[int, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(ip: str, username: str) -> tuple[str, str]:
        return (ip or "?", (username or "").strip().lower())

    def wait_time(self, ip: str, username: str) -> float:
        with self._lock:
            _, until = self._state.get(self._key(ip, username), (0, 0.0))
        return max(0.0, until - time.monotonic())

    def failure(self, ip: str, username: str) -> None:
        key = self._key(ip, username)
        with self._lock:
            count, _ = self._state.get(key, (0, 0.0))
            count += 1
            wait = 0.0
            if count > self.free:
                wait = min(self.max_wait, self.base_wait * 2 ** (count - self.free - 1))
            self._state[key] = (count, time.monotonic() + wait)
            if len(self._state) > 10_000:  # borne la mémoire en cas d'abus
                self._state.clear()

    def success(self, ip: str, username: str) -> None:
        with self._lock:
            self._state.pop(self._key(ip, username), None)
