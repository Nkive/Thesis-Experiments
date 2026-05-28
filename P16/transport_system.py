"""
Public Transport Ticketing Service — single-class implementation.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any
import uuid


class PassengerCategory(str, Enum):
    ADULT     = "adult"
    STUDENT   = "student"
    TEENAGER  = "teenager"
    SENIOR    = "senior"


class TicketType(str, Enum):
    SINGLE_RIDE   = "single_ride"
    PERIOD_7_DAY  = "period_7_day"
    PERIOD_30_DAY = "period_30_day"
    BUNDLE_10     = "bundle_10"
    BUNDLE_20     = "bundle_20"


@dataclass
class LineItem:
    ticket_type: TicketType
    quantity: int
    unit_price: Decimal
    line_total: Decimal


@dataclass
class PurchaseQuote:
    quote_id: str
    passenger_category: PassengerCategory
    items: list[LineItem]
    subtotal: Decimal
    currency: str = "SEK"


class TicketingService:

    BASE_PRICES: dict[TicketType, Decimal] = {
        TicketType.SINGLE_RIDE:   Decimal("35.00"),
        TicketType.PERIOD_7_DAY:  Decimal("290.00"),
        TicketType.PERIOD_30_DAY: Decimal("890.00"),
        TicketType.BUNDLE_10:     Decimal("300.00"),
        TicketType.BUNDLE_20:     Decimal("550.00"),
    }

    DISCOUNTS: dict[PassengerCategory, Decimal] = {
        PassengerCategory.ADULT:    Decimal("1.00"),
        PassengerCategory.STUDENT:  Decimal("0.60"),
        PassengerCategory.TEENAGER: Decimal("0.50"),
        PassengerCategory.SENIOR:   Decimal("0.55"),
    }

    MAX_QUANTITY = 50
    MAX_ITEMS    = 20

    # ── Public entry point ────────────────────────────────────────────────────

    def purchase(self, payload: dict[str, Any]) -> PurchaseQuote:
        """
        Validate, price, and return a PurchaseQuote.
        Raises ValueError with a list of human-readable messages on bad input.
        """
        errors = self._validate(payload)
        if errors:
            raise ValueError(errors)

        category = PassengerCategory(payload["passenger_category"])
        items = self._price_items(payload["items"], category)
        subtotal = sum((i.line_total for i in items), Decimal("0.00"))

        return PurchaseQuote(
            quote_id=str(uuid.uuid4()),
            passenger_category=category,
            items=items,
            subtotal=subtotal.quantize(Decimal("0.01"), ROUND_HALF_UP),
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []

        if not payload.get("passenger_category"):
            errors.append("passenger_category is required.")
        elif payload["passenger_category"] not in PassengerCategory._value2member_map_:
            errors.append(f"Invalid passenger_category '{payload['passenger_category']}'. "
                          f"Accepted: {[c.value for c in PassengerCategory]}.")

        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            errors.append("items must be a non-empty list.")
            return errors  # can't proceed without items

        if len(raw_items) > self.MAX_ITEMS:
            errors.append(f"Cannot exceed {self.MAX_ITEMS} line items per purchase.")

        for i, item in enumerate(raw_items):
            errors.extend(self._validate_item(item, index=i))

        return errors

    def _validate_item(self, item: Any, index: int) -> list[str]:
        errors: list[str] = []
        prefix = f"items[{index}]"

        if not isinstance(item, dict):
            return [f"{prefix} must be an object with 'ticket_type' and 'quantity'."]

        if not item.get("ticket_type"):
            errors.append(f"{prefix}.ticket_type is required.")
        elif item["ticket_type"] not in TicketType._value2member_map_:
            errors.append(f"{prefix}.ticket_type '{item['ticket_type']}' is invalid. "
                          f"Accepted: {[t.value for t in TicketType]}.")

        qty = item.get("quantity")
        if qty is None:
            errors.append(f"{prefix}.quantity is required — you selected a ticket without specifying how many.")
        elif not isinstance(qty, int) or isinstance(qty, bool):
            errors.append(f"{prefix}.quantity must be an integer.")
        elif qty < 1:
            errors.append(f"{prefix}.quantity must be at least 1.")
        elif qty > self.MAX_QUANTITY:
            errors.append(f"{prefix}.quantity cannot exceed {self.MAX_QUANTITY}.")

        return errors

    # ── Pricing ───────────────────────────────────────────────────────────────

    def _price_items(self, raw_items: list[dict], category: PassengerCategory) -> list[LineItem]:
        result = []
        for item in raw_items:
            unit = (self.BASE_PRICES[TicketType(item["ticket_type"])] * self.DISCOUNTS[category]
                    ).quantize(Decimal("0.01"), ROUND_HALF_UP)
            qty  = item["quantity"]
            result.append(LineItem(
                ticket_type=TicketType(item["ticket_type"]),
                quantity=qty,
                unit_price=unit,
                line_total=(unit * qty).quantize(Decimal("0.01"), ROUND_HALF_UP),
            ))
        return result
