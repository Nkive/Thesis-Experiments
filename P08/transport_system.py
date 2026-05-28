# ==============================
# Global Pricing Configuration
# ==============================
PRICING = {
    "base_price": 26,
    "youth_price": 5,
    "discounts": {
        "student": 0.15,
        "elderly": 0.50
    }
}


# ==============================
# Ticket Price Function
# ==============================
def calculate_single_ride_price(user_type: str) -> float:
    user_type = user_type.lower()

    if user_type == "youth":
        return PRICING["youth_price"]

    elif user_type == "student":
        return PRICING["base_price"] * (1 - PRICING["discounts"]["student"])

    elif user_type == "elderly":
        return PRICING["base_price"] * (1 - PRICING["discounts"]["elderly"])

    elif user_type == "common":
        return PRICING["base_price"]

    else:
        raise ValueError("Invalid user type")


# ==============================
# Main Program
# ==============================
def main():
    print("=== Public Transport Ticket System ===")

    while True:
        print("\nAvailable user types:")
        print("1. Common")
        print("2. Youth (10–19)")
        print("3. Student")
        print("4. Elderly")

        choice = input("Select user type (1-4) or 'q' to quit: ").strip()

        if choice.lower() == 'q':
            print("Exiting system. Goodbye!")
            break

        user_map = {
            "1": "common",
            "2": "youth",
            "3": "student",
            "4": "elderly"
        }

        if choice not in user_map:
            print("❌ Invalid choice. Try again.")
            continue

        user_type = user_map[choice]

        try:
            price = calculate_single_ride_price(user_type)
            print(f"✅ Ticket price for {user_type}: {price:.2f} SEK")
        except ValueError as e:
            print(f"Error: {e}")

        # Allow multiple tickets (requirement 5)
        again = input("\nBuy another ticket? (y/n): ").strip().lower()
        if again != 'y':
            print("Thank you for using the system!")
            break


# ==============================
# Run Program
# ==============================
if __name__ == "__main__":
    main()
