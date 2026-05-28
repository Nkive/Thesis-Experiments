"""
Campus Safety Escort System
============================
A functional-programming implementation of a student safety escort
matching platform for university campuses.

Design principles
-----------------
* Pure functions wherever side effects are not required.
* Immutable data via frozen dataclasses and NamedTuples.
* Higher-order functions, function composition, and partial application.
* Explicit state threading instead of shared mutable state.
* asyncio for concurrency; asyncio.Queue for back-pressure.
* Privacy by design: helpers never receive raw student PII.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from functools import partial, reduce
from typing import Any, Final, FrozenSet, NamedTuple, Optional, TypeVar

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log: Final = logging.getLogger("campus_safety")

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

StudentId = str
HelperId = str
RequestId = str
Token = str
Timestamp = float

T = TypeVar("T")
A = TypeVar("A")
B = TypeVar("B")

# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------


class RequestStatus(Enum):
    PENDING = auto()
    MATCHED = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    EXPIRED = auto()


class HelperStatus(Enum):
    AVAILABLE = auto()
    BUSY = auto()
    OFFLINE = auto()


# ---------------------------------------------------------------------------
# Immutable value objects
# ---------------------------------------------------------------------------


class Location(NamedTuple):
    """A campus location expressed as a human-readable building name and an
    optional room / entrance hint.  No GPS coordinates are stored."""

    building: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.building} ({self.detail})" if self.detail else self.building


@dataclass(frozen=True)
class EscortRequest:
    """Immutable escort request submitted by a student."""

    request_id: RequestId
    student_token: Token          # one-way token; never the real student ID
    origin: Location
    destination: Location
    requested_at: Timestamp
    status: RequestStatus = RequestStatus.PENDING
    assigned_helper_id: Optional[HelperId] = None
    expires_at: Optional[Timestamp] = None


@dataclass(frozen=True)
class HelperProfile:
    """Immutable profile of a registered campus helper / volunteer."""

    helper_id: HelperId
    display_name: str             # first name + last initial only
    status: HelperStatus = HelperStatus.AVAILABLE
    active_request_id: Optional[RequestId] = None


@dataclass(frozen=True)
class HelperView:
    """Privacy-safe projection of an EscortRequest shown to a helper.
    Sensitive student identity is excluded."""

    request_id: RequestId
    origin: Location
    destination: Location
    requested_at: Timestamp


@dataclass(frozen=True)
class Message:
    """An in-app message exchanged between a student and a helper."""

    message_id: str
    sender_token: Token
    recipient_token: Token
    body: str
    sent_at: Timestamp


# ---------------------------------------------------------------------------
# System state  (immutable snapshot, threaded explicitly)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemState:
    """Complete, immutable snapshot of the escort system at one point in time.

    All mutations produce a *new* SystemState rather than modifying this one.
    """

    requests: dict[RequestId, EscortRequest] = field(default_factory=dict)
    helpers: dict[HelperId, HelperProfile] = field(default_factory=dict)
    messages: tuple[Message, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Token / anonymisation utilities  (pure)
# ---------------------------------------------------------------------------

_HMAC_KEY: Final[bytes] = secrets.token_bytes(32)  # generated once at import time


def anonymise_student_id(student_id: StudentId) -> Token:
    """Return a stable, one-way HMAC token for a student ID.

    The raw student ID never leaves this function; helpers only see the token.
    """
    return hmac.new(_HMAC_KEY, student_id.encode(), hashlib.sha256).hexdigest()


def generate_request_id() -> RequestId:
    """Return a collision-resistant UUID4 request identifier."""
    return str(uuid.uuid4())


def current_timestamp() -> Timestamp:
    """Return the current POSIX timestamp."""
    return time.time()


# ---------------------------------------------------------------------------
# Pure state-transition functions
# ---------------------------------------------------------------------------


def register_helper(state: SystemState, helper: HelperProfile) -> SystemState:
    """Return a new state with *helper* added to the helper registry."""
    updated_helpers = {**state.helpers, helper.helper_id: helper}
    return replace(state, helpers=updated_helpers)


def submit_request(
    state: SystemState,
    student_id: StudentId,
    origin: Location,
    destination: Location,
    ttl_seconds: float = 1800.0,
) -> tuple[SystemState, EscortRequest]:
    """Create an escort request and return the updated state plus the request.

    The student's raw ID is anonymised before being stored.
    """
    now = current_timestamp()
    request = EscortRequest(
        request_id=generate_request_id(),
        student_token=anonymise_student_id(student_id),
        origin=origin,
        destination=destination,
        requested_at=now,
        expires_at=now + ttl_seconds,
    )
    updated_requests = {**state.requests, request.request_id: request}
    new_state = replace(state, requests=updated_requests)
    log.info("Request %s submitted: %s → %s", request.request_id[:8], origin, destination)
    return new_state, request


def cancel_request(
    state: SystemState, request_id: RequestId, student_id: StudentId
) -> SystemState:
    """Cancel a pending request if it belongs to the requesting student."""
    request = state.requests.get(request_id)
    if request is None:
        log.warning("cancel_request: unknown request %s", request_id)
        return state

    if request.student_token != anonymise_student_id(student_id):
        log.warning("cancel_request: student does not own request %s", request_id)
        return state

    if request.status not in (RequestStatus.PENDING, RequestStatus.MATCHED):
        log.warning("cancel_request: request %s cannot be cancelled in status %s",
                    request_id, request.status)
        return state

    updated = replace(request, status=RequestStatus.CANCELLED)
    updated_requests = {**state.requests, request_id: updated}

    # Release the helper if one was assigned
    new_helpers = state.helpers
    if request.assigned_helper_id:
        helper = state.helpers.get(request.assigned_helper_id)
        if helper:
            freed = replace(helper, status=HelperStatus.AVAILABLE, active_request_id=None)
            new_helpers = {**state.helpers, helper.helper_id: freed}

    return replace(state, requests=updated_requests, helpers=new_helpers)


def _is_request_available(request: EscortRequest, now: Timestamp) -> bool:
    """Return True when a request can still be matched."""
    if request.status != RequestStatus.PENDING:
        return False
    if request.expires_at is not None and now > request.expires_at:
        return False
    return True


def expire_requests(state: SystemState) -> SystemState:
    """Mark all overdue PENDING requests as EXPIRED and return the new state."""
    now = current_timestamp()

    def _maybe_expire(req: EscortRequest) -> EscortRequest:
        if (
            req.status == RequestStatus.PENDING
            and req.expires_at is not None
            and now > req.expires_at
        ):
            log.info("Request %s expired.", req.request_id[:8])
            return replace(req, status=RequestStatus.EXPIRED)
        return req

    updated_requests = {rid: _maybe_expire(r) for rid, r in state.requests.items()}
    return replace(state, requests=updated_requests)


def _first_available_helper(
    helpers: dict[HelperId, HelperProfile],
    excluded: FrozenSet[HelperId] = frozenset(),
) -> Optional[HelperProfile]:
    """Return the first available helper not in *excluded*, or None."""
    return next(
        (
            h
            for h in helpers.values()
            if h.status == HelperStatus.AVAILABLE and h.helper_id not in excluded
        ),
        None,
    )


def assign_helper(
    state: SystemState, request_id: RequestId
) -> tuple[SystemState, Optional[HelperId]]:
    """Attempt to assign an available helper to a pending request.

    Returns the updated state and the assigned helper ID (or None).
    """
    request = state.requests.get(request_id)
    if request is None or not _is_request_available(request, current_timestamp()):
        return state, None

    helper = _first_available_helper(state.helpers)
    if helper is None:
        log.info("No helpers available for request %s.", request_id[:8])
        return state, None

    matched_request = replace(
        request,
        status=RequestStatus.MATCHED,
        assigned_helper_id=helper.helper_id,
    )
    busy_helper = replace(
        helper,
        status=HelperStatus.BUSY,
        active_request_id=request_id,
    )

    new_state = replace(
        state,
        requests={**state.requests, request_id: matched_request},
        helpers={**state.helpers, helper.helper_id: busy_helper},
    )
    log.info(
        "Helper %s assigned to request %s.",
        helper.display_name,
        request_id[:8],
    )
    return new_state, helper.helper_id


def start_escort(state: SystemState, request_id: RequestId) -> SystemState:
    """Transition a MATCHED request to IN_PROGRESS."""
    request = state.requests.get(request_id)
    if request is None or request.status != RequestStatus.MATCHED:
        return state
    updated = replace(request, status=RequestStatus.IN_PROGRESS)
    return replace(state, requests={**state.requests, request_id: updated})


def complete_escort(state: SystemState, request_id: RequestId) -> SystemState:
    """Mark an IN_PROGRESS escort as COMPLETED and free the helper."""
    request = state.requests.get(request_id)
    if request is None or request.status != RequestStatus.IN_PROGRESS:
        return state

    completed_request = replace(request, status=RequestStatus.COMPLETED)
    new_requests = {**state.requests, request_id: completed_request}

    new_helpers = state.helpers
    if request.assigned_helper_id:
        helper = state.helpers.get(request.assigned_helper_id)
        if helper:
            freed = replace(helper, status=HelperStatus.AVAILABLE, active_request_id=None)
            new_helpers = {**state.helpers, helper.helper_id: freed}

    log.info("Escort for request %s completed.", request_id[:8])
    return replace(state, requests=new_requests, helpers=new_helpers)


# ---------------------------------------------------------------------------
# Privacy projection  (pure)
# ---------------------------------------------------------------------------


def project_request_for_helper(request: EscortRequest) -> HelperView:
    """Return a helper-safe view of a request (no student PII)."""
    return HelperView(
        request_id=request.request_id,
        origin=request.origin,
        destination=request.destination,
        requested_at=request.requested_at,
    )


def list_pending_views(state: SystemState) -> tuple[HelperView, ...]:
    """Return helper-safe projections of all PENDING requests."""
    now = current_timestamp()
    return tuple(
        project_request_for_helper(r)
        for r in state.requests.values()
        if _is_request_available(r, now)
    )


# ---------------------------------------------------------------------------
# Messaging  (pure)
# ---------------------------------------------------------------------------


def send_message(
    state: SystemState,
    sender_id: StudentId | HelperId,
    recipient_id: StudentId | HelperId,
    body: str,
    *,
    anonymise_sender: bool = True,
) -> tuple[SystemState, Message]:
    """Append an in-app message to the system state.

    When *anonymise_sender* is True the sender's raw ID is tokenised so
    helpers cannot identify students from their messages.
    """
    sender_token = anonymise_student_id(sender_id) if anonymise_sender else sender_id
    recipient_token = anonymise_student_id(recipient_id) if anonymise_sender else recipient_id

    message = Message(
        message_id=str(uuid.uuid4()),
        sender_token=sender_token,
        recipient_token=recipient_token,
        body=body,
        sent_at=current_timestamp(),
    )
    new_messages = state.messages + (message,)
    return replace(state, messages=new_messages), message


def get_messages_for_token(state: SystemState, token: Token) -> tuple[Message, ...]:
    """Return all messages addressed to the given token."""
    return tuple(m for m in state.messages if m.recipient_token == token)


# ---------------------------------------------------------------------------
# Higher-order / composition utilities  (pure)
# ---------------------------------------------------------------------------


def compose(*fns: Callable[[T], T]) -> Callable[[T], T]:
    """Right-to-left function composition: compose(f, g)(x) == f(g(x))."""
    return reduce(lambda f, g: lambda x: f(g(x)), fns)


def pipe(value: T, *fns: Callable[[T], T]) -> T:
    """Thread *value* through a sequence of functions left-to-right."""
    return reduce(lambda v, f: f(v), fns, value)


def retry(
    fn: Callable[..., T],
    *,
    attempts: int = 3,
    delay: float = 0.5,
    predicate: Callable[[T], bool] = bool,
) -> Callable[..., Optional[T]]:
    """Return a wrapper that retries *fn* up to *attempts* times (sync).

    *predicate* determines whether the result is considered successful.
    """

    def _wrapper(*args: Any, **kwargs: Any) -> Optional[T]:
        for attempt in range(1, attempts + 1):
            result = fn(*args, **kwargs)
            if predicate(result):
                return result
            log.debug("retry: attempt %d/%d failed.", attempt, attempts)
            time.sleep(delay)
        return None

    return _wrapper


def map_state(
    transform: Callable[[EscortRequest], EscortRequest],
) -> Callable[[SystemState], SystemState]:
    """Lift a request-level transform into a full state transform."""

    def _apply(state: SystemState) -> SystemState:
        updated = {rid: transform(r) for rid, r in state.requests.items()}
        return replace(state, requests=updated)

    return _apply


# ---------------------------------------------------------------------------
# Concurrency layer  (async, functional-style)
# ---------------------------------------------------------------------------


@dataclass
class EscortSystem:
    """Async façade that serialises state mutations through a single asyncio.Lock.

    The internal *_state* field is the only mutable reference in the whole
    system; every mutation replaces it with a new immutable SystemState.
    """

    _state: SystemState = field(default_factory=SystemState)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _request_queue: asyncio.Queue[RequestId] = field(
        default_factory=lambda: asyncio.Queue(maxsize=256)
    )
    _running: bool = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _mutate(
        self, fn: Callable[[SystemState], SystemState | tuple[SystemState, Any]]
    ) -> Any:
        """Apply *fn* to the current state under the lock.

        If *fn* returns a (state, value) tuple the value is forwarded;
        otherwise the new state itself is returned.
        """
        async with self._lock:
            result = fn(self._state)
            if isinstance(result, tuple):
                self._state, value = result
                return value
            self._state = result
            return self._state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register_helper(self, helper: HelperProfile) -> None:
        await self._mutate(partial(register_helper, helper=helper))
        log.info("Helper registered: %s (%s)", helper.display_name, helper.helper_id[:8])

    async def submit_request(
        self,
        student_id: StudentId,
        origin: Location,
        destination: Location,
    ) -> EscortRequest:
        result: tuple[SystemState, EscortRequest] = await self._mutate(
            lambda s: submit_request(s, student_id, origin, destination)
        )
        # The mutate helper unwraps the tuple; result is already the request.
        # But we need both state and request, so capture via closure:
        request_holder: list[EscortRequest] = []

        async with self._lock:
            self._state, req = submit_request(
                self._state, student_id, origin, destination
            )
            request_holder.append(req)

        req = request_holder[0]
        await self._request_queue.put(req.request_id)
        return req

    async def cancel_request(
        self, request_id: RequestId, student_id: StudentId
    ) -> None:
        await self._mutate(
            lambda s: cancel_request(s, request_id, student_id)
        )

    async def list_pending(self) -> tuple[HelperView, ...]:
        async with self._lock:
            return list_pending_views(self._state)

    async def start_escort(self, request_id: RequestId) -> None:
        await self._mutate(lambda s: start_escort(s, request_id))
        log.info("Escort started for request %s.", request_id[:8])

    async def complete_escort(self, request_id: RequestId) -> None:
        await self._mutate(lambda s: complete_escort(s, request_id))

    async def send_message(
        self,
        sender_id: str,
        recipient_id: str,
        body: str,
        *,
        anonymise_sender: bool = True,
    ) -> Message:
        msg_holder: list[Message] = []

        async with self._lock:
            self._state, msg = send_message(
                self._state,
                sender_id,
                recipient_id,
                body,
                anonymise_sender=anonymise_sender,
            )
            msg_holder.append(msg)

        return msg_holder[0]

    async def get_messages(self, user_id: str) -> tuple[Message, ...]:
        token = anonymise_student_id(user_id)
        async with self._lock:
            return get_messages_for_token(self._state, token)

    async def snapshot(self) -> SystemState:
        async with self._lock:
            return self._state

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _matcher_loop(self) -> None:
        """Continuously dequeue request IDs and attempt to assign helpers."""
        log.info("Matcher loop started.")
        while self._running:
            try:
                request_id = await asyncio.wait_for(
                    self._request_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                # Run housekeeping while idle
                await self._mutate(expire_requests)
                continue

            async with self._lock:
                self._state, helper_id = assign_helper(self._state, request_id)

            if helper_id is None:
                # Re-enqueue after a short back-off so the request gets
                # another chance when a helper becomes available.
                await asyncio.sleep(2.0)
                try:
                    self._request_queue.put_nowait(request_id)
                except asyncio.QueueFull:
                    log.warning(
                        "Queue full; dropping re-enqueue of request %s.",
                        request_id[:8],
                    )

    async def start(self) -> None:
        """Start background tasks."""
        self._running = True
        asyncio.create_task(self._matcher_loop(), name="matcher_loop")
        log.info("EscortSystem started.")

    async def stop(self) -> None:
        """Signal background tasks to stop."""
        self._running = False
        log.info("EscortSystem stopped.")


# ---------------------------------------------------------------------------
# Query / reporting helpers  (pure, higher-order)
# ---------------------------------------------------------------------------


def filter_requests(
    predicate: Callable[[EscortRequest], bool],
) -> Callable[[SystemState], tuple[EscortRequest, ...]]:
    """Return a state query that filters requests by *predicate*."""

    def _query(state: SystemState) -> tuple[EscortRequest, ...]:
        return tuple(r for r in state.requests.values() if predicate(r))

    return _query


def count_by_status(state: SystemState) -> dict[RequestStatus, int]:
    """Return a frequency map of request statuses."""
    return reduce(
        lambda acc, req: {**acc, req.status: acc.get(req.status, 0) + 1},
        state.requests.values(),
        {},
    )


def available_helper_count(state: SystemState) -> int:
    """Return the number of currently available helpers."""
    return sum(
        1 for h in state.helpers.values() if h.status == HelperStatus.AVAILABLE
    )


# Partially applied, reusable queries
pending_requests: Callable[[SystemState], tuple[EscortRequest, ...]] = filter_requests(
    lambda r: r.status == RequestStatus.PENDING
)

active_requests: Callable[[SystemState], tuple[EscortRequest, ...]] = filter_requests(
    lambda r: r.status == RequestStatus.IN_PROGRESS
)

completed_requests: Callable[[SystemState], tuple[EscortRequest, ...]] = filter_requests(
    lambda r: r.status == RequestStatus.COMPLETED
)


# ---------------------------------------------------------------------------
# Demo / smoke-test  (async entry point)
# ---------------------------------------------------------------------------


async def _run_demo() -> None:
    """
    End-to-end demonstration of the escort system.

    Illustrates:
    - Helper registration
    - Student submitting a request
    - Automatic matching
    - Escort lifecycle (start → complete)
    - Messaging with sender anonymisation
    - State queries and reporting
    """
    system = EscortSystem()
    await system.start()

    # --- Register helpers ---------------------------------------------------
    helpers = [
        HelperProfile(helper_id=f"H{i:03d}", display_name=f"Volunteer {i}")
        for i in range(1, 4)
    ]
    for h in helpers:
        await system.register_helper(h)

    # --- Students submit escort requests ------------------------------------
    library = Location("Main Library", "South exit")
    dormitory = Location("Oak Hall Dormitory", "Front entrance")
    science_block = Location("Science Block B", "Lobby")
    sports_centre = Location("Sports Centre", "Parking side")

    req_a = await system.submit_request("student_alice_001", library, dormitory)
    req_b = await system.submit_request("student_bob_002", science_block, sports_centre)
    req_c = await system.submit_request(
        "student_carol_003",
        Location("Engineering Annex"),
        dormitory,
    )

    # Give the matcher loop time to process the queue
    await asyncio.sleep(0.5)

    # --- Inspect pending views (helper perspective) -------------------------
    pending = await system.list_pending()
    log.info("Pending requests visible to helpers: %d", len(pending))
    for view in pending:
        log.info("  • %s → %s  (id: %s)", view.origin, view.destination, view.request_id[:8])

    # --- Lifecycle for request A -------------------------------------------
    await system.start_escort(req_a.request_id)
    await asyncio.sleep(0.1)
    await system.complete_escort(req_a.request_id)

    # --- Messaging (student → helper, anonymised) ---------------------------
    snap_before_msg = await system.snapshot()
    matched_req_b = snap_before_msg.requests.get(req_b.request_id)

    if matched_req_b and matched_req_b.assigned_helper_id:
        msg = await system.send_message(
            sender_id="student_bob_002",
            recipient_id=matched_req_b.assigned_helper_id,
            body="I'm at the main entrance of Science Block B.",
            anonymise_sender=True,
        )
        log.info(
            "Message sent: sender_token=%s…  body=%r",
            msg.sender_token[:12],
            msg.body,
        )

    # --- Cancel an unmatched or pending request ----------------------------
    await system.cancel_request(req_c.request_id, "student_carol_003")

    # --- Final state report -------------------------------------------------
    final_state = await system.snapshot()
    status_counts = count_by_status(final_state)
    log.info("=== Final Status Report ===")
    for status, count in sorted(status_counts.items(), key=lambda kv: kv[0].name):
        log.info("  %-15s : %d", status.name, count)
    log.info(
        "Available helpers : %d / %d",
        available_helper_count(final_state),
        len(final_state.helpers),
    )

    await system.stop()


if __name__ == "__main__":
    asyncio.run(_run_demo())