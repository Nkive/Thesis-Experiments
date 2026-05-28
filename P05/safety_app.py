from __future__ import annotations

"""
Campus Walk Safe - secure single-file Python MVP (v2)

What this version adds:
- defensive error handling
- persistent SQLite storage
- request history after login
- encrypted sensitive data at rest
- saved chat history that users can resume later
- GPS checkpoints only at request start and arrival
- optional safety sharing with family / close friends

Run:
    pip install fastapi uvicorn cryptography
    uvicorn campus_walk_safe_app_v2:app --reload

Docs:
    http://127.0.0.1:8000/docs

Important:
- This is an MVP backend.
- Passwords are salted and hashed.
- Sensitive stored fields are encrypted with Fernet.
- For real deployment, put this app behind HTTPS/TLS.
- No software can guarantee safety in every real-world event,
  but this version handles common and unexpected software failures more safely.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator, Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator


# --------------------------------------------------
# Configuration
# --------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CAMPUS_WALK_DB_PATH", os.path.join(APP_DIR, "campus_walk_safe.db"))
KEY_PATH = os.environ.get("CAMPUS_WALK_KEY_PATH", os.path.join(APP_DIR, "campus_walk_safe.key"))
SESSION_TTL_DAYS = 7
MAX_REQUESTS_PER_5_MIN = 3
MAX_MESSAGE_LENGTH = 1000
ARRIVAL_RADIUS_METERS = 150
REQUEST_RATE_WINDOW_SECONDS = 300
SECURITY_LOG_PATH = os.environ.get(
    "CAMPUS_WALK_SECURITY_LOG", os.path.join(APP_DIR, "campus_walk_safe_errors.log")
)

app = FastAPI(title="Campus Walk Safe Secure MVP", version="2.0.0")
lock = threading.RLock()


# --------------------------------------------------
# Utilities
# --------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def normalize_username(value: str) -> str:
    return value.strip().lower()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def safe_log_error(message: str) -> None:
    try:
        with open(SECURITY_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{utc_now().isoformat()}] {message}\n")
    except Exception:
        pass


# --------------------------------------------------
# Crypto helpers
# --------------------------------------------------
def load_or_create_fernet() -> Fernet:
    env_key = os.environ.get("APP_ENCRYPTION_KEY")
    if env_key:
        return Fernet(env_key.encode("utf-8"))

    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as fh:
            key = fh.read().strip()
        return Fernet(key)

    key = Fernet.generate_key()
    with open(KEY_PATH, "wb") as fh:
        fh.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass
    return Fernet(key)


FERNET = load_or_create_fernet()


def encrypt_text(value: str) -> str:
    return FERNET.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return FERNET.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Encrypted data could not be read") from exc


def encrypt_json(payload: dict[str, Any]) -> str:
    return encrypt_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def decrypt_json(value: Optional[str]) -> Optional[dict[str, Any]]:
    raw = decrypt_text(value)
    return json.loads(raw) if raw is not None else None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return base64.b64encode(digest).decode("utf-8"), base64.b64encode(salt).decode("utf-8")


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    salt = base64.b64decode(stored_salt.encode("utf-8"))
    computed_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(computed_hash, stored_hash)


# --------------------------------------------------
# Database helpers
# --------------------------------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db_cursor(write: bool = False) -> Generator[sqlite3.Cursor, None, None]:
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield cur
        if write:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def init_db() -> None:
    with lock:
        with db_cursor(write=True) as cur:
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email_hash TEXT NOT NULL UNIQUE,
                    email_encrypted TEXT NOT NULL,
                    full_name_encrypted TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT,
                    verified_student INTEGER NOT NULL DEFAULT 1,
                    available_for_walks INTEGER NOT NULL DEFAULT 0,
                    busy INTEGER NOT NULL DEFAULT 0,
                    campus_zone TEXT
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS walk_requests (
                    id TEXT PRIMARY KEY,
                    requester_id TEXT NOT NULL,
                    pickup_lat_encrypted TEXT NOT NULL,
                    pickup_lon_encrypted TEXT NOT NULL,
                    destination_name_encrypted TEXT NOT NULL,
                    destination_lat_encrypted TEXT NOT NULL,
                    destination_lon_encrypted TEXT NOT NULL,
                    campus_zone TEXT NOT NULL,
                    share_with_contacts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    matched_at TEXT,
                    arrived_at TEXT,
                    cancelled_at TEXT,
                    status TEXT NOT NULL,
                    match_id TEXT,
                    arrival_lat_encrypted TEXT,
                    arrival_lon_encrypted TEXT,
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    requester_id TEXT NOT NULL,
                    walker_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (request_id) REFERENCES walk_requests(id) ON DELETE CASCADE,
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (walker_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    match_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    content_encrypted TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
                    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS request_rate_limit (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS safety_contacts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name_encrypted TEXT NOT NULL,
                    relationship_encrypted TEXT,
                    contact_value_encrypted TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS safety_share_events (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    contact_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_encrypted TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (request_id) REFERENCES walk_requests(id) ON DELETE CASCADE,
                    FOREIGN KEY (contact_id) REFERENCES safety_contacts(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_walk_requests_requester ON walk_requests(requester_id);
                CREATE INDEX IF NOT EXISTS idx_walk_requests_status ON walk_requests(status, campus_zone, created_at);
                CREATE INDEX IF NOT EXISTS idx_matches_participants ON matches(requester_id, walker_id, active);
                CREATE INDEX IF NOT EXISTS idx_chat_messages_match ON chat_messages(match_id, sent_at);
                CREATE INDEX IF NOT EXISTS idx_rate_limit_user_time ON request_rate_limit(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_safety_events_contact_time ON safety_share_events(contact_id, created_at);
                """
            )


