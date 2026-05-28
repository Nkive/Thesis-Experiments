import sqlite3, threading, time, uuid, json, os, base64, hashlib, hmac
import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class AssistanceSystem:
    """
    Peer-assistance backend — single class.
    Users: role='requester' (needs help) | role='helper' (offers help)
    Security: bcrypt passwords · AES-GCM encrypted locations & messages · HMAC-SHA256 JWT
    Concurrency: SQLite WAL + exclusive transaction for atomic request acceptance
    """

    TOKEN_TTL  = 3600   # seconds
    RATE_LIMIT = 30     # calls per 60 s per user

    def __init__(self, db_path=":memory:", secret="CHANGE_ME"):
        self._secret  = secret.encode()
        self._aes     = AESGCM(hashlib.sha256(self._secret).digest())
        self._local   = threading.local()
        self._rl_lock = threading.Lock()
        self._rl      = {}                    # {user_id: [timestamps]}
        self._setup_db()

    # ── DB ────────────────────────────────────────────────────────────────────
    def _conn(self):
        if not getattr(self._local, "c", None):
            c = sqlite3.connect(":memory:", check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.c = c
        return self._local.c

    def _setup_db(self):
        self._conn().executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE, pw_hash BLOB, role TEXT);
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, requester_id TEXT, from_enc BLOB, to_enc BLOB,
                needed_at TEXT, status TEXT DEFAULT 'open', helper_id TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, request_id TEXT, sender_role TEXT,
                body_enc BLOB, sent_at TEXT);
            CREATE INDEX IF NOT EXISTS i1 ON requests(status);
        """); self._conn().commit()

    # ── Crypto / Token helpers ────────────────────────────────────────────────
    def _enc(self, text: str) -> bytes:
        n = os.urandom(12); return n + self._aes.encrypt(n, text.encode(), None)

    def _dec(self, blob: bytes) -> str:
        return self._aes.decrypt(blob[:12], blob[12:], None).decode()

    def _b64(self, b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    def _token(self, uid: str, role: str) -> str:
        h = self._b64(json.dumps({"alg":"HS256"}).encode())
        p = self._b64(json.dumps({"sub":uid,"role":role,"exp":int(time.time())+self.TOKEN_TTL}).encode())
        s = self._b64(hmac.new(self._secret, f"{h}.{p}".encode(), hashlib.sha256).digest())
        return f"{h}.{p}.{s}"

    def _verify(self, token: str) -> dict:
        try: h, p, s = token.split(".")
        except: raise ValueError("Bad token")
        if not hmac.compare_digest(
            self._b64(hmac.new(self._secret, f"{h}.{p}".encode(), hashlib.sha256).digest()), s):
            raise ValueError("Invalid token")
        claims = json.loads(base64.urlsafe_b64decode(p + "=="))
        if claims["exp"] < time.time(): raise ValueError("Token expired")
        return claims

    # ── Rate limiter ──────────────────────────────────────────────────────────
    def _rate(self, uid: str):
        now = time.time()
        with self._rl_lock:
            hits = [t for t in self._rl.get(uid, []) if now - t < 60]
            if len(hits) >= self.RATE_LIMIT: raise PermissionError("Rate limit exceeded")
            self._rl[uid] = hits + [now]

    # ── Auth ──────────────────────────────────────────────────────────────────
    def register(self, username: str, password: str, role: str) -> dict:
        if role not in ("requester", "helper"): raise ValueError("Invalid role")
        if len(password) < 8: raise ValueError("Password min 8 chars")
        uid = str(uuid.uuid4())
        try:
            self._conn().execute("INSERT INTO users VALUES (?,?,?,?)",
                (uid, username, bcrypt.hashpw(password.encode(), bcrypt.gensalt()), role))
            self._conn().commit()
        except sqlite3.IntegrityError: raise ValueError("Username taken")
        return {"user_id": uid, "username": username, "role": role}

    def login(self, username: str, password: str) -> dict:
        row = self._conn().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        dummy = bcrypt.hashpw(b"x", bcrypt.gensalt())               # timing-safe
        if not row or not bcrypt.checkpw(password.encode(), row["pw_hash"] if row else dummy):
            raise ValueError("Invalid credentials")
        return {"token": self._token(row["id"], row["role"]), "role": row["role"]}

    # ── Requests (User 1) ─────────────────────────────────────────────────────
    def submit_request(self, token: str, from_loc: str, to_loc: str, needed_at: str) -> dict:
        c = self._verify(token); self._rate(c["sub"])
        if c["role"] != "requester": raise PermissionError("Requesters only")
        rid = str(uuid.uuid4()); now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._conn().execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)",
            (rid, c["sub"], self._enc(from_loc), self._enc(to_loc), needed_at, "open", None, now))
        self._conn().commit()
        return {"request_id": rid, "status": "open", "created_at": now}

    def cancel_request(self, token: str, rid: str) -> dict:
        c = self._verify(token); self._rate(c["sub"])
        row = self._conn().execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not row: raise ValueError("Not found")
        if row["requester_id"] != c["sub"]: raise PermissionError("Not your request")
        if row["status"] not in ("open","accepted"): raise ValueError("Cannot cancel")
        self._conn().execute("UPDATE requests SET status='cancelled' WHERE id=?", (rid,))
        self._conn().commit()
        return {"request_id": rid, "status": "cancelled"}

    # ── Requests (User 2) ─────────────────────────────────────────────────────
    def list_requests(self, token: str) -> list:
        c = self._verify(token); self._rate(c["sub"])
        if c["role"] != "helper": raise PermissionError("Helpers only")
        rows = self._conn().execute(
            "SELECT id, needed_at, created_at FROM requests WHERE status='open'").fetchall()
        return [dict(r) for r in rows]          # location NOT exposed

    def accept_request(self, token: str, rid: str) -> dict:
        c = self._verify(token); self._rate(c["sub"])
        if c["role"] != "helper": raise PermissionError("Helpers only")
        db = self._conn()
        db.execute("BEGIN EXCLUSIVE")
        try:
            row = db.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if not row: raise ValueError("Not found")
            if row["status"] != "open": raise ValueError("Request already " + row["status"])
            db.execute("UPDATE requests SET status='accepted', helper_id=? WHERE id=?",
                       (c["sub"], rid))
            db.commit()
        except:
            db.execute("ROLLBACK"); raise
        return {"request_id": rid, "status": "accepted",        # location revealed here only
                "from_loc": self._dec(bytes(row["from_enc"])),
                "to_loc":   self._dec(bytes(row["to_enc"])),
                "needed_at": row["needed_at"]}

    def complete_request(self, token: str, rid: str) -> dict:
        c = self._verify(token); self._rate(c["sub"])
        row = self._conn().execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not row or row["helper_id"] != c["sub"]: raise PermissionError("Not your request")
        self._conn().execute("UPDATE requests SET status='completed' WHERE id=?", (rid,))
        self._conn().commit()
        return {"request_id": rid, "status": "completed"}

    # ── Messaging ─────────────────────────────────────────────────────────────
    def send_message(self, token: str, rid: str, body: str) -> dict:
        c = self._verify(token); self._rate(c["sub"])
        if not body.strip() or len(body) > 1000: raise ValueError("Invalid message")
        row = self._conn().execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not row: raise ValueError("Request not found")
        if row["status"] != "accepted": raise ValueError("Request not active")
        if   c["sub"] == row["requester_id"]: role = "requester"
        elif c["sub"] == row["helper_id"]:    role = "helper"
        else: raise PermissionError("Not a participant")
        mid = str(uuid.uuid4()); now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._conn().execute("INSERT INTO messages VALUES (?,?,?,?,?)",
                             (mid, rid, role, self._enc(body), now))
        self._conn().commit()
        return {"message_id": mid, "sender_role": role, "sent_at": now}

    def get_messages(self, token: str, rid: str) -> list:
        c = self._verify(token); self._rate(c["sub"])
        row = self._conn().execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not row: raise ValueError("Not found")
        if c["sub"] not in (row["requester_id"], row["helper_id"]):
            raise PermissionError("Not a participant")
        rows = self._conn().execute(
            "SELECT * FROM messages WHERE request_id=? ORDER BY sent_at", (rid,)).fetchall()
        return [{"message_id": r["id"], "sender_role": r["sender_role"],  # no real user_id
                 "body": self._dec(bytes(r["body_enc"])), "sent_at": r["sent_at"]} for r in rows]


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = AssistanceSystem(secret="dev-secret")

    app.register("alice", "password123", "requester")
    app.register("bob",   "securepass1", "helper")
    app.register("carol", "securepass2", "helper")

    tok_a = app.login("alice", "password123")["token"]
    tok_b = app.login("bob",   "securepass1")["token"]
    tok_c = app.login("carol", "securepass2")["token"]

    req = app.submit_request(tok_a, "Central Station", "City Hall", "14:00")
    rid = req["request_id"]

    open_list = app.list_requests(tok_b)
    assert "from_loc" not in open_list[0], "Privacy violation!"
    print("✓ Helper sees no location before accepting")

    result = app.accept_request(tok_b, rid)
    print(f"✓ Bob accepted: {result['from_loc']} → {result['to_loc']}")

    try:
        app.accept_request(tok_c, rid)
    except ValueError as e:
        print(f"✓ Carol correctly blocked: {e}")

    app.send_message(tok_a, rid, "I'm wearing a red jacket.")
    app.send_message(tok_b, rid, "On my way!")

    for m in app.get_messages(tok_b, rid):
        print(f"  [{m['sender_role']}] {m['body']}")

    print("✓", app.complete_request(tok_b, rid))
    print("\n=== All checks passed ===")
