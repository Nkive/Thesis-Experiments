"""
Campus Safety Walk Companion - Core Python Functions
Covers: request management, helper matching, concurrency, privacy, messaging
"""

import uuid
import hashlib
import threading
import queue
import time
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────
# ENUMS & DATA MODELS
# ──────────────────────────────────────────────

class RequestStatus(Enum):
    PENDING    = "pending"
    MATCHED    = "matched"
    IN_PROGRESS = "in_progress"
    COMPLETED  = "completed"
    CANCELLED  = "cancelled"
    EXPIRED    = "expired"


class HelperStatus(Enum):
    AVAILABLE   = "available"
    BUSY        = "busy"
    OFFLINE     = "offline"


@dataclass
class WalkRequest:
    request_id: str
    student_id: str          # real ID, kept server-side only
    anonymous_token: str     # shared with helpers instead of real ID
    origin: str
    destination: str
    scheduled_time: datetime
    status: RequestStatus = RequestStatus.PENDING
    assigned_helper_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    notes: str = ""          # optional public note, no PII


@dataclass
class Helper:
    helper_id: str
    display_name: str        # first name / alias only
    status: HelperStatus = HelperStatus.AVAILABLE
    current_request_id: Optional[str] = None
    rating: float = 5.0
    total_walks: int = 0


@dataclass
class Message:
    message_id: str
    request_id: str
    sender_token: str        # anonymous token, never real ID
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    is_system: bool = False


# ──────────────────────────────────────────────
# PRIVACY UTILITIES
# ──────────────────────────────────────────────

def generate_anonymous_token(student_id: str, request_id: str) -> str:
    """
    Create a one-way anonymous token for a student scoped to a single request.
    Helpers only ever see this token, never the real student ID.
    """
    raw = f"{student_id}:{request_id}:campus_safety_salt_2024"
    return "anon_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def sanitize_location(raw_location: str) -> str:
    """
    Strip any accidentally included PII (email, phone, student number)
    from a free-text location string before storing or broadcasting.
    """
    import re
    # Remove email addresses
    clean = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[removed]", raw_location)
    # Remove phone-like patterns
    clean = re.sub(r"\b(\+?\d[\d\s\-().]{7,14}\d)\b", "[removed]", clean)
    # Remove student ID patterns (6-10 consecutive digits)
    clean = re.sub(r"\b\d{6,10}\b", "[removed]", clean)
    return clean.strip()


def build_helper_visible_request(request: WalkRequest) -> dict:
    """
    Return only the fields a helper is allowed to see.
    Omits student_id and any other sensitive internal fields.
    """
    return {
        "request_id":      request.request_id,
        "anonymous_token": request.anonymous_token,
        "origin":          request.origin,
        "destination":     request.destination,
        "scheduled_time":  request.scheduled_time.isoformat(),
        "status":          request.status.value,
        "notes":           request.notes,
    }


# ──────────────────────────────────────────────
# REQUEST MANAGEMENT
# ──────────────────────────────────────────────

def create_walk_request(
    student_id: str,
    origin: str,
    destination: str,
    scheduled_time: datetime,
    notes: str = "",
    ttl_minutes: int = 30,
) -> WalkRequest:
    """
    Create and return a new WalkRequest with sanitized locations and
    an anonymous token that hides the student's real identity from helpers.
    """
    request_id  = str(uuid.uuid4())
    anon_token  = generate_anonymous_token(student_id, request_id)
    clean_orig  = sanitize_location(origin)
    clean_dest  = sanitize_location(destination)

    return WalkRequest(
        request_id       = request_id,
        student_id       = student_id,
        anonymous_token  = anon_token,
        origin           = clean_orig,
        destination      = clean_dest,
        scheduled_time   = scheduled_time,
        notes            = notes,
        expires_at       = datetime.now() + timedelta(minutes=ttl_minutes),
    )


def cancel_walk_request(request: WalkRequest, reason: str = "") -> bool:
    """
    Cancel a pending or matched request.
    Returns False if the request is already in progress or terminal.
    """
    cancellable = {RequestStatus.PENDING, RequestStatus.MATCHED}
    if request.status not in cancellable:
        return False
    request.status = RequestStatus.CANCELLED
    return True


def expire_stale_requests(requests: list[WalkRequest]) -> list[WalkRequest]:
    """
    Mark any unmatched requests whose TTL has passed as EXPIRED.
    Returns the list of requests that were expired.
    """
    now = datetime.now()
    expired = []
    for req in requests:
        if (
            req.status == RequestStatus.PENDING
            and req.expires_at
            and now > req.expires_at
        ):
            req.status = RequestStatus.EXPIRED
            expired.append(req)
    return expired