@app.on_event("startup")
def startup() -> None:
    init_db()


# --------------------------------------------------
# Error handlers
# --------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation failed",
            "details": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    safe_log_error(
        f"Unhandled error on {request.method} {request.url.path}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Unexpected server error",
            "request_path": request.url.path,
        },
    )


# --------------------------------------------------
# Pydantic models
# --------------------------------------------------
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    full_name: str = Field(min_length=2, max_length=100)
    university_email: EmailStr
    password: str = Field(min_length=8, max_length=100)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value: str) -> str:
        value = normalize_username(value)
        if not all(c.isalnum() or c in {"_", ".", "-"} for c in value):
            raise ValueError("Username may only contain letters, numbers, ., _, -")
        return value

    @field_validator("full_name")
    @classmethod
    def full_name_valid(cls, value: str) -> str:
        value = value.strip()
        if len(value.split()) < 1:
            raise ValueError("Please provide a valid name")
        return value


class LoginRequest(BaseModel):
    username_or_email: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=1, max_length=100)


class AvailabilityRequest(BaseModel):
    available: bool
    campus_zone: Optional[str] = Field(default=None, min_length=2, max_length=50)

    @field_validator("campus_zone")
    @classmethod
    def normalize_zone(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().lower() if value else value


class WalkRequestCreate(BaseModel):
    pickup_latitude: float = Field(ge=-90, le=90)
    pickup_longitude: float = Field(ge=-180, le=180)
    destination_name: str = Field(min_length=2, max_length=120)
    destination_latitude: float = Field(ge=-90, le=90)
    destination_longitude: float = Field(ge=-180, le=180)
    campus_zone: str = Field(min_length=2, max_length=50)
    share_with_contacts: bool = False

    @field_validator("campus_zone")
    @classmethod
    def normalize_zone(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("destination_name")
    @classmethod
    def clean_dest(cls, value: str) -> str:
        return value.strip()


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class ArrivalRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SafetyContactCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    relationship: str = Field(min_length=2, max_length=50)
    contact_value: str = Field(min_length=3, max_length=200)

    @field_validator("name", "relationship", "contact_value")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()


# --------------------------------------------------
# Domain helpers
# --------------------------------------------------
def first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else full_name.strip()


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import atan2, cos, radians, sin, sqrt

    radius = 6371000.0
    p1 = radians(lat1)
    p2 = radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return radius * c


def encrypt_float(value: float) -> str:
    return encrypt_text(f"{value:.8f}")


def decrypt_float(value: str) -> float:
    return float(decrypt_text(value))


def get_user_by_id(user_id: str) -> sqlite3.Row:
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def decrypt_user_public(row: sqlite3.Row) -> dict[str, Any]:
    full_name = decrypt_text(row["full_name_encrypted"]) or "Unknown"
    return {
        "id": row["id"],
        "username": row["username"],
        "first_name": first_name(full_name),
        "verified_student": bool(row["verified_student"]),
        "available_for_walks": bool(row["available_for_walks"]),
        "busy": bool(row["busy"]),
        "campus_zone": row["campus_zone"],
    }


def create_session(user_id: str) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = sha256_text(raw_token)
    now = utc_now()
    expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                "INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, token_hash, iso(now), iso(expires_at)),
            )
    return raw_token


def cleanup_expired_sessions() -> None:
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso(utc_now()),))


