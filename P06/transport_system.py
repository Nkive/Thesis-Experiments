"""
Transit Ticketing System
Handles ticket purchasing, pricing, discounts, validation, concurrency safety,
and secure handling of passenger and payment data.

stdlib-only — no external dependencies required.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TicketType(str, Enum):
    SINGLE_RIDE       = "single_ride"
    TIME_PASS_DAILY   = "time_pass_daily"
    TIME_PASS_WEEKLY  = "time_pass_weekly"
    TIME_PASS_MONTHLY = "time_pass_monthly"
    BUNDLE_5          = "bundle_5"
    BUNDLE_10         = "bundle_10"
    BUNDLE_20         = "bundle_20"


class PassengerCategory(str, Enum):
    ADULT   = "adult"
    STUDENT = "student"
    SENIOR  = "senior"
    CHILD   = "child"


# ---------------------------------------------------------------------------
# Pricing tables  (Decimal for exact arithmetic)
# ---------------------------------------------------------------------------

BASE_PRICES: dict[TicketType, Decimal] = {
    TicketType.SINGLE_RIDE:        Decimal("30.00"),
    TicketType.TIME_PASS_DAILY:    Decimal("90.00"),
    TicketType.TIME_PASS_WEEKLY:   Decimal("350.00"),
    TicketType.TIME_PASS_MONTHLY:  Decimal("1050.00"),
    TicketType.BUNDLE_5:           Decimal("135.00"),   # ~10 % bulk saving
    TicketType.BUNDLE_10:          Decimal("255.00"),   # ~15 % bulk saving
    TicketType.BUNDLE_20:          Decimal("480.00"),   # ~20 % bulk saving
}

CATEGORY_DISCOUNTS: dict[PassengerCategory, Decimal] = {
    PassengerCategory.ADULT:   Decimal("0.00"),
    PassengerCategory.STUDENT: Decimal("0.20"),   # 20 % off
    PassengerCategory.SENIOR:  Decimal("0.30"),   # 30 % off
    PassengerCategory.CHILD:   Decimal("0.50"),   # 50 % off
}

MAX_QUANTITY_PER_ITEM: int = 100
MAX_ITEMS_PER_REQUEST: int = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TicketItem:
    """One line in a purchase: a ticket type + passenger category + quantity."""
    ticket_type: TicketType
    passenger_category: PassengerCategory
    quantity: int


@dataclass
class PurchaseRequest:
    """
    Incoming purchase request.

    Personal data fields (passenger_id, contact_email, contact_phone) are kept
    as opaque strings here; call mask_passenger_data() before logging or
    storing outside a secure boundary.

    Payment data is intentionally NOT stored on this object — pass it
    separately to process_payment_token().
    """
    passenger_id: str
    items: list[TicketItem]
    payment_method: Optional[str] = None   # "card" | "wallet" | "apple_pay" …
    contact_email: Optional[str] = None    # PII — mask before logging
    contact_phone: Optional[str] = None    # PII — mask before logging


@dataclass
class PricedItem:
    """A TicketItem enriched with its computed price breakdown."""
    ticket_type: TicketType
    passenger_category: PassengerCategory
    quantity: int
    unit_base_price: Decimal
    discount_rate: Decimal
    unit_final_price: Decimal
    line_total: Decimal


@dataclass
class PurchaseReceipt:
    """Returned after a successful purchase."""
    passenger_id: str
    items: list[PricedItem]
    subtotal: Decimal
    total: Decimal
    payment_method: Optional[str]
    transaction_id: str


# ---------------------------------------------------------------------------
# Pricing — exact arithmetic with Decimal
# ---------------------------------------------------------------------------

def get_base_price(ticket_type: TicketType) -> Decimal:
    """Return the base price for a given ticket type."""
    if ticket_type not in BASE_PRICES:
        raise ValueError(f"Unknown ticket type: {ticket_type!r}")
    return BASE_PRICES[ticket_type]


def get_discount_rate(passenger_category: PassengerCategory) -> Decimal:
    """Return the discount rate (0.00–1.00) for a given passenger category."""
    if passenger_category not in CATEGORY_DISCOUNTS:
        raise ValueError(f"Unknown passenger category: {passenger_category!r}")
    return CATEGORY_DISCOUNTS[passenger_category]


def calculate_unit_price(
    ticket_type: TicketType,
    passenger_category: PassengerCategory,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Compute the per-unit price using exact Decimal arithmetic.

    Returns:
        (base_price, discount_rate, final_unit_price)
    """
    base = get_base_price(ticket_type)
    rate = get_discount_rate(passenger_category)
    final = (base * (Decimal("1") - rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return base, rate, final


def calculate_line_total(unit_price: Decimal, quantity: int) -> Decimal:
    """
    Return the exact total for *quantity* units at *unit_price*.

    Raises ValueError for non-positive or out-of-range quantities.
    """
    if not isinstance(quantity, int) or quantity < 1:
        raise ValueError(f"Quantity must be a positive integer, got {quantity!r}")
    if quantity > MAX_QUANTITY_PER_ITEM:
        raise ValueError(
            f"Quantity {quantity} exceeds the per-item maximum of {MAX_QUANTITY_PER_ITEM}"
        )
    return (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def price_ticket_item(item: TicketItem) -> PricedItem:
    """Apply all pricing rules to one TicketItem and return a PricedItem."""
    base, rate, unit_final = calculate_unit_price(item.ticket_type, item.passenger_category)
    line_total = calculate_line_total(unit_final, item.quantity)
    return PricedItem(
        ticket_type=item.ticket_type,
        passenger_category=item.passenger_category,
        quantity=item.quantity,
        unit_base_price=base,
        discount_rate=rate,
        unit_final_price=unit_final,
        line_total=line_total,
    )


def calculate_order_total(priced_items: list[PricedItem]) -> Decimal:
    """
    Sum all line totals with exact Decimal arithmetic.

    Each line total was already rounded to 2 decimal places by
    calculate_line_total(), so the sum is accumulated precisely.
    """
    total = sum((pi.line_total for pi in priced_items), Decimal("0.00"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ALLOWED_PAYMENT_METHODS = {"card", "wallet", "apple_pay", "google_pay", "bank_transfer"}


def validate_ticket_item(item: TicketItem) -> list[str]:
    """
    Validate a single TicketItem.

    Returns a list of human-readable error messages; empty means valid.
    """
    errors: list[str] = []

    if not isinstance(item.ticket_type, TicketType):
        errors.append(f"ticket_type {item.ticket_type!r} is not a recognised TicketType")

    if not isinstance(item.passenger_category, PassengerCategory):
        errors.append(
            f"passenger_category {item.passenger_category!r} is not a recognised PassengerCategory"
        )

    if not isinstance(item.quantity, int) or isinstance(item.quantity, bool):
        errors.append(f"quantity must be an integer, got {type(item.quantity).__name__}")
    elif item.quantity < 1:
        errors.append(f"quantity must be ≥ 1, got {item.quantity}")
    elif item.quantity > MAX_QUANTITY_PER_ITEM:
        errors.append(f"quantity {item.quantity} exceeds the maximum of {MAX_QUANTITY_PER_ITEM}")

    return errors


def validate_purchase_request(request: PurchaseRequest) -> list[str]:
    """
    Validate a full PurchaseRequest before any pricing or processing.

    Checks:
      - passenger_id present and non-blank
      - at least one item, no more than MAX_ITEMS_PER_REQUEST
      - each TicketItem is individually valid
      - payment_method is known (if supplied)
      - contact_email is well-formed (if supplied)

    Returns a list of all error messages; empty means valid.
    """
    errors: list[str] = []

    # --- passenger identity ---
    if not request.passenger_id or not str(request.passenger_id).strip():
        errors.append("passenger_id is required and must not be blank")

    # --- items list ---
    if not request.items:
        errors.append("Purchase must contain at least one ticket item")
    elif len(request.items) > MAX_ITEMS_PER_REQUEST:
        errors.append(
            f"A single purchase may not exceed {MAX_ITEMS_PER_REQUEST} line items "
            f"(got {len(request.items)})"
        )
    else:
        for idx, item in enumerate(request.items):
            for err in validate_ticket_item(item):
                errors.append(f"items[{idx}]: {err}")

    # --- payment method ---
    if request.payment_method is not None:
        if request.payment_method not in ALLOWED_PAYMENT_METHODS:
            errors.append(
                f"payment_method {request.payment_method!r} is not supported; "
                f"allowed values: {sorted(ALLOWED_PAYMENT_METHODS)}"
            )

    # --- optional contact e-mail (PII format check only) ---
    if request.contact_email is not None:
        if not _EMAIL_RE.match(request.contact_email):
            errors.append(f"contact_email does not look like a valid e-mail address")

    return errors


# ---------------------------------------------------------------------------
# Concurrency safety
# ---------------------------------------------------------------------------

# One re-entrant lock per passenger prevents two simultaneous purchases for
# the same passenger_id racing each other.  A global lock covers the rare
# case where a passenger_id is seen for the first time concurrently.
_passenger_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def _get_passenger_lock(passenger_id: str) -> threading.RLock:
    """Return (creating if necessary) the per-passenger re-entrant lock."""
    with _registry_lock:
        if passenger_id not in _passenger_locks:
            _passenger_locks[passenger_id] = threading.RLock()
        return _passenger_locks[passenger_id]


# ---------------------------------------------------------------------------
# Security helpers — personal data
# ---------------------------------------------------------------------------

def mask_email(email: str) -> str:
    """
    Return a masked version of an e-mail address safe for logging.

    E.g.  "alice@example.com"  →  "a***@example.com"
    """
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    masked_local = local[0] + "***" if local else "***"
    return f"{masked_local}@{domain}"


def mask_phone(phone: str) -> str:
    """
    Return a masked phone number safe for logging.

    Keeps the last 4 digits only.  E.g.  "+46701234567"  →  "***4567"
    """
    digits = re.sub(r"\D", "", phone)
    return "***" + digits[-4:] if len(digits) >= 4 else "***"


def mask_passenger_data(request: PurchaseRequest) -> dict:
    """
    Return a log-safe dictionary of the request with all PII masked.

    Never call repr() or str() on a PurchaseRequest directly in a log
    statement; use this function instead.
    """
    return {
        "passenger_id": hashlib.sha256(request.passenger_id.encode()).hexdigest()[:10],
        "item_count": len(request.items),
        "payment_method": request.payment_method,
        "contact_email": mask_email(request.contact_email) if request.contact_email else None,
        "contact_phone": mask_phone(request.contact_phone) if request.contact_phone else None,
    }


# ---------------------------------------------------------------------------
# Security helpers — payment data
# ---------------------------------------------------------------------------

def tokenise_payment_details(raw_card_number: str) -> str:
    """
    Replace a raw card number with a one-way token for safe internal use.

    In a real integration this function would call a PCI-compliant vault
    (e.g. Stripe, Adyen).  This implementation demonstrates the pattern:
    the raw PAN never travels beyond this function boundary.

    Returns a token string of the form  "tok_<hex>"  that can be stored or
    logged without exposing the PAN.
    """
    # Validate Luhn checksum before tokenising
    if not _luhn_check(raw_card_number):
        raise ValueError("Card number failed Luhn validation — check for typos")

    # Derive a deterministic token via HMAC-SHA256 with a per-process secret
    # so the token cannot be reverse-engineered without the secret.
    secret = _get_payment_secret()
    token_hex = hmac.new(secret, raw_card_number.encode(), hashlib.sha256).hexdigest()
    return "tok_" + token_hex[:32]


def mask_card_number(card_number: str) -> str:
    """
    Return a display-safe version of a card number (last 4 digits only).

    E.g.  "4111111111111111"  →  "**** **** **** 1111"
    """
    digits = re.sub(r"\D", "", card_number)
    if len(digits) < 4:
        return "****"
    return "**** **** **** " + digits[-4:]


def _luhn_check(card_number: str) -> bool:
    """Return True if *card_number* passes the Luhn algorithm."""
    digits = [int(d) for d in re.sub(r"\D", "", card_number)]
    if len(digits) < 13:
        return False
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _get_payment_secret() -> bytes:
    """
    Return the HMAC secret used for payment tokenisation.

    Reads PAYMENT_HMAC_SECRET from the environment.  Falls back to a
    process-lifetime random secret (safe for testing; not for production).
    """
    env_secret = os.environ.get("PAYMENT_HMAC_SECRET")
    if env_secret:
        return env_secret.encode()
    # Lazily generate and cache a random secret for this process lifetime
    if not hasattr(_get_payment_secret, "_cached"):
        _get_payment_secret._cached = os.urandom(32)  # type: ignore[attr-defined]
    return _get_payment_secret._cached  # type: ignore[attr-defined]


def process_payment_token(payment_token: str, amount: Decimal) -> dict:
    """
    Simulate authorising a pre-tokenised payment.

    In production this would call the payment gateway API using *payment_token*
    (never the raw PAN) and *amount*.

    Returns a dict with keys:
        authorised (bool), gateway_ref (str), amount_charged (Decimal)
    Raises RuntimeError if the gateway call fails.
    """
    if not payment_token.startswith("tok_"):
        raise ValueError("payment_token must be a value returned by tokenise_payment_details()")
    if amount <= Decimal("0"):
        raise ValueError(f"Payment amount must be positive, got {amount}")

    # --- stub: replace with real gateway call ---
    gateway_ref = "GW-" + uuid.uuid4().hex[:10].upper()
    logger.info("Payment authorised: ref=%s amount=%s", gateway_ref, amount)
    return {
        "authorised": True,
        "gateway_ref": gateway_ref,
        "amount_charged": amount,
    }


# ---------------------------------------------------------------------------
# Transaction ID
# ---------------------------------------------------------------------------

def _generate_transaction_id() -> str:
    """Generate a globally unique, collision-resistant transaction ID."""
    return "TXN-" + uuid.uuid4().hex[:12].upper()


# ---------------------------------------------------------------------------
# Purchase processing  (thread-safe, exact arithmetic)
# ---------------------------------------------------------------------------

def process_purchase(request: PurchaseRequest) -> PurchaseReceipt:
    """
    Process a ticket purchase end-to-end.

    Thread safety
    -------------
    Acquires a per-passenger re-entrant lock before any pricing or state
    mutation so that concurrent calls for the same passenger_id are
    serialised, preventing double-charging or race conditions.

    Steps
    -----
    1. Validate all inputs — raises ValueError listing every problem found.
    2. Acquire the per-passenger lock.
    3. Price every item with exact Decimal arithmetic.
    4. Sum the order total.
    5. Return a PurchaseReceipt.

    Raises
    ------
    ValueError  — if validation fails (message includes all errors).
    """
    # 1. Validate — collect ALL errors before raising so the caller sees
    #    everything wrong in one shot rather than one error at a time.
    errors = validate_purchase_request(request)
    if errors:
        raise ValueError(
            "Purchase validation failed:\n  - " + "\n  - ".join(errors)
        )

    # 2. Per-passenger lock (concurrent-safe)
    passenger_lock = _get_passenger_lock(request.passenger_id)
    with passenger_lock:
        logger.debug("Processing purchase for passenger %s", mask_passenger_data(request))

        # 3. Price each item — pure functions, no shared state
        priced_items: list[PricedItem] = [price_ticket_item(item) for item in request.items]

        # 4. Exact total
        total = calculate_order_total(priced_items)

        # 5. Build and return receipt (payment gateway call would go here)
        receipt = PurchaseReceipt(
            passenger_id=request.passenger_id,
            items=priced_items,
            subtotal=total,
            total=total,
            payment_method=request.payment_method,
            transaction_id=_generate_transaction_id(),
        )

    logger.info(
        "Purchase complete: txn=%s total=%s items=%d",
        receipt.transaction_id,
        receipt.total,
        len(receipt.items),
    )
    return receipt
