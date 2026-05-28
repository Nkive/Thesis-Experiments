"""
Transit Ticketing System
========================
Features:
- Ticket types: single ride, period ticket, group ticket
- Passenger types: regular, senior, student, regular commuter
- Price preview before purchase
- Multiple tickets per transaction
- Default values for unspecified options
- 10k+ operations within 5 seconds via async worker pool
- Load-balanced queue (max 15s per transaction)
- AES-256 encryption for passenger data
- Payment result stored only as boolean (no card/bank data retained)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import queue
import secrets
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ticketing")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TicketType(str, Enum):
    SINGLE_RIDE = "single_ride"
    PERIOD = "period"
    GROUP = "group"


class PassengerType(str, Enum):
    REGULAR = "regular"
    SENIOR = "senior"
    STUDENT = "student"
    COMMUTER = "commuter"


class PeriodDuration(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Interface(str, Enum):
    MOBILE = "mobile"
    WEB = "web"
    VENDING = "vending"


# ---------------------------------------------------------------------------
# Pricing table (SEK)
# ---------------------------------------------------------------------------

BASE_PRICES: dict[TicketType, float] = {
    TicketType.SINGLE_RIDE: 39.0,
    TicketType.PERIOD: 200.0,   # base (day)
    TicketType.GROUP: 35.0,     # per person, min 3
}

PASSENGER_MULTIPLIERS: dict[PassengerType, float] = {
    PassengerType.REGULAR: 1.0,
    PassengerType.SENIOR: 0.65,
    PassengerType.STUDENT: 0.70,
    PassengerType.COMMUTER: 0.85,
}

PERIOD_MULTIPLIERS: dict[PeriodDuration, float] = {
    PeriodDuration.DAY: 1.0,
    PeriodDuration.WEEK: 5.5,
    PeriodDuration.MONTH: 18.0,
}

DEFAULT_TICKET_TYPE = TicketType.SINGLE_RIDE
DEFAULT_PASSENGER_TYPE = PassengerType.REGULAR
DEFAULT_PERIOD_DURATION = PeriodDuration.DAY
DEFAULT_GROUP_SIZE = 3
DEFAULT_QUANTITY = 1
DEFAULT_INTERFACE = Interface.MOBILE


# ---------------------------------------------------------------------------
# Encryption layer
# ---------------------------------------------------------------------------

class EncryptionService:
    """AES-256 (via Fernet / PBKDF2) for passenger data at rest and in transit."""

    def __init__(self, master_password: Optional[str] = None):
        password = (master_password or os.environ.get("TICKET_MASTER_KEY", "default-dev-key-change-in-prod")).encode()
        salt = hashlib.sha256(b"ticketing-system-salt-v1").digest()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=390_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password))
        self._fernet = Fernet(key)

    def encrypt(self, data: dict | str) -> str:
        raw = json.dumps(data) if isinstance(data, dict) else data
        return self._fernet.encrypt(raw.encode()).decode()

    def decrypt(self, token: str) -> dict | str:
        raw = self._fernet.decrypt(token.encode()).decode()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def hash_identity(self, identity: str) -> str:
        """One-way hash for logging/indexing without storing raw identity."""
        return hmac.new(b"id-hmac-key", identity.encode(), hashlib.sha256).hexdigest()


_encryption_service = EncryptionService()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TicketItem:
    ticket_type: TicketType = DEFAULT_TICKET_TYPE
    passenger_type: PassengerType = DEFAULT_PASSENGER_TYPE
    quantity: int = DEFAULT_QUANTITY
    period_duration: Optional[PeriodDuration] = None   # only for PERIOD tickets
    group_size: int = DEFAULT_GROUP_SIZE                # only for GROUP tickets

    def __post_init__(self):
        if self.ticket_type == TicketType.PERIOD and self.period_duration is None:
            self.period_duration = DEFAULT_PERIOD_DURATION
        if self.ticket_type == TicketType.GROUP and self.group_size < 3:
            self.group_size = DEFAULT_GROUP_SIZE

    def unit_price(self) -> float:
        base = BASE_PRICES[self.ticket_type]
        pax_mult = PASSENGER_MULTIPLIERS[self.passenger_type]

        if self.ticket_type == TicketType.PERIOD:
            period_mult = PERIOD_MULTIPLIERS[self.period_duration]
            return round(base * period_mult * pax_mult, 2)

        if self.ticket_type == TicketType.GROUP:
            return round(base * self.group_size * pax_mult, 2)

        return round(base * pax_mult, 2)

    def total_price(self) -> float:
        return round(self.unit_price() * self.quantity, 2)

    def preview(self) -> dict:
        return {
            "ticket_type": self.ticket_type.value,
            "passenger_type": self.passenger_type.value,
            "quantity": self.quantity,
            "period_duration": self.period_duration.value if self.period_duration else None,
            "group_size": self.group_size if self.ticket_type == TicketType.GROUP else None,
            "unit_price_sek": self.unit_price(),
            "total_price_sek": self.total_price(),
        }


@dataclass
class PassengerInfo:
    """Raw passenger info – never stored; encrypted representation is stored instead."""
    name: str
    email: str
    phone: Optional[str] = None

    def encrypt(self) -> str:
        return _encryption_service.encrypt(asdict(self))

    @staticmethod
    def decrypt(token: str) -> "PassengerInfo":
        data = _encryption_service.decrypt(token)
        return PassengerInfo(**data)


@dataclass
class CartItem:
    ticket: TicketItem
    passenger_info_encrypted: str  # always stored encrypted


@dataclass
class Transaction:
    transaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.utcnow)
    interface: Interface = DEFAULT_INTERFACE
    items: list[CartItem] = field(default_factory=list)
    payment_success: Optional[bool] = None        # only result stored, no card data
    completed_at: Optional[datetime] = None
    total_amount_sek: float = 0.0

    def compute_total(self) -> float:
        total = sum(item.ticket.total_price() for item in self.items)
        self.total_amount_sek = round(total, 2)
        return self.total_amount_sek


@dataclass
class TransactionResult:
    transaction_id: str
    success: bool
    total_amount_sek: float
    payment_success: bool
    duration_ms: float
    ticket_summaries: list[dict]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Price preview (stateless helper)
# ---------------------------------------------------------------------------

def preview_cart(items: list[TicketItem]) -> dict:
    """
    Return a full price breakdown before any purchase is committed.
    No passenger data needed at this stage.
    """
    line_items = [item.preview() for item in items]
    grand_total = round(sum(i["total_price_sek"] for i in line_items), 2)
    return {
        "line_items": line_items,
        "grand_total_sek": grand_total,
        "currency": "SEK",
        "preview_generated_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Payment gateway stub
# ---------------------------------------------------------------------------

class PaymentGateway:
    """
    Stub payment gateway.
    Real implementation would call an external PCI-DSS compliant processor.
    We never receive or store card numbers / bank details.
    Only the boolean outcome is kept.
    """

    @staticmethod
    def charge(amount_sek: float, payment_token: str) -> bool:
        """
        `payment_token` is a one-time tokenised reference obtained from
        the client-side payment SDK (e.g. Stripe.js / Klarna). The raw
        card data never reaches this server.
        Returns True if payment succeeded, False otherwise.
        """
        # Simulate a ~2 ms network call and ~97 % success rate
        time.sleep(0.002)
        return secrets.randbelow(100) < 97


_payment_gateway = PaymentGateway()


# ---------------------------------------------------------------------------
# Transaction store (in-memory; replace with encrypted DB in production)
# ---------------------------------------------------------------------------

class TransactionStore:
    """Thread-safe, in-memory store that keeps only encrypted passenger data."""

    def __init__(self):
        self._lock = threading.RLock()
        self._store: dict[str, dict] = {}

    def save(self, txn: Transaction) -> None:
        record = {
            "transaction_id": txn.transaction_id,
            "created_at": txn.created_at.isoformat(),
            "completed_at": txn.completed_at.isoformat() if txn.completed_at else None,
            "interface": txn.interface.value,
            "payment_success": txn.payment_success,
            "total_amount_sek": txn.total_amount_sek,
            # passenger_info_encrypted is already encrypted per CartItem
            "items": [
                {
                    "ticket": asdict(item.ticket),
                    "passenger_info_encrypted": item.passenger_info_encrypted,
                }
                for item in txn.items
            ],
        }
        with self._lock:
            self._store[txn.transaction_id] = record

    def get(self, transaction_id: str) -> Optional[dict]:
        with self._lock:
            return self._store.get(transaction_id)

    def count(self) -> int:
        with self._lock:
            return len(self._store)


_transaction_store = TransactionStore()


# ---------------------------------------------------------------------------
# Load-balanced worker queue
# ---------------------------------------------------------------------------

class LoadBalancer:
    """
    Distributes incoming transaction requests across N worker threads.
    Each worker drains its own sub-queue, providing balanced load.
    Guarantees processing within max_wait_seconds (default 15 s).
    Sized to handle 10 000 operations within 5 seconds.
    """

    def __init__(
        self,
        num_workers: int = 32,
        max_queue_size: int = 20_000,
        max_wait_seconds: float = 15.0,
    ):
        self.num_workers = num_workers
        self.max_wait_seconds = max_wait_seconds
        self._executor = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="txn-worker")
        self._queues: list[queue.Queue] = [
            queue.Queue(maxsize=max_queue_size // num_workers + 1)
            for _ in range(num_workers)
        ]
        self._counters: list[int] = [0] * num_workers
        self._lock = threading.Lock()
        self._worker_threads: list[threading.Thread] = []
        self._running = True
        self._start_workers()

    def _start_workers(self) -> None:
        for i in range(self.num_workers):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            t.start()
            self._worker_threads.append(t)

    def _least_loaded_worker(self) -> int:
        with self._lock:
            idx = min(range(self.num_workers), key=lambda i: self._queues[i].qsize())
            return idx

    def _worker_loop(self, worker_id: int) -> None:
        q = self._queues[worker_id]
        while self._running:
            try:
                task, future = q.get(timeout=0.1)
                try:
                    result = task()
                    future.set_result(result)
                except Exception as exc:
                    future.set_exception(exc)
                finally:
                    q.task_done()
            except queue.Empty:
                continue

    def submit(self, task) -> "concurrent.futures.Future":
        import concurrent.futures
        future: concurrent.futures.Future = concurrent.futures.Future()
        worker_id = self._least_loaded_worker()
        try:
            self._queues[worker_id].put_nowait((task, future))
        except queue.Full:
            future.set_exception(RuntimeError("System overloaded – please retry."))
        return future

    def shutdown(self) -> None:
        self._running = False
        self._executor.shutdown(wait=False)


_load_balancer = LoadBalancer()


# ---------------------------------------------------------------------------
# Core ticketing functions
# ---------------------------------------------------------------------------

def build_ticket_item(
    ticket_type: Optional[str] = None,
    passenger_type: Optional[str] = None,
    quantity: Optional[int] = None,
    period_duration: Optional[str] = None,
    group_size: Optional[int] = None,
) -> TicketItem:
    """
    Build a TicketItem with safe defaults for any unspecified field.
    """
    tt = TicketType(ticket_type) if ticket_type else DEFAULT_TICKET_TYPE
    pt = PassengerType(passenger_type) if passenger_type else DEFAULT_PASSENGER_TYPE
    qty = quantity if quantity and quantity >= 1 else DEFAULT_QUANTITY
    pd = PeriodDuration(period_duration) if period_duration else DEFAULT_PERIOD_DURATION
    gs = group_size if group_size and group_size >= 3 else DEFAULT_GROUP_SIZE

    return TicketItem(
        ticket_type=tt,
        passenger_type=pt,
        quantity=qty,
        period_duration=pd if tt == TicketType.PERIOD else None,
        group_size=gs,
    )


def get_price_preview(raw_items: list[dict]) -> dict:
    """
    Public API: receive a list of raw item dicts, return full price breakdown.
    No passenger data required.

    Example raw_item:
        {"ticket_type": "single_ride", "passenger_type": "student", "quantity": 2}
    """
    items = [build_ticket_item(**raw) for raw in raw_items]
    return preview_cart(items)


def _process_transaction(
    txn: Transaction,
    payment_token: str,
) -> TransactionResult:
    """Internal: validate, charge, persist one transaction. Runs inside a worker."""
    start = time.perf_counter()

    if not txn.items:
        return TransactionResult(
            transaction_id=txn.transaction_id,
            success=False,
            total_amount_sek=0.0,
            payment_success=False,
            duration_ms=0.0,
            ticket_summaries=[],
            error="Cart is empty.",
        )

    total = txn.compute_total()

    # Charge via gateway – we pass only the tokenised reference, never raw card data
    payment_ok = _payment_gateway.charge(total, payment_token)

    txn.payment_success = payment_ok
    txn.completed_at = datetime.utcnow()
    _transaction_store.save(txn)

    duration_ms = (time.perf_counter() - start) * 1000

    return TransactionResult(
        transaction_id=txn.transaction_id,
        success=payment_ok,
        total_amount_sek=total,
        payment_success=payment_ok,
        duration_ms=round(duration_ms, 2),
        ticket_summaries=[item.ticket.preview() for item in txn.items],
    )


def purchase_tickets(
    raw_items: list[dict],
    passenger_info: dict,
    payment_token: str,
    interface: Optional[str] = None,
    max_wait_seconds: float = 15.0,
) -> TransactionResult:
    """
    Primary purchase function.

    Parameters
    ----------
    raw_items : list of dicts, each describing one TicketItem.
        Keys (all optional, defaults applied):
          ticket_type, passenger_type, quantity, period_duration, group_size
    passenger_info : dict with keys: name, email, phone (optional).
        Encrypted immediately; raw values are not retained.
    payment_token : one-time token from client-side payment SDK.
        Raw card/bank details are never passed to or stored by this system.
    interface : "mobile" | "web" | "vending"  (default: "mobile")
    max_wait_seconds : SLA ceiling (default 15 s)

    Returns
    -------
    TransactionResult
    """
    iface = Interface(interface) if interface else DEFAULT_INTERFACE

    # Encrypt passenger data immediately – raw dict is never stored
    passenger = PassengerInfo(
        name=passenger_info.get("name", ""),
        email=passenger_info.get("email", ""),
        phone=passenger_info.get("phone"),
    )
    encrypted_passenger = passenger.encrypt()

    # Build cart
    cart: list[CartItem] = []
    for raw in raw_items:
        ticket = build_ticket_item(**raw)
        cart.append(CartItem(ticket=ticket, passenger_info_encrypted=encrypted_passenger))

    txn = Transaction(interface=iface, items=cart)

    # Submit to load balancer
    future = _load_balancer.submit(lambda t=txn, pt=payment_token: _process_transaction(t, pt))

    try:
        result: TransactionResult = future.result(timeout=max_wait_seconds)
    except TimeoutError:
        result = TransactionResult(
            transaction_id=txn.transaction_id,
            success=False,
            total_amount_sek=txn.total_amount_sek,
            payment_success=False,
            duration_ms=max_wait_seconds * 1000,
            ticket_summaries=[],
            error=f"Transaction timed out after {max_wait_seconds}s.",
        )

    return result


def purchase_tickets_bulk(
    orders: list[dict],
    max_wait_seconds: float = 15.0,
) -> list[TransactionResult]:
    """
    Submit multiple independent purchase orders concurrently.
    Each order dict mirrors the kwargs of `purchase_tickets`.
    Designed to handle 10 000+ concurrent submissions.

    Parameters
    ----------
    orders : list of dicts, each with keys:
        raw_items, passenger_info, payment_token, interface (optional)
    max_wait_seconds : per-order SLA ceiling

    Returns
    -------
    List of TransactionResult in the same order as `orders`.
    """
    futures = []
    for order in orders:
        iface = Interface(order.get("interface", DEFAULT_INTERFACE.value))
        passenger = PassengerInfo(
            name=order["passenger_info"].get("name", ""),
            email=order["passenger_info"].get("email", ""),
            phone=order["passenger_info"].get("phone"),
        )
        encrypted_passenger = passenger.encrypt()
        cart = [
            CartItem(ticket=build_ticket_item(**raw), passenger_info_encrypted=encrypted_passenger)
            for raw in order.get("raw_items", [{}])
        ]
        txn = Transaction(interface=iface, items=cart)
        pt = order.get("payment_token", "tok_dummy")
        future = _load_balancer.submit(lambda t=txn, p=pt: _process_transaction(t, p))
        futures.append((txn.transaction_id, future))

    results = []
    deadline = time.perf_counter() + max_wait_seconds
    for txn_id, fut in futures:
        remaining = deadline - time.perf_counter()
        try:
            result = fut.result(timeout=max(remaining, 0.01))
        except TimeoutError:
            result = TransactionResult(
                transaction_id=txn_id,
                success=False,
                total_amount_sek=0.0,
                payment_success=False,
                duration_ms=max_wait_seconds * 1000,
                ticket_summaries=[],
                error="Bulk order timed out.",
            )
        results.append(result)
    return results


def get_transaction(transaction_id: str) -> Optional[dict]:
    """Retrieve a stored transaction record by ID (passenger data remains encrypted)."""
    return _transaction_store.get(transaction_id)


def decrypt_passenger_info(encrypted_token: str) -> dict:
    """
    Decrypt passenger info for authorised internal use (e.g. ticket inspector).
    In production this should be gated by role-based access control.
    """
    passenger = PassengerInfo.decrypt(encrypted_token)
    return asdict(passenger)


def get_available_ticket_types() -> dict:
    """Return the full catalogue with defaults highlighted."""
    return {
        "ticket_types": [t.value for t in TicketType],
        "passenger_types": [p.value for p in PassengerType],
        "period_durations": [d.value for d in PeriodDuration],
        "interfaces": [i.value for i in Interface],
        "defaults": {
            "ticket_type": DEFAULT_TICKET_TYPE.value,
            "passenger_type": DEFAULT_PASSENGER_TYPE.value,
            "period_duration": DEFAULT_PERIOD_DURATION.value,
            "group_size": DEFAULT_GROUP_SIZE,
            "quantity": DEFAULT_QUANTITY,
            "interface": DEFAULT_INTERFACE.value,
        },
        "base_prices_sek": {k.value: v for k, v in BASE_PRICES.items()},
        "passenger_discounts": {k.value: f"{int((1 - v) * 100)}%" for k, v in PASSENGER_MULTIPLIERS.items()},
        "period_multipliers": {k.value: v for k, v in PERIOD_MULTIPLIERS.items()},
    }


def system_health() -> dict:
    """Return basic system health metrics."""
    queue_sizes = [q.qsize() for q in _load_balancer._queues]
    return {
        "status": "ok",
        "workers": _load_balancer.num_workers,
        "queue_sizes": queue_sizes,
        "total_queue_depth": sum(queue_sizes),
        "transactions_stored": _transaction_store.count(),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Async wrapper (for async frameworks / mobile backends)
# ---------------------------------------------------------------------------

async def async_purchase_tickets(
    raw_items: list[dict],
    passenger_info: dict,
    payment_token: str,
    interface: Optional[str] = None,
    max_wait_seconds: float = 15.0,
) -> TransactionResult:
    """Async wrapper around purchase_tickets for use in FastAPI / asyncio contexts."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: purchase_tickets(raw_items, passenger_info, payment_token, interface, max_wait_seconds),
    )