def get_current_user(authorization: Optional[str] = Header(default=None)) -> sqlite3.Row:
    cleanup_expired_sessions()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    raw_token = authorization.split(" ", 1)[1].strip()
    token_hash = sha256_text(raw_token)

    with db_cursor() as cur:
        row = cur.execute(
            """
            SELECT u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (token_hash, iso(utc_now())),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return row


def get_active_match_for_user(user_id: str) -> Optional[sqlite3.Row]:
    with db_cursor() as cur:
        row = cur.execute(
            """
            SELECT * FROM matches
            WHERE active = 1 AND (requester_id = ? OR walker_id = ?)
            LIMIT 1
            """,
            (user_id, user_id),
        ).fetchone()
    return row


def get_pending_request_for_user(user_id: str) -> Optional[sqlite3.Row]:
    with db_cursor() as cur:
        row = cur.execute(
            """
            SELECT * FROM walk_requests
            WHERE requester_id = ? AND status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return row


def assert_rate_limit(user_id: str) -> None:
    cutoff = utc_now() - timedelta(seconds=REQUEST_RATE_WINDOW_SECONDS)
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                "DELETE FROM request_rate_limit WHERE user_id = ? AND created_at < ?",
                (user_id, iso(cutoff)),
            )
            count = cur.execute(
                "SELECT COUNT(*) AS c FROM request_rate_limit WHERE user_id = ?",
                (user_id,),
            ).fetchone()["c"]
            if count >= MAX_REQUESTS_PER_5_MIN:
                raise HTTPException(
                    status_code=429,
                    detail="Too many walk requests in a short time. Please wait a few minutes.",
                )
            cur.execute(
                "INSERT INTO request_rate_limit (id, user_id, created_at) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), user_id, iso(utc_now())),
            )


def create_match(request_row: sqlite3.Row, walker_row: sqlite3.Row) -> str:
    match_id = str(uuid.uuid4())
    now = utc_now()
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO matches (id, request_id, requester_id, walker_id, started_at, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (match_id, request_row["id"], request_row["requester_id"], walker_row["id"], iso(now)),
            )
            cur.execute(
                "UPDATE walk_requests SET status = 'matched', match_id = ?, matched_at = ? WHERE id = ?",
                (match_id, iso(now), request_row["id"]),
            )
            cur.execute("UPDATE users SET busy = 1 WHERE id IN (?, ?)", (request_row["requester_id"], walker_row["id"]))
    return match_id


def try_match_pending_requests() -> None:
    with lock:
        with db_cursor(write=False) as cur:
            pending_requests = cur.execute(
                """
                SELECT * FROM walk_requests
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()

        for request_row in pending_requests:
            with db_cursor() as cur:
                walker_row = cur.execute(
                    """
                    SELECT * FROM users
                    WHERE id != ?
                      AND available_for_walks = 1
                      AND busy = 0
                      AND campus_zone = ?
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (request_row["requester_id"], request_row["campus_zone"]),
                ).fetchone()
            if walker_row:
                create_match(request_row, walker_row)


def build_request_summary(request_row: sqlite3.Row) -> dict[str, Any]:
    return {
        "request_id": request_row["id"],
        "status": request_row["status"],
        "campus_zone": request_row["campus_zone"],
        "destination_name": decrypt_text(request_row["destination_name_encrypted"]),
        "created_at": request_row["created_at"],
        "matched_at": request_row["matched_at"],
        "arrived_at": request_row["arrived_at"],
        "cancelled_at": request_row["cancelled_at"],
        "share_with_contacts": bool(request_row["share_with_contacts"]),
    }


def build_minimum_partner_view(user_row: sqlite3.Row) -> dict[str, Any]:
    user = decrypt_user_public(user_row)
    return {
        "first_name": user["first_name"],
        "verified_student": user["verified_student"],
        "campus_zone": user["campus_zone"],
    }


