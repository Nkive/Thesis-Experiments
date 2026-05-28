from enum import Enum
import hashlib


class PassengerType(Enum):
    REGULAR = "regular"
    STUDENT = "student"
    SENIOR = "senior"


class TicketType(Enum):
    SINGLE = "single"
    PERIOD = "period"
    BUNDLE = "bundle"


BASE_PRICES = {
    TicketType.SINGLE: 3.00,
    TicketType.PERIOD: 25.00,
    TicketType.BUNDLE: 20.00
}


DISCOUNTS = {
    PassengerType.REGULAR: 0.0,
    PassengerType.STUDENT: 0.2,
    PassengerType.SENIOR: 0.3
}


# 🔒 Simulated eligibility database (hashed IDs only)
ELIGIBILITY_DB = {
    # Pretend these are verified users
}


def hash_id(user_id: str):
    return hashlib.sha256(user_id.encode()).hexdigest()


def verify_eligibility(user_hash: str, passenger_type: PassengerType):
    """
    Simulates checking eligibility from a secure system.
    In real life, this would call an external verified service.
    """
    # Example rule: only users in DB are verified
    return ELIGIBILITY_DB.get(user_hash) == passenger_type


class Passenger:
    def __init__(self, passenger_type: PassengerType, user_id: str):
        self.passenger_type = passenger_type
        self.user_hash = hash_id(user_id)

        # 🔍 Verify eligibility
        self.is_verified = verify_eligibility(self.user_hash, passenger_type)

    def get_discount(self):
        # ❗ Only apply discount if verified
        if self.is_verified:
            return DISCOUNTS[self.passenger_type]
        return 0.0


class Ticket:
    def __init__(self, ticket_type: TicketType, quantity):
        if not isinstance(quantity, int):
            raise TypeError("Quantity must be an integer.")

        if quantity <= 0:
            raise ValueError("Quantity must be greater than 0.")

        self.ticket_type = ticket_type
        self.quantity = quantity

    def get_base_price(self):
        return BASE_PRICES[self.ticket_type] * self.quantity


class Order:
    def __init__(self, passenger: Passenger):
        self.passenger = passenger
        self.tickets = []

    def add_ticket(self, ticket: Ticket):
        self.tickets.append(ticket)

    def calculate_total(self):
        total = sum(ticket.get_base_price() for ticket in self.tickets)

        discount = self.passenger.get_discount()
        final_total = total * (1 - discount)

        return round(final_total, 2)


# ---- Example usage ----

def main():
    try:
        # Simulate adding a verified student
        test_user_id = "student@email.com"
        test_hash = hash_id(test_user_id)
        ELIGIBILITY_DB[test_hash] = PassengerType.STUDENT

        passenger = Passenger(PassengerType.STUDENT, test_user_id)

        order = Order(passenger)
        order.add_ticket(Ticket(TicketType.SINGLE, 2))
        order.add_ticket(Ticket(TicketType.BUNDLE, 1))

        total_price = order.calculate_total()

        print(f"Total price: ${total_price}")
        print(f"Verified: {passenger.is_verified}")

    except (ValueError, TypeError) as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
