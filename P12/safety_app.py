"""
campus_safety.py
================
Campus Student Safety System — all functions in a single class.

Account types : Student | Helper
Request states: PENDING → ACCEPTED → COMPLETED | CANCELLED
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import os
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional


# ─────────────────────────────────────────────
#  Enumerations
# ─────────────────────────────────────────────

class AccountType(str, Enum):
    STUDENT = "student"
    HELPER  = "helper"


class RequestStatus(str, Enum):
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ─────────────────────────────────────────────
#  Data models
# ─────────────────────────────────────────────

@dataclass
class User:
    user_id:       str
    username:      str
    password_hash: str
    account_type:  AccountType
    full_name:     str
    email:         str
    phone:         str
    # Students only
    student_id:    Optional[str] = None
    created_at:    datetime      = field(default_factory=datetime.utcnow)
    is_active:     bool          = True


@dataclass
class HelpRequest:
    request_id:   str
    student_id:   str
    location:     str
    destination:  str
    leaving_time: datetime
    notes:        str            = ""
    status:       RequestStatus  = RequestStatus.PENDING
    helper_id:    Optional[str]  = None
    created_at:   datetime       = field(default_factory=datetime.utcnow)
    accepted_at:  Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class Notification:
    notification_id: str
    recipient_id:    str
    title:           str
    body:            str
    request_id:      Optional[str] = None
    read:            bool           = False
    created_at:      datetime       = field(default_factory=datetime.utcnow)


@dataclass
class ChatMessage:
    message_id: str
    request_id: str
    sender_id:  str
    content:    str
    sent_at:    datetime = field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
#  Privacy-safe views (sent to helpers)
# ─────────────────────────────────────────────

@dataclass
class RequestSummary:
    """Shown to helpers browsing available requests — no student PII."""
    request_id:   str
    location:     str
    destination:  str
    leaving_time: datetime
    notes:        str
    created_at:   datetime


@dataclass
class AcceptedRequestView:
    """Shown to a helper after they accept — still no email / phone / student_id."""
    request_id:      str
    location:        str
    destination:     str
    leaving_time:    datetime
    notes:           str
    student_username: str
    accepted_at:     datetime


# ─────────────────────────────────────────────
#  Result wrappers
# ─────────────────────────────────────────────

@dataclass
class Result:
    success:    bool
    message:    str           = ""
    data:       object        = None   # carries request_id, token, etc.


# ─────────────────────────────────────────────
#  Main class
# ─────────────────────────────────────────────

class CampusSafetySystem:
    """
    All backend functions for the Campus Student Safety app.

    Thread-safety
    -------------
    Three independent RLocks guard users, requests, and notifications/chat
    so that independent subsystems can proceed concurrently.

    Notification fan-out runs in a ThreadPoolExecutor so heavy helper
    populations never block the calling thread.

    Password security
    -----------------
    PBKDF2-HMAC-SHA256 with 390 000 iterations (NIST 2023 guidance).

    Collision window
    ----------------
    Each accepted request occupies [leaving_time − 30 min, leaving_time + 30 min].
    A helper cannot accept a second request whose window overlaps an existing one.
    """

    _PBKDF2_ITERATIONS = 390_000
    _COLLISION_BUFFER  = timedelta(minutes=30)
    _NOTIF_WORKERS     = 16

    def __init__(self) -> None:
        # ── storage ──────────────────────────────────────────────────
        self._users:         Dict[str, User]         = {}   # user_id  → User
        self._usernames:     Dict[str, str]           = {}   # username → user_id
        self._sessions:      Dict[str, str]           = {}   # token    → user_id
        self._requests:      Dict[str, HelpRequest]  = {}
        self._notifications: Dict[str, Notification] = {}
        self._messages:      Dict[str, ChatMessage]  = {}

        # ── locks ────────────────────────────────────────────────────
        self._user_lock    = threading.RLock()
        self._request_lock = threading.RLock()
        self._notif_lock   = threading.RLock()

        # ── background workers for notification fan-out ───────────────
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._NOTIF_WORKERS,
            thread_name_prefix="notif",
        )

    # ═════════════════════════════════════════════════════════════════
    #  AUTHENTICATION
    # ═════════════════════════════════════════════════════════════════

    def register_student(
        self,
        username:   str,
        password:   str,
        full_name:  str,
        student_id: str,
        email:      str,
        phone:      str,
    ) -> Result:
        """
        Create a student account.

        student_id, email, and phone are stored server-side only and
        are never exposed to helpers.
        """
        err = self._validate_credentials(username, password)
        if err:
            return Result(success=False, message=err)

        user_id = self._new_id()
        user = User(
            user_id=user_id,
            username=username,
            password_hash=self._hash_password(password),
            account_type=AccountType.STUDENT,
            full_name=full_name,
            email=email,
            phone=phone,
            student_id=student_id,
        )
        with self._user_lock:
            self._users[user_id]        = user
            self._usernames[username]   = user_id

        token = self._create_session(user_id)
        return Result(success=True, message="Student account created.",
                      data={"user_id": user_id, "token": token})

    def register_helper(
        self,
        username:  str,
        password:  str,
        full_name: str,
        email:     str,
        phone:     str,
    ) -> Result:
        """Create a helper (volunteer escort) account."""
        err = self._validate_credentials(username, password)
        if err:
            return Result(success=False, message=err)

        user_id = self._new_id()
        user = User(
            user_id=user_id,
            username=username,
            password_hash=self._hash_password(password),
            account_type=AccountType.HELPER,
            full_name=full_name,
            email=email,
            phone=phone,
        )
        with self._user_lock:
            self._users[user_id]      = user
            self._usernames[username] = user_id

        token = self._create_session(user_id)
        return Result(success=True, message="Helper account created.",
                      data={"user_id": user_id, "token": token})

    def login(self, username: str, password: str) -> Result:
        """Authenticate a user and return a session token."""
        with self._user_lock:
            uid  = self._usernames.get(username)
            user = self._users.get(uid) if uid else None

        if not user or not user.is_active:
            return Result(success=False, message="Invalid credentials.")
        if not self._verify_password(password, user.password_hash):
            return Result(success=False, message="Invalid credentials.")

        token = self._create_session(user.user_id)
        return Result(success=True, message="Login successful.",
                      data={"user_id": user.user_id, "token": token})

    def logout(self, token: str) -> Result:
        """Invalidate a session token."""
        with self._user_lock:
            self._sessions.pop(token, None)
        return Result(success=True, message="Logged out.")

    # ═════════════════════════════════════════════════════════════════
    #  HELP REQUESTS — student side
    # ═════════════════════════════════════════════════════════════════

    def create_request(
        self,
        token:        str,
        location:     str,
        destination:  str,
        leaving_time: datetime,
        notes:        str = "",
    ) -> Result:
        """
        Student submits a help request.

        Rules:
        - Caller must be a student.
        - leaving_time must be in the future.
        - Student may not have another active (PENDING / ACCEPTED) request.

        On success, all active helpers are notified in the background.
        """
        user = self._auth_user(token, AccountType.STUDENT)
        if not user:
            return Result(success=False, message="Not authenticated as a student.")

        if leaving_time <= datetime.utcnow():
            return Result(success=False, message="Leaving time must be in the future.")
        if not location.strip() or not destination.strip():
            return Result(success=False, message="Location and destination are required.")

        with self._request_lock:
            for r in self._requests.values():
                if r.student_id == user.user_id and \
                   r.status in (RequestStatus.PENDING, RequestStatus.ACCEPTED):
                    return Result(success=False,
                                  message="You already have an active request. "
                                          "Cancel it before creating a new one.")

            req = HelpRequest(
                request_id=self._new_id(),
                student_id=user.user_id,
                location=location.strip(),
                destination=destination.strip(),
                leaving_time=leaving_time,
                notes=notes.strip(),
            )
            self._requests[req.request_id] = req

        # Fan-out notifications to all active helpers (non-blocking)
        self._notify_helpers_new_request(req.request_id, location, destination)

        return Result(success=True, message="Help request created.",
                      data={"request_id": req.request_id})

    def cancel_request(self, token: str, request_id: str) -> Result:
        """Student cancels their own PENDING or ACCEPTED request."""
        user = self._auth_user(token, AccountType.STUDENT)
        if not user:
            return Result(success=False, message="Not authenticated as a student.")

        with self._request_lock:
            req = self._requests.get(request_id)
            if not req:
                return Result(success=False, message="Request not found.")
            if req.student_id != user.user_id:
                return Result(success=False, message="Not your request.")
            if req.status in (RequestStatus.COMPLETED, RequestStatus.CANCELLED):
                return Result(success=False,
                              message=f"Request is already {req.status.value}.")
            helper_id_to_notify = req.helper_id
            req.status = RequestStatus.CANCELLED

        if helper_id_to_notify:
            self._add_notification(
                recipient_id=helper_id_to_notify,
                title="Request cancelled",
                body="The student has cancelled their help request.",
                request_id=request_id,
            )
        return Result(success=True, message="Request cancelled.",
                      data={"request_id": request_id})

    def get_student_requests(self, token: str) -> List[HelpRequest]:
        """Return all requests belonging to the authenticated student."""
        user = self._auth_user(token, AccountType.STUDENT)
        if not user:
            return []
        with self._request_lock:
            return [r for r in self._requests.values()
                    if r.student_id == user.user_id]

    # ═════════════════════════════════════════════════════════════════
    #  HELP REQUESTS — helper side
    # ═════════════════════════════════════════════════════════════════

    def list_pending_requests(self, token: str) -> List[RequestSummary]:
        """
        Return all PENDING requests as privacy-safe summaries.

        No student PII is included — only the information a helper
        needs to decide whether to accept.
        """
        user = self._auth_user(token, AccountType.HELPER)
        if not user:
            return []
        with self._request_lock:
            return [
                RequestSummary(
                    request_id=r.request_id,
                    location=r.location,
                    destination=r.destination,
                    leaving_time=r.leaving_time,
                    notes=r.notes,
                    created_at=r.created_at,
                )
                for r in self._requests.values()
                if r.status == RequestStatus.PENDING
            ]

    def accept_request(self, token: str, request_id: str) -> Result:
        """
        Helper accepts a pending request.

        Collision check (atomic under the request lock):
        - Request must still be PENDING.
        - Helper must have no other ACCEPTED request whose time window
          overlaps this one (±30 minutes around leaving_time).

        On success the student is notified.
        """
        user = self._auth_user(token, AccountType.HELPER)
        if not user:
            return Result(success=False, message="Not authenticated as a helper.")

        with self._request_lock:
            req = self._requests.get(request_id)
            if not req:
                return Result(success=False, message="Request not found.")
            if req.status != RequestStatus.PENDING:
                return Result(success=False, message="Request is no longer available.")

            collision = self._find_collision(user.user_id, req)
            if collision:
                return Result(
                    success=False,
                    message=(
                        f"Time conflict with request {collision.request_id} "
                        f"(leaving {collision.leaving_time.isoformat()}). "
                        "Please complete that journey first."
                    ),
                )

            req.status      = RequestStatus.ACCEPTED
            req.helper_id   = user.user_id
            req.accepted_at = datetime.utcnow()

        self._add_notification(
            recipient_id=req.student_id,
            title="Your request has been accepted",
            body=f"Helper '{user.username}' is on their way. You can now chat with them.",
            request_id=request_id,
        )
        return Result(success=True, message="Request accepted.",
                      data={"request_id": request_id})

    def complete_request(self, token: str, request_id: str) -> Result:
        """Helper marks an escort journey as completed."""
        user = self._auth_user(token, AccountType.HELPER)
        if not user:
            return Result(success=False, message="Not authenticated as a helper.")

        with self._request_lock:
            req = self._requests.get(request_id)
            if not req:
                return Result(success=False, message="Request not found.")
            if req.helper_id != user.user_id:
                return Result(success=False, message="You did not accept this request.")
            if req.status != RequestStatus.ACCEPTED:
                return Result(success=False, message="Request is not in accepted state.")
            req.status       = RequestStatus.COMPLETED
            req.completed_at = datetime.utcnow()
            student_id       = req.student_id

        self._add_notification(
            recipient_id=student_id,
            title="Journey completed",
            body="Your escort has marked this journey as completed. Stay safe!",
            request_id=request_id,
        )
        return Result(success=True, message="Request marked as completed.",
                      data={"request_id": request_id})

    def get_accepted_request_view(
        self, token: str, request_id: str
    ) -> Optional[AcceptedRequestView]:
        """
        Return the privacy-safe detail view of a request the helper accepted.

        Only the student's username is revealed — no email, phone, or student_id.
        """
        user = self._auth_user(token, AccountType.HELPER)
        if not user:
            return None

        with self._request_lock:
            req = self._requests.get(request_id)
        if not req or req.helper_id != user.user_id:
            return None
        if req.status != RequestStatus.ACCEPTED:
            return None

        with self._user_lock:
            student = self._users.get(req.student_id)
        student_username = student.username if student else "unknown"

        return AcceptedRequestView(
            request_id=req.request_id,
            location=req.location,
            destination=req.destination,
            leaving_time=req.leaving_time,
            notes=req.notes,
            student_username=student_username,
            accepted_at=req.accepted_at,
        )

    def get_helper_requests(self, token: str) -> List[HelpRequest]:
        """Return all requests a helper has been involved in."""
        user = self._auth_user(token, AccountType.HELPER)
        if not user:
            return []
        with self._request_lock:
            return [r for r in self._requests.values()
                    if r.helper_id == user.user_id]

    # ═════════════════════════════════════════════════════════════════
    #  NOTIFICATIONS
    # ═════════════════════════════════════════════════════════════════

    def get_notifications(
        self, token: str, unread_only: bool = False
    ) -> List[Notification]:
        """Return a user's notifications, newest first."""
        user = self._resolve_token(token)
        if not user:
            return []
        with self._notif_lock:
            notifs = [n for n in self._notifications.values()
                      if n.recipient_id == user.user_id]
            if unread_only:
                notifs = [n for n in notifs if not n.read]
        return sorted(notifs, key=lambda n: n.created_at, reverse=True)

    def mark_notification_read(self, token: str, notification_id: str) -> Result:
        """Mark one of the authenticated user's notifications as read."""
        user = self._resolve_token(token)
        if not user:
            return Result(success=False, message="Not authenticated.")
        with self._notif_lock:
            notif = self._notifications.get(notification_id)
            if not notif or notif.recipient_id != user.user_id:
                return Result(success=False, message="Notification not found.")
            notif.read = True
        return Result(success=True, message="Marked as read.")

    # ═════════════════════════════════════════════════════════════════
    #  CHAT
    # ═════════════════════════════════════════════════════════════════

    def send_message(self, token: str, request_id: str, content: str) -> Result:
        """
        Send a chat message on a request thread.

        Access rules:
        - Only the student and the accepted helper may participate.
        - Chat is only open while the request is ACCEPTED.
        - Messages are stored immediately with no queuing delay.
        """
        user = self._resolve_token(token)
        if not user:
            return Result(success=False, message="Not authenticated.")
        if not content or not content.strip():
            return Result(success=False, message="Message cannot be empty.")

        with self._request_lock:
            req = self._requests.get(request_id)
            if not req:
                return Result(success=False, message="Request not found.")
            if req.status != RequestStatus.ACCEPTED:
                return Result(success=False,
                              message="Chat is only available for accepted requests.")
            if user.user_id not in (req.student_id, req.helper_id):
                return Result(success=False,
                              message="You are not a participant in this request.")

        msg = ChatMessage(
            message_id=self._new_id(),
            request_id=request_id,
            sender_id=user.user_id,
            content=content.strip(),
        )
        with self._notif_lock:
            self._messages[msg.message_id] = msg

        return Result(success=True, message="Message sent.",
                      data={"message_id": msg.message_id})

    def get_messages(self, token: str, request_id: str) -> List[ChatMessage]:
        """
        Return all messages for a request in chronological order.

        Only the student or the matched helper may read the thread.
        """
        user = self._resolve_token(token)
        if not user:
            return []
        with self._request_lock:
            req = self._requests.get(request_id)
        if not req or user.user_id not in (req.student_id, req.helper_id):
            return []
        with self._notif_lock:
            msgs = [m for m in self._messages.values()
                    if m.request_id == request_id]
        return sorted(msgs, key=lambda m: m.sent_at)

    def get_recent_messages(
        self, token: str, request_id: str, limit: int = 20
    ) -> List[ChatMessage]:
        """Return the most recent `limit` messages in a chat thread."""
        all_msgs = self.get_messages(token, request_id)
        return all_msgs[-limit:] if len(all_msgs) > limit else all_msgs

    # ═════════════════════════════════════════════════════════════════
    #  LIFECYCLE
    # ═════════════════════════════════════════════════════════════════

    def shutdown(self) -> None:
        """Gracefully stop background notification threads."""
        self._executor.shutdown(wait=True)

    # ═════════════════════════════════════════════════════════════════
    #  PRIVATE HELPERS
    # ═════════════════════════════════════════════════════════════════

    # ── identity & sessions ──────────────────────────────────────────

    def _new_id(self) -> str:
        return str(uuid.uuid4())

    def _resolve_token(self, token: str) -> Optional[User]:
        """Return the User for a valid session token, else None."""
        with self._user_lock:
            uid  = self._sessions.get(token)
            return self._users.get(uid) if uid else None

    def _auth_user(
        self, token: str, required_type: AccountType
    ) -> Optional[User]:
        """Resolve token and enforce account type."""
        user = self._resolve_token(token)
        if not user or user.account_type != required_type or not user.is_active:
            return None
        return user

    def _create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._user_lock:
            self._sessions[token] = user_id
        return token

    # ── password hashing (PBKDF2-HMAC-SHA256) ───────────────────────

    def _hash_password(self, password: str) -> str:
        salt = os.urandom(32)
        dk   = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, self._PBKDF2_ITERATIONS
        )
        return salt.hex() + ":" + dk.hex()

    def _verify_password(self, password: str, stored: str) -> bool:
        try:
            salt_hex, dk_hex = stored.split(":")
            salt = bytes.fromhex(salt_hex)
            dk   = bytes.fromhex(dk_hex)
        except ValueError:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, self._PBKDF2_ITERATIONS
        )
        return hmac.compare_digest(candidate, dk)

    def _validate_credentials(self, username: str, password: str) -> str:
        """Return an error message, or empty string if valid."""
        if not username or len(username) < 3:
            return "Username must be at least 3 characters."
        with self._user_lock:
            if username in self._usernames:
                return "Username already taken."
        if not password or len(password) < 8:
            return "Password must be at least 8 characters."
        return ""

    # ── collision detection ──────────────────────────────────────────

    def _find_collision(
        self, helper_id: str, new_req: HelpRequest
    ) -> Optional[HelpRequest]:
        """
        Return an existing ACCEPTED request that time-overlaps `new_req`,
        or None if there is no conflict.

        Each request occupies [leaving_time − buffer, leaving_time + buffer].
        Two requests collide when their windows intersect.

        Must be called while holding self._request_lock.
        """
        buf       = self._COLLISION_BUFFER
        new_start = new_req.leaving_time - buf
        new_end   = new_req.leaving_time + buf

        for r in self._requests.values():
            if r.helper_id != helper_id or r.status != RequestStatus.ACCEPTED:
                continue
            if new_start < (r.leaving_time + buf) and new_end > (r.leaving_time - buf):
                return r
        return None

    # ── notifications ────────────────────────────────────────────────

    def _add_notification(
        self,
        recipient_id: str,
        title:        str,
        body:         str,
        request_id:   Optional[str] = None,
    ) -> None:
        notif = Notification(
            notification_id=self._new_id(),
            recipient_id=recipient_id,
            title=title,
            body=body,
            request_id=request_id,
        )
        with self._notif_lock:
            self._notifications[notif.notification_id] = notif

    def _notify_helpers_new_request(
        self, request_id: str, location: str, destination: str
    ) -> None:
        """Fan-out a notification to every active helper (runs in thread pool)."""
        with self._user_lock:
            helpers = [u for u in self._users.values()
                       if u.account_type == AccountType.HELPER and u.is_active]

        title = "New Help Request Available"
        body  = (f"A student needs an escort from '{location}' "
                 f"to '{destination}'.")

        for helper in helpers:
            self._executor.submit(
                self._add_notification,
                helper.user_id, title, body, request_id,
            )