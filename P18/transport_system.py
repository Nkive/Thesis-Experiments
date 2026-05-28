from dataclasses import dataclass
from enum import Enum
from typing import List


# -------------------------
# Passenger Categories
# -------------------------
class PassengerType(Enum):
    REGULAR = "regular"
    STUDENT = "student"
    SENIOR = "senior"


# -------------------------
# Ticket Types
# -------------------------
class TicketType(Enum):
    SINGLE = "single"
    PASS_24H = "24h_pass"
    BUNDLE_10 = "bundle_10"


# -------------------------
# Ticket Prices (base system)
# -------------------------
TICKET_PRICES = {
    TicketType.SINGLE: 3.0,
    TicketType.PASS_24H: 10.0,
    TicketType.BUNDLE_10: 25.0,
}


# -------------------------
# Discount Rules
# -------------------------
DISCOUNTS = {
    PassengerType.REGULAR: 0.0,
    PassengerType.STUDENT: 0.30,  # 30% off
    PassengerType.SENIOR: 0.40,   # 40% off
}


# -------------------------
# Passenger Model
# -------------------------
@dataclass
class Passenger:
    name: str
    passenger_type: PassengerType


# -------------------------
# Validation
# -------------------------
def validate_quantity(quantity: int):
    if not isinstance(quantity, int):
        raise ValueError("Quantity must be an integer.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than 0.")


# -------------------------
# Price Calculation
# -------------------------
def calculate_ticket_price(
    passenger: Passenger,
    ticket_type: TicketType,
    quantity: int = 1
) -> float:

    validate_quantity(quantity)

    base_price = TICKET_PRICES[ticket_type]
    discount = DISCOUNTS[passenger.passenger_type]

    total = base_price * quantity
    discounted_total = total * (1 - discount)

    return round(discounted_total, 2)


# -------------------------
# Multiple Ticket Purchase
# -------------------------
def purchase_tickets(
    passenger: Passenger,
    orders: List[tuple]
) -> float:
    """
    orders format:
    [(TicketType.SINGLE, 2), (TicketType.PASS_24H, 1)]
    """

    if not orders:
        raise ValueError("No tickets selected.")

    total_cost = 0.0

    for ticket_type, qty in orders:
        if ticket_type not in TicketType:
            raise ValueError(f"Invalid ticket type: {ticket_type}")

        total_cost += calculate_ticket_price(passenger, ticket_type, qty)

    return round(total_cost, 2)