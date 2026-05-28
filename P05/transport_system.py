from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
from typing import Any, Dict, List

from flask import Flask, jsonify, request

# ============================================================
# Public Transportation Ticket App
# ============================================================
# Features:
# - Single ticket, period ticket, bundle ticket
# - Adult, student, senior passenger categories
# - Discounts for student and senior
# - One order can contain many ticket items
# - Persistent storage with SQLite
# - Concurrent-safe writes using transactions + lock
# - Clear validation and error handling
#
# Run:
#   pip install flask
#   python transport_ticket_app.py
#
# Open:
#   http://127.0.0.1:5000
# ============================================================

app = Flask(__name__)

DB_PATH = os.environ.get("TICKET_APP_DB", "ticket_app.db")
WRITE_LOCK = threading.RLock()

CENTS = Decimal("0.01")


def money(value: Decimal | str | int | float) -> Decimal:
    """Always keep money with 2 decimals."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


class PassengerType(str, Enum):
    ADULT = "adult"
    STUDENT = "student"
    SENIOR = "senior"


class TicketType(str, Enum):
    SINGLE = "single"
    PERIOD = "period"
    BUNDLE = "bundle"


TICKET_CATALOG: Dict[TicketType, Dict[str, Any]] = {
    TicketType.SINGLE: {
        "name": "Single Ticket",
        "base_price": money("40.00"),
        "description": "One trip within the valid zone.",
        "validity_minutes": 90,
    },
    TicketType.PERIOD: {
        "name": "30-Day Period Ticket",
        "base_price": money("970.00"),
        "description": "Unlimited travel for 30 days.",
        "validity_days": 30,
    },
    TicketType.BUNDLE: {
        "name": "10-Trip Bundle",
        "base_price": money("320.00"),
        "description": "Bundle with 10 trips.",
        "bundle_size": 10,
    },
}

DISCOUNT_RATES: Dict[PassengerType, Decimal] = {
    PassengerType.ADULT: money("0.00"),
    PassengerType.STUDENT: money("0.25"),
    PassengerType.SENIOR: money("0.30"),
}


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_passenger_type(value: Any) -> PassengerType:
    if not isinstance(value, str):
        raise AppError("passenger_type must be a string.")
    try:
        return PassengerType(value.strip().lower())
    except ValueError:
        valid = ", ".join(t.value for t in PassengerType)
        raise AppError(f"Invalid passenger_type. Use one of: {valid}.")


def parse_ticket_type(value: Any) -> TicketType:
    if not isinstance(value, str):
        raise AppError("ticket_type must be a string.")
    try:
        return TicketType(value.strip().lower())
    except ValueError:
        valid = ", ".join(t.value for t in TicketType)
        raise AppError(f"Invalid ticket_type. Use one of: {valid}.")


def positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise AppError(f"{field_name} must be a positive integer.")
    return value


def non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError(f"{field_name} is required.")
    return value.strip()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._initialize()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    passenger_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    total_price TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    order_item_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    ticket_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price_before_discount TEXT NOT NULL,
                    discount_rate TEXT NOT NULL,
                    unit_price_after_discount TEXT NOT NULL,
                    line_total TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    extra_json TEXT,
                    FOREIGN KEY(order_id) REFERENCES orders(order_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
                CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
                """
            )


db = Database(DB_PATH)