def complete_walk_request(request: WalkRequest) -> bool:
    """Mark an in-progress walk as completed."""
    if request.status != RequestStatus.IN_PROGRESS:
        return False
    request.status = RequestStatus.COMPLETED
    return True


# ──────────────────────────────────────────────
# HELPER MANAGEMENT
# ──────────────────────────────────────────────

def register_helper(display_name: str) -> Helper:
    """Create a new helper profile."""
    return Helper(
        helper_id    = str(uuid.uuid4()),
        display_name = display_name,
    )


def set_helper_availability(helper: Helper, available: bool) -> None:
    """Toggle a helper's availability; busy helpers stay busy until released."""
    if helper.status == HelperStatus.BUSY:
        return  # must finish current walk first
    helper.status = HelperStatus.AVAILABLE if available else HelperStatus.OFFLINE


def get_available_helpers(helpers: list[Helper]) -> list[Helper]:
    """Return all helpers currently available to accept requests."""
    return [h for h in helpers if h.status == HelperStatus.AVAILABLE]


def update_helper_rating(helper: Helper, new_rating: float) -> None:
    """
    Rolling average rating update after each completed walk.
    new_rating must be between 1.0 and 5.0.
    """
    if not (1.0 <= new_rating <= 5.0):
        raise ValueError("Rating must be between 1.0 and 5.0")
    n = helper.total_walks
    helper.rating = ((helper.rating * n) + new_rating) / (n + 1)
    helper.total_walks += 1


# ──────────────────────────────────────────────
# MATCHING ENGINE
# ──────────────────────────────────────────────

def match_helper_to_request(
    request: WalkRequest,
    available_helpers: list[Helper],
) -> Optional[Helper]:
    """
    Assign the best available helper to a pending request.
    Selection priority: highest rating → most total walks (experience).
    Returns the matched helper, or None if no one is available.
    """
    if request.status != RequestStatus.PENDING:
        return None

    candidates = [h for h in available_helpers if h.status == HelperStatus.AVAILABLE]
    if not candidates:
        return None

    best = max(candidates, key=lambda h: (h.rating, h.total_walks))
    best.status             = HelperStatus.BUSY
    best.current_request_id = request.request_id
    request.assigned_helper_id = best.helper_id
    request.status          = RequestStatus.MATCHED
    return best


def release_helper(helper: Helper) -> None:
    """Free a helper after a walk ends (completed or cancelled)."""
    helper.status             = HelperStatus.AVAILABLE
    helper.current_request_id = None


def handle_multiple_helper_responses(
    request: WalkRequest,
    responding_helpers: list[Helper],
) -> tuple[Optional[Helper], list[Helper]]:
    """
    When multiple helpers respond to the same request, pick one and
    notify the rest they were not selected.
    Returns (assigned_helper, rejected_helpers).
    """
    if not responding_helpers or request.status != RequestStatus.PENDING:
        return None, responding_helpers

    # Sort: highest rating first, then most experienced
    sorted_helpers = sorted(
        responding_helpers,
        key=lambda h: (h.rating, h.total_walks),
        reverse=True,
    )
    chosen   = sorted_helpers[0]
    rejected = sorted_helpers[1:]

    chosen.status             = HelperStatus.BUSY
    chosen.current_request_id = request.request_id
    request.assigned_helper_id = chosen.helper_id
    request.status            = RequestStatus.MATCHED

    return chosen, rejected


# ──────────────────────────────────────────────
# MESSAGING (privacy-safe, anonymous)
# ──────────────────────────────────────────────

def send_message(
    request_id: str,
    sender_token: str,
    content: str,
    is_system: bool = False,
) -> Message:
    """
    Create a message tied to a request. Both student and helper
    communicate via their anonymous tokens — no real IDs in messages.
    """
    if not content.strip():
        raise ValueError("Message content cannot be empty")
    return Message(
        message_id   = str(uuid.uuid4()),
        request_id   = request_id,
        sender_token = sender_token,
        content      = content.strip(),
        is_system    = is_system,
    )


def get_conversation(
    messages: list[Message],
    request_id: str,
) -> list[Message]:
    """Return all messages for a given request, ordered by timestamp."""
    return sorted(
        [m for m in messages if m.request_id == request_id],
        key=lambda m: m.timestamp,
    )


