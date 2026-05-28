"""
TransportApp — single-class public transport ticketing system.

Features
--------
* Ticket types : SINGLE, DAY, WEEKLY, MONTHLY, BUNDLE_5, BUNDLE_10
* Passenger categories : youth, student, adult, senior
* Thread-safe concurrent purchasing
* Abstract personal / payment information (never stored in plain text)
* Robust input validation and error handling
* Designed as a pure-Python backend callable from any interface
  (REST API, mobile SDK, CLI, etc.)

Usage
-----
    app = TransportApp()
    session = app.create_session(passenger_type="student")
    result  = app.purchase(session_id=session["session_id"],
                           ticket_type="WEEKLY",
                           quantity=2,
                           payment_token="tok_abc123")
    print(result)
"""
from __future__ import annotations 
import hashlib
import hmac
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class TransportApp:
    """
    Single-class public transport ticketing system.

    All public methods are thread-safe and raise ``ValueError`` on
    invalid input so callers (web handlers, mobile SDKs, CLIs) can
    surface meaningful error messages without crashing.
    """

    # ------------------------------------------------------------------ #
    #  Enumerations (kept as inner Enums so the class is self-contained)  #
    # ------------------------------------------------------------------ #

    class PassengerType(str, Enum):
        YOUTH   = "youth"    # under 16
        STUDENT = "student"  # 16-25 with valid student card
        ADULT   = "adult"    # standard fare
        SENIOR  = "senior"   # 65+

    class TicketType(str, Enum):
        SINGLE   = "SINGLE"    # one trip
        DAY      = "DAY"       # unlimited trips for one day
        WEEKLY   = "WEEKLY"    # 7-day period
        MONTHLY  = "MONTHLY"   # 30-day period
        BUNDLE_5 = "BUNDLE_5"  # carnet of 5 single trips
        BUNDLE_10= "BUNDLE_10" # carnet of 10 single trips

    class TicketStatus(str, Enum):
        VALID    = "valid"
        USED     = "used"      # single / bundle fully consumed
        EXPIRED  = "expired"

    # ------------------------------------------------------------------ #
    #  Fixed base prices (adult fare, in the system's currency unit)      #
    # ------------------------------------------------------------------ #

    _BASE_PRICE: dict[str, float] = {
        "SINGLE"   : 3.50,
        "DAY"      : 12.00,
        "WEEKLY"   : 40.00,
        "MONTHLY"  : 120.00,
        "BUNDLE_5" : 15.00,   # ~14 % saving vs 5 singles
        "BUNDLE_10": 27.00,   # ~23 % saving vs 10 singles
    }

    # Discount multipliers applied to the base price
    _DISCOUNT: dict[str, float] = {
        "youth"  : 0.50,   # 50 % of adult fare
        "student": 0.70,   # 30 % discount
        "adult"  : 1.00,   # full fare
        "senior" : 0.60,   # 40 % discount
    }

    # How many single-trip uses each ticket type provides
    _USES: dict[str, int | None] = {
        "SINGLE"   : 1,
        "DAY"      : None,   # unlimited within validity window
        "WEEKLY"   : None,
        "MONTHLY"  : None,
        "BUNDLE_5" : 5,
        "BUNDLE_10": 10,
    }

    # Validity duration in days (None = must be used same day)
    _VALIDITY_DAYS: dict[str, int | None] = {
        "SINGLE"   : None,   # valid until used or end of operating day
        "DAY"      : 1,
        "WEEKLY"   : 7,
        "MONTHLY"  : 30,
        "BUNDLE_5" : 180,    # 6-month carnet
        "BUNDLE_10": 180,
    }

    # ------------------------------------------------------------------ #
    #  Initialisation                                                      #
    # ------------------------------------------------------------------ #

    def __init__(self, *, currency: str = "SEK", secret_key: str | None = None) -> None:
        """
        Parameters
        ----------
        currency   : ISO currency code shown in receipts (default SEK).
        secret_key : HMAC key used to pseudonymise payment tokens.
                     Generated randomly if not supplied.
        """
        self.currency   = currency.upper()
        self._secret    = (secret_key or secrets.token_hex(32)).encode()

        # Thread-safe storage
        self._lock      = threading.Lock()
        self._sessions: dict[str, dict]  = {}   # session_id → session info
        self._tickets:  dict[str, dict]  = {}   # ticket_id  → ticket info
        self._receipts: dict[str, dict]  = {}   # receipt_id → purchase record

        # Per-session ticket lists (session_id → [ticket_id, …])
        self._session_tickets: dict[str, list[str]] = defaultdict(list)

    # ------------------------------------------------------------------ #
    #  Session management                                                  #
    # ------------------------------------------------------------------ #

    def create_session(self, passenger_type: str) -> dict[str, Any]:
        """
        Open a purchasing session for a passenger.

        Parameters
        ----------
        passenger_type : One of "youth", "student", "adult", "senior".

        Returns
        -------
        dict with ``session_id``, ``passenger_type``, ``discount_rate``,
        ``created_at``.

        Raises
        ------
        ValueError if passenger_type is not recognised.
        """
        ptype = self._validate_enum(passenger_type, self.PassengerType, "passenger_type")
        session_id = str(uuid.uuid4())
        now        = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        session = {
            "session_id"    : session_id,
            "passenger_type": ptype,
            "discount_rate" : self._DISCOUNT[ptype],
            "created_at"    : now,
        }

        with self._lock:
            self._sessions[session_id] = session

        return {
            "session_id"    : session_id,
            "passenger_type": ptype,
            "discount_rate" : self._DISCOUNT[ptype],
            "created_at"    : now,
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Return session info, or raise ValueError if not found."""
        self._validate_str(session_id, "session_id")
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found.")
        return dict(session)

    # ------------------------------------------------------------------ #
    #  Pricing                                                             #
    # ------------------------------------------------------------------ #

    def calculate_price(
        self,
        ticket_type  : str,
        passenger_type: str,
        quantity     : int = 1,
    ) -> dict[str, Any]:
        """
        Calculate the total price for a ticket purchase.

        Formula
        -------
        unit_price = BASE_PRICE[ticket_type] × DISCOUNT[passenger_type]
        total      = unit_price × quantity

        Returns
        -------
        dict with ``unit_price``, ``total_price``, ``currency``,
        ``ticket_type``, ``passenger_type``, ``quantity``.
        """
        ttype  = self._validate_enum(ticket_type,   self.TicketType,   "ticket_type")
        ptype  = self._validate_enum(passenger_type, self.PassengerType,"passenger_type")
        qty    = self._validate_positive_int(quantity, "quantity")

        unit   = round(self._BASE_PRICE[ttype] * self._DISCOUNT[ptype], 2)
        total  = round(unit * qty, 2)

        return {
            "ticket_type"   : ttype,
            "passenger_type": ptype,
            "quantity"      : qty,
            "unit_price"    : unit,
            "total_price"   : total,
            "currency"      : self.currency,
        }

    def list_prices(self, passenger_type: str) -> list[dict[str, Any]]:
        """
        Return price list for all ticket types for a given passenger category.
        Useful for populating a mobile/web ticket-selection screen.
        """
        ptype = self._validate_enum(passenger_type, self.PassengerType, "passenger_type")
        return [
            self.calculate_price(ttype, ptype)
            for ttype in self.TicketType
        ]

    # ------------------------------------------------------------------ #
    #  Purchasing                                                          #
    # ------------------------------------------------------------------ #

    def purchase(
        self,
        session_id    : str,
        ticket_type   : str,
        quantity      : int = 1,
        payment_token : str = "",
    ) -> dict[str, Any]:
        """
        Purchase one or more tickets within a session.

        Payment information is never stored; only a one-way HMAC
        fingerprint of the token is kept for audit purposes.

        Parameters
        ----------
        session_id    : Active session (from ``create_session``).
        ticket_type   : E.g. "SINGLE", "WEEKLY", "BUNDLE_10".
        quantity      : Number of tickets to purchase (≥ 1).
        payment_token : Opaque token from a payment gateway.
                        Content is never logged or stored in plain text.

        Returns
        -------
        dict with ``receipt_id``, ``ticket_ids``, pricing summary,
        and ticket validity windows.
        """
        self._validate_str(session_id, "session_id")
        ttype = self._validate_enum(ticket_type, self.TicketType, "ticket_type")
        qty   = self._validate_positive_int(quantity, "quantity")
        self._validate_str(payment_token or "", "payment_token", required=False)

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ValueError(f"Session '{session_id}' not found.")
            ptype = session["passenger_type"]

        pricing   = self.calculate_price(ttype, ptype, qty)
        now       = datetime.now(timezone.utc).replace(tzinfo=None)
        ticket_ids: list[str] = []

        for _ in range(qty):
            ticket_id = str(uuid.uuid4())
            ticket    = self._build_ticket(ticket_id, ttype, ptype, now)
            with self._lock:
                self._tickets[ticket_id] = ticket
                self._session_tickets[session_id].append(ticket_id)
            ticket_ids.append(ticket_id)

        receipt_id   = str(uuid.uuid4())
        payment_hmac = self._pseudonymise(payment_token or "no-token")

        receipt = {
            "receipt_id"        : receipt_id,
            "session_id"        : session_id,
            "passenger_type"    : ptype,
            "ticket_type"       : ttype,
            "quantity"          : qty,
            "unit_price"        : pricing["unit_price"],
            "total_price"       : pricing["total_price"],
            "currency"          : self.currency,
            "ticket_ids"        : ticket_ids,
            "payment_fingerprint": payment_hmac,   # abstract — not reversible
            "purchased_at"      : now.isoformat(),
        }

        with self._lock:
            self._receipts[receipt_id] = receipt

        return {
            "receipt_id"  : receipt_id,
            "ticket_ids"  : ticket_ids,
            "ticket_type" : ttype,
            "quantity"    : qty,
            "unit_price"  : pricing["unit_price"],
            "total_price" : pricing["total_price"],
            "currency"    : self.currency,
            "purchased_at": now.isoformat(),
            "validity"    : self._validity_summary(ttype, now),
        }

    # ------------------------------------------------------------------ #
    #  Ticket usage                                                        #
    # ------------------------------------------------------------------ #

    def use_ticket(self, ticket_id: str) -> dict[str, Any]:
        """
        Validate and consume a single use of a ticket.

        For unlimited-use period tickets the ticket remains VALID.
        For SINGLE and BUNDLE_* tickets the use counter decrements;
        when exhausted the status becomes USED.

        Raises
        ------
        ValueError if the ticket is not found, already USED, or EXPIRED.
        """
        self._validate_str(ticket_id, "ticket_id")

        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise ValueError(f"Ticket '{ticket_id}' not found.")

            # Refresh expiry status
            self._refresh_status(ticket)

            if ticket["status"] == self.TicketStatus.EXPIRED:
                raise ValueError(f"Ticket '{ticket_id}' has expired.")
            if ticket["status"] == self.TicketStatus.USED:
                raise ValueError(f"Ticket '{ticket_id}' has already been fully used.")

            # Consume one use if applicable
            if ticket["uses_remaining"] is not None:
                ticket["uses_remaining"] -= 1
                if ticket["uses_remaining"] == 0:
                    ticket["status"] = self.TicketStatus.USED

            ticket["last_used"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            snapshot = dict(ticket)

        return {
            "ticket_id"    : snapshot["ticket_id"],
            "ticket_type"  : snapshot["ticket_type"],
            "status"       : snapshot["status"],
            "uses_remaining": snapshot["uses_remaining"],
            "expires_at"   : snapshot["expires_at"],
            "last_used"    : snapshot["last_used"],
            "message"      : "Ticket accepted. Have a good journey!",
        }

    # ------------------------------------------------------------------ #
    #  Inspection / account views                                          #
    # ------------------------------------------------------------------ #

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Return current state of a ticket."""
        self._validate_str(ticket_id, "ticket_id")
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise ValueError(f"Ticket '{ticket_id}' not found.")
            self._refresh_status(ticket)
            return dict(ticket)

    def get_session_tickets(self, session_id: str) -> list[dict[str, Any]]:
        """Return all tickets purchased in a session, with live status."""
        self._validate_str(session_id, "session_id")
        with self._lock:
            if session_id not in self._sessions:
                raise ValueError(f"Session '{session_id}' not found.")
            ids = list(self._session_tickets[session_id])
            tickets = []
            for tid in ids:
                t = self._tickets.get(tid)
                if t:
                    self._refresh_status(t)
                    tickets.append(dict(t))
        return tickets

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        """Return a purchase receipt (payment info is pseudonymised)."""
        self._validate_str(receipt_id, "receipt_id")
        with self._lock:
            receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise ValueError(f"Receipt '{receipt_id}' not found.")
        # Never expose the raw payment fingerprint to the caller
        safe = {k: v for k, v in receipt.items() if k != "payment_fingerprint"}
        return safe

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _build_ticket(
        self,
        ticket_id  : str,
        ttype      : str,
        ptype      : str,
        issued_at  : datetime,
    ) -> dict[str, Any]:
        """Construct a new ticket record."""
        validity_days = self._VALIDITY_DAYS[ttype]
        if validity_days is None:
            # SINGLE: valid until end of operating day (midnight)
            expires_at = (issued_at + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            expires_at = issued_at + timedelta(days=validity_days)

        return {
            "ticket_id"     : ticket_id,
            "ticket_type"   : ttype,
            "passenger_type": ptype,
            "status"        : self.TicketStatus.VALID,
            "uses_remaining": self._USES[ttype],    # None = unlimited
            "issued_at"     : issued_at.isoformat(),
            "expires_at"    : expires_at.isoformat(),
            "last_used"     : None,
        }

    def _refresh_status(self, ticket: dict) -> None:
        """Update ticket status in-place if it has expired. Caller holds lock."""
        if ticket["status"] == self.TicketStatus.VALID:
            expires = datetime.fromisoformat(ticket["expires_at"])
            if datetime.now(timezone.utc).replace(tzinfo=None) > expires:
                ticket["status"] = self.TicketStatus.EXPIRED

    def _validity_summary(self, ttype: str, from_dt: datetime) -> dict[str, str]:
        """Human-readable validity window for receipts."""
        days = self._VALIDITY_DAYS[ttype]
        if days is None:
            end = (from_dt + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            end = from_dt + timedelta(days=days)
        return {
            "from"     : from_dt.isoformat(),
            "until"    : end.isoformat(),
            "uses"     : str(self._USES[ttype]) if self._USES[ttype] else "unlimited",
        }

    def _pseudonymise(self, raw: str) -> str:
        """Return an HMAC-SHA256 fingerprint of ``raw``. Not reversible."""
        return hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()

    # --- Input validation helpers -----------------------------------------

    @staticmethod
    def _validate_enum(value: Any, enum_cls: type[Enum], field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{field}' must be a non-empty string.")
        canonical = value.strip().lower() if enum_cls is TransportApp.PassengerType \
                    else value.strip().upper()
        try:
            return enum_cls(canonical).value
        except ValueError:
            valid = [e.value for e in enum_cls]
            raise ValueError(
                f"'{field}' must be one of {valid!r}; got {value!r}."
            )

    @staticmethod
    def _validate_positive_int(value: Any, field: str) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{field}' must be an integer; got {value!r}.")
        if v < 1:
            raise ValueError(f"'{field}' must be ≥ 1; got {v}.")
        if v > 100:
            raise ValueError(f"'{field}' exceeds maximum of 100 per transaction.")
        return v

    @staticmethod
    def _validate_str(value: Any, field: str, *, required: bool = True) -> str:
        if not isinstance(value, str):
            raise ValueError(f"'{field}' must be a string; got {type(value).__name__}.")
        if required and not value.strip():
            raise ValueError(f"'{field}' must not be empty.")
        return value


# ======================================================================== #
#  Demo / smoke-test (run directly: python transport_app.py)               #
# ======================================================================== #

if __name__ == "__main__":
    import concurrent.futures, json

    app = TransportApp(currency="SEK")

    # --- Price list for a student ---
    print("=== Student price list ===")
    for row in app.list_prices("student"):
        print(f"  {row['ticket_type']:10s}  {row['unit_price']:6.2f} {row['currency']}")

    # --- Single purchase ---
    print("\n=== Single purchase (adult, WEEKLY × 2) ===")
    s1 = app.create_session(passenger_type="adult")
    r1 = app.purchase(s1["session_id"], "WEEKLY", quantity=2, payment_token="tok_demo_001")
    print(json.dumps(r1, indent=2))

    # --- Use a ticket ---
    print("\n=== Using first ticket ===")
    use = app.use_ticket(r1["ticket_ids"][0])
    print(json.dumps(use, indent=2))

    # --- Bundle purchase and multi-use ---
    print("\n=== Senior buying BUNDLE_5, then using 3 times ===")
    s2 = app.create_session("senior")
    r2 = app.purchase(s2["session_id"], "BUNDLE_5", payment_token="tok_senior_42")
    tid = r2["ticket_ids"][0]
    for i in range(3):
        u = app.use_ticket(tid)
        print(f"  use {i+1}: remaining={u['uses_remaining']}, status={u['status']}")

    # --- Concurrent purchases (10 threads) ---
    print("\n=== Concurrent purchases (10 threads) ===")
    def buy(i: int) -> str:
        ptype = ["youth", "student", "adult", "senior"][i % 4]
        ttype = ["SINGLE", "DAY", "BUNDLE_10", "MONTHLY"][i % 4]
        s  = app.create_session(ptype)
        r  = app.purchase(s["session_id"], ttype, payment_token=f"tok_{i}")
        return f"Thread {i:02d}: {ptype:7s} {ttype:10s} → {r['total_price']} SEK"

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(buy, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futs):
            print(" ", f.result())

    # --- Error handling ---
    print("\n=== Error handling ===")
    for bad in [
        lambda: app.create_session("alien"),
        lambda: app.calculate_price("ROCKET", "adult"),
        lambda: app.purchase("bad-session-id", "SINGLE"),
        lambda: app.use_ticket("nonexistent-ticket"),
        lambda: app.calculate_price("SINGLE", "adult", quantity=0),
        lambda: app.calculate_price("SINGLE", "adult", quantity=999),
    ]:
        try:
            bad()
        except ValueError as e:
            print(f"  ValueError caught: {e}")

    print("\nAll done.")