def require_match_participant(match_id: str, user_id: str) -> sqlite3.Row:
    with db_cursor() as cur:
        match_row = cur.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match_row:
        raise HTTPException(status_code=404, detail="Match not found")
    if user_id not in {match_row["requester_id"], match_row["walker_id"]}:
        raise HTTPException(status_code=403, detail="You are not part of this match")
    return match_row


def create_safety_share_events_for_start(request_id: str, requester_id: str) -> int:
    with db_cursor() as cur:
        request_row = cur.execute("SELECT * FROM walk_requests WHERE id = ?", (request_id,)).fetchone()
        contacts = cur.execute(
            "SELECT * FROM safety_contacts WHERE user_id = ? ORDER BY created_at ASC",
            (requester_id,),
        ).fetchall()
        requester = cur.execute("SELECT * FROM users WHERE id = ?", (requester_id,)).fetchone()

    if not request_row or not contacts or not requester:
        return 0

    full_name = decrypt_text(requester["full_name_encrypted"]) or "Student"
    payload = {
        "student_first_name": first_name(full_name),
        "event_type": "walk_started",
        "created_at": request_row["created_at"],
        "pickup_location": {
            "latitude": decrypt_float(request_row["pickup_lat_encrypted"]),
            "longitude": decrypt_float(request_row["pickup_lon_encrypted"]),
        },
        "destination_name": decrypt_text(request_row["destination_name_encrypted"]),
        "campus_zone": request_row["campus_zone"],
    }

    now = iso(utc_now())
    with lock:
        with db_cursor(write=True) as cur:
            for contact in contacts:
                cur.execute(
                    """
                    INSERT INTO safety_share_events (id, request_id, contact_id, event_type, payload_encrypted, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        request_id,
                        contact["id"],
                        "walk_started",
                        encrypt_json(payload),
                        now,
                    ),
                )
    return len(contacts)


def create_safety_share_events_for_arrival(request_id: str, requester_id: str, lat: float, lon: float) -> int:
    with db_cursor() as cur:
        contacts = cur.execute(
            "SELECT * FROM safety_contacts WHERE user_id = ? ORDER BY created_at ASC",
            (requester_id,),
        ).fetchall()
        requester = cur.execute("SELECT * FROM users WHERE id = ?", (requester_id,)).fetchone()

    if not contacts or not requester:
        return 0

    full_name = decrypt_text(requester["full_name_encrypted"]) or "Student"
    payload = {
        "student_first_name": first_name(full_name),
        "event_type": "walk_arrived",
        "created_at": iso(utc_now()),
        "arrival_location": {
            "latitude": lat,
            "longitude": lon,
        },
    }

    with lock:
        with db_cursor(write=True) as cur:
            for contact in contacts:
                cur.execute(
                    """
                    INSERT INTO safety_share_events (id, request_id, contact_id, event_type, payload_encrypted, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        request_id,
                        contact["id"],
                        "walk_arrived",
                        encrypt_json(payload),
                        iso(utc_now()),
                    ),
                )
    return len(contacts)


def ensure_contact_owner(contact_id: str, owner_id: str) -> sqlite3.Row:
    with db_cursor() as cur:
        contact = cur.execute(
            "SELECT * FROM safety_contacts WHERE id = ? AND user_id = ?",
            (contact_id, owner_id),
        ).fetchone()
    if not contact:
        raise HTTPException(status_code=404, detail="Safety contact not found")
    return contact