def send_system_notification(request_id: str, event: str) -> Message:
    """Generate a system-level notification message for a request lifecycle event."""
    return send_message(
        request_id   = request_id,
        sender_token = "system",
        content      = event,
        is_system    = True,
    )


# ──────────────────────────────────────────────
# CONCURRENCY — thread-safe request queue
# ──────────────────────────────────────────────

class RequestQueue:
    """
    Thread-safe queue for incoming walk requests.
    Ensures helpers are not overwhelmed and requests are processed
    in order even when many students submit simultaneously.
    """

    def __init__(self):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._lock  = threading.Lock()
        self._active: dict[str, WalkRequest] = {}

    def enqueue(self, request: WalkRequest, priority: int = 5) -> None:
        """
        Add a request to the queue.
        Lower priority number = processed first (urgent requests can use priority=1).
        """
        # PriorityQueue is ordered ascending; tie-break on creation time
        self._queue.put((priority, request.created_at, request))
        with self._lock:
            self._active[request.request_id] = request

    def dequeue(self, timeout: float = 5.0) -> Optional[WalkRequest]:
        """
        Retrieve the next pending request.
        Returns None if the queue is empty within the timeout window.
        """
        try:
            _, _, request = self._queue.get(timeout=timeout)
            return request
        except queue.Empty:
            return None

    def remove(self, request_id: str) -> bool:
        """Remove a specific request (e.g., cancelled before processing)."""
        with self._lock:
            if request_id in self._active:
                del self._active[request_id]
                return True
            return False

    def pending_count(self) -> int:
        """Return the number of requests currently waiting."""
        return self._queue.qsize()

    def get_all_active(self) -> list[WalkRequest]:
        """Snapshot of all active requests (for admin/dashboard use)."""
        with self._lock:
            return list(self._active.values())


def process_request_worker(
    rq: RequestQueue,
    helpers: list[Helper],
    processed_requests: list[WalkRequest],
    stop_event: threading.Event,
) -> None:
    """
    Background worker that continuously dequeues requests and tries to match
    them with available helpers. Designed to run in a dedicated thread.
    """
    while not stop_event.is_set():
        request = rq.dequeue(timeout=2.0)
        if request is None:
            continue

        available = get_available_helpers(helpers)
        matched   = match_helper_to_request(request, available)

        if matched:
            request.status = RequestStatus.IN_PROGRESS
            notify = send_system_notification(
                request.request_id,
                f"Helper '{matched.display_name}' is on the way.",
            )
        else:
            # Re-queue with a slight delay if no helper available yet
            time.sleep(1)
            if request.status == RequestStatus.PENDING:
                rq.enqueue(request)

        processed_requests.append(request)


def start_request_processor(
    rq: RequestQueue,
    helpers: list[Helper],
    processed_requests: list[WalkRequest],
) -> tuple[threading.Thread, threading.Event]:
    """
    Spawn the background processing thread.
    Returns (thread, stop_event) so the caller can shut it down cleanly.
    """
    stop_event = threading.Event()
    t = threading.Thread(
        target  = process_request_worker,
        args    = (rq, helpers, processed_requests, stop_event),
        daemon  = True,
    )
    t.start()
    return t, stop_event


# ──────────────────────────────────────────────
# REPORTING / ADMIN UTILITIES
# ──────────────────────────────────────────────

def get_requests_by_status(
    requests: list[WalkRequest],
    status: RequestStatus,
) -> list[WalkRequest]:
    """Filter requests by a given status."""
    return [r for r in requests if r.status == status]


def get_helper_history(
    requests: list[WalkRequest],
    helper_id: str,
) -> list[WalkRequest]:
    """Return all requests ever assigned to a specific helper."""
    return [r for r in requests if r.assigned_helper_id == helper_id]


def summarize_activity(
    requests: list[WalkRequest],
    helpers: list[Helper],
) -> dict:
    """
    High-level activity summary for an admin dashboard or logs.
    Returns aggregate counts — no PII included.
    """
    status_counts = {s.value: 0 for s in RequestStatus}
    for r in requests:
        status_counts[r.status.value] += 1

    return {
        "total_requests":     len(requests),
        "by_status":          status_counts,
        "total_helpers":      len(helpers),
        "available_helpers":  sum(1 for h in helpers if h.status == HelperStatus.AVAILABLE),
        "busy_helpers":       sum(1 for h in helpers if h.status == HelperStatus.BUSY),
        "avg_helper_rating":  (
            round(sum(h.rating for h in helpers) / len(helpers), 2)
            if helpers else 0.0
        ),
    }