class TicketService:
    def list_catalog(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for ticket_type, info in TICKET_CATALOG.items():
            item: Dict[str, Any] = {
                "ticket_type": ticket_type.value,
                "name": info["name"],
                "base_price": str(info["base_price"]),
                "description": info["description"],
            }
            if "validity_minutes" in info:
                item["validity_minutes"] = info["validity_minutes"]
            if "validity_days" in info:
                item["validity_days"] = info["validity_days"]
            if "bundle_size" in info:
                item["bundle_size"] = info["bundle_size"]
            items.append(item)
        return items

    def create_user(self, name: Any, passenger_type: Any) -> Dict[str, Any]:
        clean_name = non_empty_string(name, "name")
        passenger = parse_passenger_type(passenger_type)

        user = {
            "user_id": str(uuid.uuid4()),
            "name": clean_name,
            "passenger_type": passenger.value,
            "created_at": utc_now_iso(),
        }

        with WRITE_LOCK:
            with db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO users (user_id, name, passenger_type, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        user["user_id"],
                        user["name"],
                        user["passenger_type"],
                        user["created_at"],
                    ),
                )

        return user

    def get_user(self, user_id: str) -> Dict[str, Any]:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT user_id, name, passenger_type, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        if row is None:
            raise NotFoundError("User not found.")

        return dict(row)

    def create_order(self, user_id: Any, items: Any) -> Dict[str, Any]:
        user_id = non_empty_string(user_id, "user_id")

        if not isinstance(items, list) or len(items) == 0:
            raise AppError("items must be a non-empty list.")

        user = self.get_user(user_id)
        passenger_type = PassengerType(user["passenger_type"])
        discount_rate = DISCOUNT_RATES[passenger_type]

        validated_items: List[Dict[str, Any]] = []
        total_price = money("0.00")

        current_time = datetime.now(timezone.utc)

        for index, raw_item in enumerate(items, start=1):
            if not isinstance(raw_item, dict):
                raise AppError(f"Each item must be an object. Problem at item {index}.")

            ticket_type = parse_ticket_type(raw_item.get("ticket_type"))
            quantity = positive_int(raw_item.get("quantity"), f"quantity for item {index}")

            ticket_info = TICKET_CATALOG[ticket_type]
            base_price = ticket_info["base_price"]
            unit_after_discount = money(base_price * (Decimal("1.00") - discount_rate))
            line_total = money(unit_after_discount * quantity)

            valid_from = current_time.isoformat()
            valid_until = None
            extra_json = None

            if ticket_type == TicketType.SINGLE:
                valid_until = (current_time + timedelta(minutes=ticket_info["validity_minutes"])).isoformat()
            elif ticket_type == TicketType.PERIOD:
                valid_until = (current_time + timedelta(days=ticket_info["validity_days"])).isoformat()
            elif ticket_type == TicketType.BUNDLE:
                valid_until = None
                extra_json = f'{{"bundle_size": {ticket_info["bundle_size"]}, "remaining_trips": {ticket_info["bundle_size"] * quantity}}}'

            validated_item = {
                "order_item_id": str(uuid.uuid4()),
                "ticket_type": ticket_type.value,
                "quantity": quantity,
                "unit_price_before_discount": str(base_price),
                "discount_rate": str(discount_rate),
                "unit_price_after_discount": str(unit_after_discount),
                "line_total": str(line_total),
                "valid_from": valid_from,
                "valid_until": valid_until,
                "extra_json": extra_json,
            }
            validated_items.append(validated_item)
            total_price = money(total_price + line_total)

        order = {
            "order_id": str(uuid.uuid4()),
            "user_id": user_id,
            "total_price": str(total_price),
            "created_at": utc_now_iso(),
            "items": validated_items,
        }

        # One atomic write transaction for the full order.
        # If one insert fails, the full order is rolled back.
        with WRITE_LOCK:
            with db.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO orders (order_id, user_id, total_price, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        order["order_id"],
                        order["user_id"],
                        order["total_price"],
                        order["created_at"],
                    ),
                )

                conn.executemany(
                    """
                    INSERT INTO order_items (
                        order_item_id,
                        order_id,
                        ticket_type,
                        quantity,
                        unit_price_before_discount,
                        discount_rate,
                        unit_price_after_discount,
                        line_total,
                        valid_from,
                        valid_until,
                        extra_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item["order_item_id"],
                            order["order_id"],
                            item["ticket_type"],
                            item["quantity"],
                            item["unit_price_before_discount"],
                            item["discount_rate"],
                            item["unit_price_after_discount"],
                            item["line_total"],
                            item["valid_from"],
                            item["valid_until"],
                            item["extra_json"],
                        )
                        for item in validated_items
                    ],
                )

        return order

    def get_user_orders(self, user_id: Any) -> List[Dict[str, Any]]:
        user_id = non_empty_string(user_id, "user_id")
        self.get_user(user_id)

        with db.connection() as conn:
            order_rows = conn.execute(
                """
                SELECT order_id, user_id, total_price, created_at
                FROM orders
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()

            results: List[Dict[str, Any]] = []

            for order_row in order_rows:
                item_rows = conn.execute(
                    """
                    SELECT
                        order_item_id,
                        ticket_type,
                        quantity,
                        unit_price_before_discount,
                        discount_rate,
                        unit_price_after_discount,
                        line_total,
                        valid_from,
                        valid_until,
                        extra_json
                    FROM order_items
                    WHERE order_id = ?
                    ORDER BY rowid ASC
                    """,
                    (order_row["order_id"],),
                ).fetchall()

                results.append(
                    {
                        "order_id": order_row["order_id"],
                        "user_id": order_row["user_id"],
                        "total_price": order_row["total_price"],
                        "created_at": order_row["created_at"],
                        "items": [dict(item_row) for item_row in item_rows],
                    }
                )

        return results


service = TicketService()


@app.errorhandler(AppError)
def handle_app_error(error: AppError):
    return jsonify({"error": str(error)}), error.status_code


@app.errorhandler(404)
def handle_404(_error):
    return jsonify({"error": "Endpoint not found."}), 404


@app.errorhandler(405)
def handle_405(_error):
    return jsonify({"error": "Method not allowed for this endpoint."}), 405


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    return jsonify({"error": f"Unexpected server error: {str(error)}"}), 500


@app.get("/")
def home():
    return jsonify(
        {
            "message": "Public Transportation Ticket API is running.",
            "endpoints": {
                "GET /catalog": "List all ticket types",
                "POST /users": "Create a user",
                "GET /users/<user_id>": "Get a user",
                "POST /orders": "Buy one or more tickets in a single order",
                "GET /users/<user_id>/orders": "Get order history for one user",
                "GET /health": "Health check",
            },
            "example_create_user": {
                "name": "Parham",
                "passenger_type": "student",
            },
            "example_create_order": {
                "user_id": "PUT_USER_ID_HERE",
                "items": [
                    {"ticket_type": "single", "quantity": 2},
                    {"ticket_type": "bundle", "quantity": 1},
                    {"ticket_type": "period", "quantity": 1},
                ],
            },
        }
    )


@app.get("/health")
def health():
    with db.connection() as conn:
        conn.execute("SELECT 1")
    return jsonify({"status": "ok", "time": utc_now_iso()})


@app.get("/catalog")
def catalog():
    return jsonify(service.list_catalog())


@app.post("/users")
def create_user():
    data = request.get_json(silent=True)
    if data is None:
        raise AppError("Request body must be valid JSON.")
    user = service.create_user(
        name=data.get("name"),
        passenger_type=data.get("passenger_type"),
    )
    return jsonify({"message": "User created successfully.", "user": user}), 201


@app.get("/users/<user_id>")
def get_user(user_id: str):
    user = service.get_user(user_id)
    return jsonify(user)


@app.post("/orders")
def create_order():
    data = request.get_json(silent=True)
    if data is None:
        raise AppError("Request body must be valid JSON.")
    order = service.create_order(
        user_id=data.get("user_id"),
        items=data.get("items"),
    )
    return jsonify({"message": "Order created successfully.", "order": order}), 201


@app.get("/users/<user_id>/orders")
def get_user_orders(user_id: str):
    orders = service.get_user_orders(user_id)
    return jsonify(orders)


if __name__ == "__main__":
    # For real production, use gunicorn or another WSGI server instead of Flask's built-in server.
    # Example:
    #   gunicorn -w 4 -b 0.0.0.0:5000 transport_ticket_app:app
    app.run(host="0.0.0.0", port=5000, threaded=True)