# --------------------------------------------------
# Routes
# --------------------------------------------------
@app.get("/")
def root() -> dict[str, Any]:
    return {
        "message": "Campus Walk Safe Secure MVP is running.",
        "docs": "/docs",
        "storage": "sqlite",
        "encryption": "sensitive stored fields encrypted at rest",
        "gps_policy": "GPS is used only at request creation and arrival confirmation",
        "transport_note": "Use HTTPS/TLS in deployment for encrypted data in transit",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    with db_cursor() as cur:
        users = cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        pending_requests = cur.execute(
            "SELECT COUNT(*) AS c FROM walk_requests WHERE status = 'pending'"
        ).fetchone()["c"]
        active_matches = cur.execute(
            "SELECT COUNT(*) AS c FROM matches WHERE active = 1"
        ).fetchone()["c"]
    return {
        "status": "ok",
        "users": users,
        "pending_requests": pending_requests,
        "active_matches": active_matches,
        "server_time": iso(utc_now()),
    }


@app.post("/register")
def register(payload: RegisterRequest) -> dict[str, Any]:
    username = normalize_username(payload.username)
    email = normalize_email(payload.university_email)
    email_hash = sha256_text(email)
    password_hash, password_salt = hash_password(payload.password)

    if "@" not in email:
        raise HTTPException(status_code=400, detail="Please provide a valid university email")

    with lock:
        try:
            with db_cursor(write=True) as cur:
                cur.execute(
                    """
                    INSERT INTO users (
                        id, username, email_hash, email_encrypted, full_name_encrypted,
                        password_hash, password_salt, created_at, verified_student,
                        available_for_walks, busy, campus_zone
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, NULL)
                    """,
                    (
                        str(uuid.uuid4()),
                        username,
                        email_hash,
                        encrypt_text(email),
                        encrypt_text(payload.full_name.strip()),
                        password_hash,
                        password_salt,
                        iso(utc_now()),
                    ),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Username or email already exists")

    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    user = decrypt_user_public(row)
    return {
        "message": "User registered successfully",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "verified_student": user["verified_student"],
        },
    }


@app.post("/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    lookup = payload.username_or_email.strip().lower()
    email_hash = sha256_text(lookup)

    with db_cursor() as cur:
        row = cur.execute(
            "SELECT * FROM users WHERE username = ? OR email_hash = ? LIMIT 1",
            (lookup, email_hash),
        ).fetchone()

    if not row or not verify_password(payload.password, row["password_hash"], row["password_salt"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_session(row["id"])
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (iso(utc_now()), row["id"]))

    user = decrypt_user_public(row)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_days": SESSION_TTL_DAYS,
        "user": user,
    }


@app.post("/logout")
def logout(current_user: sqlite3.Row = Depends(get_current_user), authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    raw_token = authorization.split(" ", 1)[1].strip()
    token_hash = sha256_text(raw_token)
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash = ?", (current_user["id"], token_hash))
    return {"message": "Logged out successfully"}


@app.get("/me")
def me(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    user = decrypt_user_public(current_user)
    user["last_login_at"] = current_user["last_login_at"]
    return user


@app.post("/availability")
def set_availability(payload: AvailabilityRequest, current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    if bool(current_user["busy"]) and payload.available:
        raise HTTPException(status_code=400, detail="You are already in an active walk")
    if payload.available and not payload.campus_zone:
        raise HTTPException(status_code=400, detail="campus_zone is required when availability is true")

    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                "UPDATE users SET available_for_walks = ?, campus_zone = ? WHERE id = ?",
                (1 if payload.available else 0, payload.campus_zone if payload.available else None, current_user["id"]),
            )
    try_match_pending_requests()

    return {
        "message": "Availability updated",
        "available_for_walks": payload.available,
        "campus_zone": payload.campus_zone if payload.available else None,
    }


@app.post("/walk-requests")
def create_walk_request(payload: WalkRequestCreate, current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    if bool(current_user["busy"]):
        raise HTTPException(status_code=400, detail="You are already in an active walk")
    if get_pending_request_for_user(current_user["id"]):
        raise HTTPException(status_code=400, detail="You already have a pending walk request")

    assert_rate_limit(current_user["id"])

    request_id = str(uuid.uuid4())
    now = utc_now()
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO walk_requests (
                    id, requester_id, pickup_lat_encrypted, pickup_lon_encrypted,
                    destination_name_encrypted, destination_lat_encrypted, destination_lon_encrypted,
                    campus_zone, share_with_contacts, created_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    request_id,
                    current_user["id"],
                    encrypt_float(payload.pickup_latitude),
                    encrypt_float(payload.pickup_longitude),
                    encrypt_text(payload.destination_name),
                    encrypt_float(payload.destination_latitude),
                    encrypt_float(payload.destination_longitude),
                    payload.campus_zone,
                    1 if payload.share_with_contacts else 0,
                    iso(now),
                ),
            )

    contacts_notified = 0
    if payload.share_with_contacts:
        contacts_notified = create_safety_share_events_for_start(request_id, current_user["id"])

    try_match_pending_requests()

    with db_cursor() as cur:
        request_row = cur.execute("SELECT * FROM walk_requests WHERE id = ?", (request_id,)).fetchone()

    response: dict[str, Any] = {
        "message": "Walk request created",
        "request": build_request_summary(request_row),
        "gps_usage": "start checkpoint saved",
        "contacts_notified": contacts_notified,
    }

    if request_row["match_id"]:
        with db_cursor() as cur:
            match_row = cur.execute("SELECT * FROM matches WHERE id = ?", (request_row["match_id"],)).fetchone()
            walker = cur.execute("SELECT * FROM users WHERE id = ?", (match_row["walker_id"],)).fetchone()
        response["message"] = "Walking partner found"
        response["match_id"] = match_row["id"]
        response["walking_partner"] = build_minimum_partner_view(walker)

    return response


@app.get("/walk-requests/me")
def my_walk_request_state(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    active_match = get_active_match_for_user(current_user["id"])
    if active_match:
        return {
            "state": "matched",
            "match_id": active_match["id"],
            "request_id": active_match["request_id"],
            "started_at": active_match["started_at"],
        }

    pending = get_pending_request_for_user(current_user["id"])
    if pending:
        return {
            "state": "pending",
            "request": build_request_summary(pending),
        }

    return {"state": "none"}


@app.get("/walk-requests/history")
def walk_request_history(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM walk_requests WHERE requester_id = ? ORDER BY created_at DESC",
            (current_user["id"],),
        ).fetchall()
    return {
        "count": len(rows),
        "requests": [build_request_summary(row) for row in rows],
    }


@app.delete("/walk-requests/me")
def cancel_my_pending_request(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    pending = get_pending_request_for_user(current_user["id"])
    if not pending:
        raise HTTPException(status_code=404, detail="No pending request found")

    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                "UPDATE walk_requests SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
                (iso(utc_now()), pending["id"]),
            )
    return {"message": "Pending walk request cancelled", "request_id": pending["id"]}


@app.get("/my-match")
def my_match(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    match = get_active_match_for_user(current_user["id"])
    if not match:
        raise HTTPException(status_code=404, detail="No active match found")

    with db_cursor() as cur:
        request_row = cur.execute("SELECT * FROM walk_requests WHERE id = ?", (match["request_id"],)).fetchone()
        requester = cur.execute("SELECT * FROM users WHERE id = ?", (match["requester_id"],)).fetchone()
        walker = cur.execute("SELECT * FROM users WHERE id = ?", (match["walker_id"],)).fetchone()

    request_payload = {
        "pickup_location": {
            "latitude": decrypt_float(request_row["pickup_lat_encrypted"]),
            "longitude": decrypt_float(request_row["pickup_lon_encrypted"]),
        },
        "destination_name": decrypt_text(request_row["destination_name_encrypted"]),
        "campus_zone": request_row["campus_zone"],
        "gps_policy": "no continuous tracking; only start and arrival checkpoints",
    }

    if current_user["id"] == requester["id"]:
        return {
            "match_id": match["id"],
            "role": "requester",
            "started_at": match["started_at"],
            "request": request_payload,
            "walking_partner": build_minimum_partner_view(walker),
        }

    return {
        "match_id": match["id"],
        "role": "walking_partner",
        "started_at": match["started_at"],
        "request": request_payload,
        "requester": build_minimum_partner_view(requester),
    }


@app.get("/matches/{match_id}/chat")
def get_chat(
    match_id: str,
    after: Optional[str] = Query(default=None, description="Optional ISO timestamp. Return only newer messages."),
    current_user: sqlite3.Row = Depends(get_current_user),
) -> dict[str, Any]:
    require_match_participant(match_id, current_user["id"])
    after_dt = parse_dt(after) if after else None

    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM chat_messages WHERE match_id = ? ORDER BY sent_at ASC",
            (match_id,),
        ).fetchall()

    messages: list[dict[str, Any]] = []
    for row in rows:
        sent_at = parse_dt(row["sent_at"])
        if after_dt and sent_at and sent_at <= after_dt:
            continue
        sender = get_user_by_id(row["sender_id"])
        sender_name = decrypt_text(sender["full_name_encrypted"]) or "Unknown"
        messages.append(
            {
                "id": row["id"],
                "sender_first_name": first_name(sender_name),
                "content": decrypt_text(row["content_encrypted"]),
                "sent_at": row["sent_at"],
            }
        )

    return {
        "match_id": match_id,
        "count": len(messages),
        "messages": messages,
    }


@app.post("/matches/{match_id}/chat")
def send_chat_message(match_id: str, payload: ChatSendRequest, current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    match = require_match_participant(match_id, current_user["id"])
    if not bool(match["active"]):
        raise HTTPException(status_code=400, detail="Cannot send messages to an ended walk")

    message_id = str(uuid.uuid4())
    now = utc_now()
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                "INSERT INTO chat_messages (id, match_id, sender_id, content_encrypted, sent_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, match_id, current_user["id"], encrypt_text(payload.message), iso(now)),
            )

    full_name = decrypt_text(current_user["full_name_encrypted"]) or "Unknown"
    return {
        "message": "Chat message sent",
        "chat_message": {
            "id": message_id,
            "sender_first_name": first_name(full_name),
            "content": payload.message,
            "sent_at": iso(now),
        },
    }


@app.post("/walks/arrive")
def confirm_arrival(payload: ArrivalRequest, current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    match = get_active_match_for_user(current_user["id"])
    if not match:
        raise HTTPException(status_code=404, detail="No active match found")
    if current_user["id"] != match["requester_id"]:
        raise HTTPException(status_code=403, detail="Only the requesting student can confirm arrival")

    with db_cursor() as cur:
        request_row = cur.execute("SELECT * FROM walk_requests WHERE id = ?", (match["request_id"],)).fetchone()
        walker = cur.execute("SELECT * FROM users WHERE id = ?", (match["walker_id"],)).fetchone()

    dest_lat = decrypt_float(request_row["destination_lat_encrypted"])
    dest_lon = decrypt_float(request_row["destination_lon_encrypted"])
    distance_m = haversine_meters(payload.latitude, payload.longitude, dest_lat, dest_lon)
    if distance_m > ARRIVAL_RADIUS_METERS:
        raise HTTPException(
            status_code=400,
            detail=f"Arrival location is too far from the saved destination ({distance_m:.1f} meters)",
        )

    now = utc_now()
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE walk_requests
                SET status = 'completed',
                    arrived_at = ?,
                    arrival_lat_encrypted = ?,
                    arrival_lon_encrypted = ?
                WHERE id = ?
                """,
                (iso(now), encrypt_float(payload.latitude), encrypt_float(payload.longitude), request_row["id"]),
            )
            cur.execute(
                "UPDATE matches SET active = 0, ended_at = ? WHERE id = ?",
                (iso(now), match["id"]),
            )
            cur.execute(
                "UPDATE users SET busy = 0 WHERE id IN (?, ?)",
                (match["requester_id"], match["walker_id"]),
            )

    contacts_notified = 0
    if bool(request_row["share_with_contacts"]):
        contacts_notified = create_safety_share_events_for_arrival(request_row["id"], current_user["id"], payload.latitude, payload.longitude)

    walker_name = decrypt_text(walker["full_name_encrypted"]) or "Unknown"
    return {
        "message": "Arrival confirmed and walk ended successfully",
        "match_id": match["id"],
        "ended_at": iso(now),
        "gps_usage": "arrival checkpoint saved",
        "distance_from_destination_meters": round(distance_m, 1),
        "walking_partner_first_name": first_name(walker_name),
        "contacts_notified": contacts_notified,
    }


@app.post("/safety-contacts")
def add_safety_contact(payload: SafetyContactCreate, current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    contact_id = str(uuid.uuid4())
    raw_token = secrets.token_urlsafe(24)
    token_hash = sha256_text(raw_token)

    with lock:
        with db_cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO safety_contacts (
                    id, user_id, name_encrypted, relationship_encrypted,
                    contact_value_encrypted, token_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id,
                    current_user["id"],
                    encrypt_text(payload.name),
                    encrypt_text(payload.relationship),
                    encrypt_text(payload.contact_value),
                    token_hash,
                    iso(utc_now()),
                ),
            )

    return {
        "message": "Safety contact added",
        "contact": {
            "id": contact_id,
            "name": payload.name,
            "relationship": payload.relationship,
            "contact_value": payload.contact_value,
            "share_feed_token": raw_token,
            "note": "Give this token only to the trusted contact. They can use it to read shared safety checkpoints.",
        },
    }


@app.get("/safety-contacts")
def list_safety_contacts(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    with db_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM safety_contacts WHERE user_id = ? ORDER BY created_at ASC",
            (current_user["id"],),
        ).fetchall()

    contacts = [
        {
            "id": row["id"],
            "name": decrypt_text(row["name_encrypted"]),
            "relationship": decrypt_text(row["relationship_encrypted"]),
            "contact_value": decrypt_text(row["contact_value_encrypted"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {"count": len(contacts), "contacts": contacts}


@app.delete("/safety-contacts/{contact_id}")
def delete_safety_contact(contact_id: str, current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    ensure_contact_owner(contact_id, current_user["id"])
    with lock:
        with db_cursor(write=True) as cur:
            cur.execute("DELETE FROM safety_contacts WHERE id = ? AND user_id = ?", (contact_id, current_user["id"]))
    return {"message": "Safety contact deleted", "contact_id": contact_id}


@app.get("/public/safety-feed/{contact_id}")
def public_safety_feed(contact_id: str, token: str = Query(..., min_length=10)) -> dict[str, Any]:
    with db_cursor() as cur:
        contact = cur.execute("SELECT * FROM safety_contacts WHERE id = ?", (contact_id,)).fetchone()
        if not contact:
            raise HTTPException(status_code=404, detail="Safety contact not found")

        if not hmac.compare_digest(contact["token_hash"], sha256_text(token)):
            raise HTTPException(status_code=401, detail="Invalid safety feed token")

        events = cur.execute(
            "SELECT * FROM safety_share_events WHERE contact_id = ? ORDER BY created_at DESC LIMIT 50",
            (contact_id,),
        ).fetchall()

    return {
        "contact_name": decrypt_text(contact["name_encrypted"]),
        "relationship": decrypt_text(contact["relationship_encrypted"]),
        "count": len(events),
        "events": [
            {
                "event_id": event["id"],
                "event_type": event["event_type"],
                "created_at": event["created_at"],
                "payload": decrypt_json(event["payload_encrypted"]),
            }
            for event in events
        ],
    }


@app.get("/safety-share-events")
def my_safety_share_events(current_user: sqlite3.Row = Depends(get_current_user)) -> dict[str, Any]:
    with db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT e.*, c.name_encrypted
            FROM safety_share_events e
            JOIN safety_contacts c ON c.id = e.contact_id
            WHERE c.user_id = ?
            ORDER BY e.created_at DESC
            LIMIT 100
            """,
            (current_user["id"],),
        ).fetchall()

    events = [
        {
            "event_id": row["id"],
            "contact_name": decrypt_text(row["name_encrypted"]),
            "event_type": row["event_type"],
            "created_at": row["created_at"],
            "payload": decrypt_json(row["payload_encrypted"]),
        }
        for row in rows
    ]
    return {"count": len(events), "events": events}


@app.get("/admin/stats")
def admin_stats() -> dict[str, Any]:
    with db_cursor() as cur:
        return {
            "users": cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
            "available_walkers": cur.execute(
                "SELECT COUNT(*) AS c FROM users WHERE available_for_walks = 1 AND busy = 0"
            ).fetchone()["c"],
            "busy_users": cur.execute("SELECT COUNT(*) AS c FROM users WHERE busy = 1").fetchone()["c"],
            "pending_requests": cur.execute(
                "SELECT COUNT(*) AS c FROM walk_requests WHERE status = 'pending'"
            ).fetchone()["c"],
            "matched_requests": cur.execute(
                "SELECT COUNT(*) AS c FROM walk_requests WHERE status = 'matched'"
            ).fetchone()["c"],
            "completed_requests": cur.execute(
                "SELECT COUNT(*) AS c FROM walk_requests WHERE status = 'completed'"
            ).fetchone()["c"],
            "cancelled_requests": cur.execute(
                "SELECT COUNT(*) AS c FROM walk_requests WHERE status = 'cancelled'"
            ).fetchone()["c"],
            "active_matches": cur.execute(
                "SELECT COUNT(*) AS c FROM matches WHERE active = 1"
            ).fetchone()["c"],
            "stored_chat_messages": cur.execute(
                "SELECT COUNT(*) AS c FROM chat_messages"
            ).fetchone()["c"],
        }
    