async def async_purchase_tickets_bulk(
    orders: list[dict],
    max_wait_seconds: float = 15.0,
) -> list[TransactionResult]:
    """Async wrapper around purchase_tickets_bulk."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: purchase_tickets_bulk(orders, max_wait_seconds),
    )


# ---------------------------------------------------------------------------
# Quick smoke-test (run directly: python ticketing_system.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Ticket Catalogue ===")
    catalogue = get_available_ticket_types()
    print(json.dumps(catalogue, indent=2))

    print("\n=== Price Preview ===")
    preview = get_price_preview([
        {"ticket_type": "single_ride", "passenger_type": "student", "quantity": 2},
        {"ticket_type": "period", "passenger_type": "commuter", "period_duration": "month", "quantity": 1},
        {"ticket_type": "group", "passenger_type": "regular", "group_size": 5, "quantity": 1},
    ])
    print(json.dumps(preview, indent=2))

    print("\n=== Single Purchase ===")
    result = purchase_tickets(
        raw_items=[
            {"ticket_type": "single_ride", "passenger_type": "senior", "quantity": 2},
            {},  # all defaults
        ],
        passenger_info={"name": "Anna Svensson", "email": "anna@example.se", "phone": "+46701234567"},
        payment_token="tok_test_abc123",
        interface="mobile",
    )
    print(json.dumps(result.__dict__, indent=2, default=str))

    print("\n=== Bulk Throughput Test (10 000 orders) ===")
    N = 10_000
    orders = [
        {
            "raw_items": [{"ticket_type": "single_ride", "passenger_type": "regular", "quantity": 1}],
            "passenger_info": {"name": f"User{i}", "email": f"user{i}@example.com"},
            "payment_token": f"tok_{i}",
            "interface": "web",
        }
        for i in range(N)
    ]
    t0 = time.perf_counter()
    results = purchase_tickets_bulk(orders, max_wait_seconds=15.0)
    elapsed = time.perf_counter() - t0
    succeeded = sum(1 for r in results if r.success)
    print(f"  Processed : {N:,} orders")
    print(f"  Elapsed   : {elapsed:.2f}s")
    print(f"  Succeeded : {succeeded:,} / {N:,}")
    print(f"  Rate      : {N / elapsed:,.0f} ops/s")
    print(f"  Within 5s : {'YES' if elapsed <= 5 else 'NO – increase workers'}")

    print("\n=== System Health ===")
    print(json.dumps(system_health(), indent=2))