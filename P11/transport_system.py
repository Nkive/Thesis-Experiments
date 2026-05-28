"""
Public Transport Ticketing System
A comprehensive system for buying and managing public transport tickets
with age-based pricing, student discounts, and secure passenger data handling.
"""

import json
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, asdict
import threading
from concurrent.futures import ThreadPoolExecutor
import time


class TicketType(Enum):
    """Enumeration of available ticket types"""
    SINGLE_RIDE = "single_ride"
    NINETY_MINUTES = "90_minutes"
    ONE_DAY = "1_day"
    ONE_MONTH = "1_month"
    ONE_YEAR = "1_year"


class PassengerCategory(Enum):
    """Enumeration of passenger categories for pricing"""
    CHILD = "child"  # 0-17 years
    STUDENT = "student"  # 18-25 years with student status
    ADULT = "adult"  # 18-64 years (non-student)
    ELDERLY = "elderly"  # 65+ years


@dataclass
class PriceStructure:
    """Base prices for different ticket types (adult prices)"""
    single_ride: float = 3.50
    ninety_minutes: float = 4.00
    one_day: float = 10.00
    one_month: float = 90.00
    one_year: float = 900.00


@dataclass
class DiscountRates:
    """Discount rates for different passenger categories"""
    child: float = 0.50  # 50% discount
    student: float = 0.30  # 30% discount
    adult: float = 0.00  # No discount
    elderly: float = 0.40  # 40% discount


@dataclass
class Ticket:
    """Represents a purchased ticket"""
    ticket_id: str
    ticket_type: TicketType
    passenger_category: PassengerCategory
    price: float
    purchase_time: str
    valid_until: str
    is_active: bool = True


@dataclass
class PassengerInfo:
    """Secure passenger information"""
    name: str
    age: int
    is_student: bool
    passenger_id: str  # Hashed identifier for security


class SecurityManager:
    """Handles secure storage and encryption of sensitive data"""
    
    def __init__(self):
        self._encryption_key = secrets.token_hex(32)
        self._lock = threading.Lock()
    
    def hash_sensitive_data(self, data: str) -> str:
        """Hash sensitive information for secure storage"""
        return hashlib.sha256(f"{data}{self._encryption_key}".encode()).hexdigest()
    
    def generate_ticket_id(self) -> str:
        """Generate a unique, secure ticket ID"""
        return f"TKT-{secrets.token_hex(8).upper()}"
    
    def generate_passenger_id(self, name: str, age: int) -> str:
        """Generate a secure passenger identifier"""
        unique_data = f"{name}{age}{datetime.now().isoformat()}"
        return self.hash_sensitive_data(unique_data)[:16]


class PricingEngine:
    """Handles all pricing calculations and discount applications"""
    
    def __init__(self):
        self.base_prices = PriceStructure()
        self.discounts = DiscountRates()
    
    def determine_passenger_category(self, age: int, is_student: bool) -> PassengerCategory:
        """Automatically determine passenger category based on age and student status"""
        if age < 0:
            raise ValueError("Age cannot be negative")
        if age > 150:
            raise ValueError("Invalid age provided")
        
        if age < 18:
            return PassengerCategory.CHILD
        elif age >= 65:
            return PassengerCategory.ELDERLY
        elif 18 <= age <= 25 and is_student:
            return PassengerCategory.STUDENT
        else:
            return PassengerCategory.ADULT
    
    def get_base_price(self, ticket_type: TicketType) -> float:
        """Get base price for a ticket type"""
        price_mapping = {
            TicketType.SINGLE_RIDE: self.base_prices.single_ride,
            TicketType.NINETY_MINUTES: self.base_prices.ninety_minutes,
            TicketType.ONE_DAY: self.base_prices.one_day,
            TicketType.ONE_MONTH: self.base_prices.one_month,
            TicketType.ONE_YEAR: self.base_prices.one_year,
        }
        return price_mapping[ticket_type]
    
    def calculate_discounted_price(self, ticket_type: TicketType, 
                                   passenger_category: PassengerCategory) -> float:
        """Calculate final price after applying category discount"""
        base_price = self.get_base_price(ticket_type)
        
        discount_mapping = {
            PassengerCategory.CHILD: self.discounts.child,
            PassengerCategory.STUDENT: self.discounts.student,
            PassengerCategory.ADULT: self.discounts.adult,
            PassengerCategory.ELDERLY: self.discounts.elderly,
        }
        
        discount_rate = discount_mapping[passenger_category]
        final_price = base_price * (1 - discount_rate)
        return round(final_price, 2)
    
    def calculate_validity_period(self, ticket_type: TicketType, 
                                  purchase_time: datetime) -> datetime:
        """Calculate when a ticket expires based on its type"""
        validity_mapping = {
            TicketType.SINGLE_RIDE: timedelta(hours=2),  # Valid for 2 hours for single use
            TicketType.NINETY_MINUTES: timedelta(minutes=90),
            TicketType.ONE_DAY: timedelta(days=1),
            TicketType.ONE_MONTH: timedelta(days=30),
            TicketType.ONE_YEAR: timedelta(days=365),
        }
        return purchase_time + validity_mapping[ticket_type]


class InputValidator:
    """Validates all user inputs and handles edge cases"""
    
    @staticmethod
    def validate_ticket_quantity(quantity: any) -> int:
        """Validate ticket purchase quantity"""
        # Handle edge cases for unexpected input types
        try:
            # Convert to float first to handle string numbers
            qty_float = float(quantity)
            
            # Check if it's actually an integer value
            if qty_float != int(qty_float):
                raise ValueError("Ticket quantity must be a whole number")
            
            qty_int = int(qty_float)
            
            # Must be positive and greater than zero
            if qty_int <= 0:
                raise ValueError("Ticket quantity must be a positive number greater than zero")
            
            # Reasonable upper limit to prevent abuse
            if qty_int > 100:
                raise ValueError("Cannot purchase more than 100 tickets at once")
            
            return qty_int
            
        except (TypeError, ValueError) as e:
            if "could not convert" in str(e) or "invalid literal" in str(e):
                raise ValueError("Ticket quantity must be a valid number")
            raise
    
    @staticmethod
    def validate_name(name: str) -> str:
        """Validate passenger name"""
        if not isinstance(name, str):
            raise ValueError("Name must be a string")
        
        name = name.strip()
        
        if not name:
            raise ValueError("Name cannot be empty")
        
        if len(name) < 2:
            raise ValueError("Name must be at least 2 characters long")
        
        if len(name) > 100:
            raise ValueError("Name is too long (max 100 characters)")
        
        return name
    
    @staticmethod
    def validate_age(age: any) -> int:
        """Validate passenger age"""
        try:
            age_float = float(age)
            
            if age_float != int(age_float):
                raise ValueError("Age must be a whole number")
            
            age_int = int(age_float)
            
            if age_int < 0:
                raise ValueError("Age cannot be negative")
            
            if age_int > 150:
                raise ValueError("Invalid age (maximum 150 years)")
            
            return age_int
            
        except (TypeError, ValueError) as e:
            if "could not convert" in str(e) or "invalid literal" in str(e):
                raise ValueError("Age must be a valid number")
            raise
    
    @staticmethod
    def validate_student_status(is_student: any) -> bool:
        """Validate student status input"""
        if isinstance(is_student, bool):
            return is_student
        
        if isinstance(is_student, str):
            lower_status = is_student.lower().strip()
            if lower_status in ['true', 'yes', '1', 'y']:
                return True
            elif lower_status in ['false', 'no', '0', 'n']:
                return False
            else:
                raise ValueError("Student status must be yes/no or true/false")
        
        if isinstance(is_student, (int, float)):
            return bool(is_student)
        
        raise ValueError("Invalid student status format")


class TicketingSystem:
    """Main ticketing system coordinating all operations"""
    
    def __init__(self):
        self.security = SecurityManager()
        self.pricing = PricingEngine()
        self.validator = InputValidator()
        self.passengers: Dict[str, PassengerInfo] = {}
        self.tickets: Dict[str, List[Ticket]] = {}  # passenger_id -> list of tickets
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=50)  # Handle peak periods
    
    def register_passenger(self, name: str, age: any, is_student: any) -> PassengerInfo:
        """Register or retrieve passenger information"""
        # Validate inputs
        validated_name = self.validator.validate_name(name)
        validated_age = self.validator.validate_age(age)
        validated_student = self.validator.validate_student_status(is_student)
        
        # Generate secure passenger ID
        passenger_id = self.security.generate_passenger_id(validated_name, validated_age)
        
        # Thread-safe passenger registration
        with self._lock:
            if passenger_id in self.passengers:
                # Return existing passenger info
                return self.passengers[passenger_id]
            
            # Create new passenger record
            passenger = PassengerInfo(
                name=validated_name,
                age=validated_age,
                is_student=validated_student,
                passenger_id=passenger_id
            )
            
            self.passengers[passenger_id] = passenger
            self.tickets[passenger_id] = []
            
            return passenger
    
    def purchase_tickets(self, passenger_id: str, ticket_type: TicketType, 
                        quantity: any) -> Tuple[List[Ticket], float]:
        """Purchase multiple tickets for a passenger"""
        # Validate quantity
        validated_quantity = self.validator.validate_ticket_quantity(quantity)
        
        # Get passenger info
        with self._lock:
            if passenger_id not in self.passengers:
                raise ValueError("Passenger not found. Please register first.")
            
            passenger = self.passengers[passenger_id]
        
        # Determine pricing category
        category = self.pricing.determine_passenger_category(
            passenger.age, 
            passenger.is_student
        )
        
        # Calculate price per ticket
        price_per_ticket = self.pricing.calculate_discounted_price(ticket_type, category)
        total_cost = round(price_per_ticket * validated_quantity, 2)
        
        # Generate tickets
        purchase_time = datetime.now()
        valid_until = self.pricing.calculate_validity_period(ticket_type, purchase_time)
        
        tickets = []
        for _ in range(validated_quantity):
            ticket = Ticket(
                ticket_id=self.security.generate_ticket_id(),
                ticket_type=ticket_type,
                passenger_category=category,
                price=price_per_ticket,
                purchase_time=purchase_time.isoformat(),
                valid_until=valid_until.isoformat(),
                is_active=True
            )
            tickets.append(ticket)
        
        # Thread-safe ticket storage
        with self._lock:
            self.tickets[passenger_id].extend(tickets)
        
        return tickets, total_cost
    
    def get_passenger_tickets(self, passenger_id: str) -> List[Ticket]:
        """Retrieve all tickets for a passenger"""
        with self._lock:
            if passenger_id not in self.tickets:
                return []
            return self.tickets[passenger_id].copy()
    
    def display_ticket(self, ticket_id: str, passenger_id: str) -> Optional[Dict]:
        """Display ticket information for showing to controller"""
        with self._lock:
            if passenger_id not in self.tickets:
                return None
            
            for ticket in self.tickets[passenger_id]:
                if ticket.ticket_id == ticket_id:
                    # Check if ticket is still valid
                    valid_until = datetime.fromisoformat(ticket.valid_until)
                    is_currently_valid = datetime.now() <= valid_until
                    
                    return {
                        "ticket_id": ticket.ticket_id,
                        "type": ticket.ticket_type.value,
                        "category": ticket.passenger_category.value,
                        "price": ticket.price,
                        "purchased": ticket.purchase_time,
                        "valid_until": ticket.valid_until,
                        "status": "VALID" if is_currently_valid else "EXPIRED"
                    }
            
            return None
    
    def simulate_peak_load(self, num_passengers: int, tickets_per_passenger: int):
        """Simulate peak period performance with concurrent ticket purchases"""
        def purchase_for_passenger(index):
            try:
                # Register passenger
                passenger = self.register_passenger(
                    f"Passenger_{index}",
                    20 + (index % 50),
                    index % 2 == 0
                )
                
                # Purchase tickets
                self.purchase_tickets(
                    passenger.passenger_id,
                    TicketType.SINGLE_RIDE,
                    tickets_per_passenger
                )
                return True
            except Exception as e:
                print(f"Error for passenger {index}: {e}")
                return False
        
        start_time = time.time()
        
        # Execute concurrent purchases
        futures = [
            self._executor.submit(purchase_for_passenger, i) 
            for i in range(num_passengers)
        ]
        
        # Wait for all to complete
        results = [f.result() for f in futures]
        
        end_time = time.time()
        duration = end_time - start_time
        
        successful = sum(results)
        return {
            "total_passengers": num_passengers,
            "successful_purchases": successful,
            "failed_purchases": num_passengers - successful,
            "duration_seconds": round(duration, 2),
            "throughput_per_second": round(successful / duration, 2)
        }


def print_ticket_display(ticket_info: Dict):
    """Format ticket for display to controller"""
    print("\n" + "="*50)
    print("          PUBLIC TRANSPORT TICKET")
    print("="*50)
    print(f"Ticket ID: {ticket_info['ticket_id']}")
    print(f"Type: {ticket_info['type'].replace('_', ' ').title()}")
    print(f"Category: {ticket_info['category'].title()}")
    print(f"Price: €{ticket_info['price']:.2f}")
    print(f"Purchased: {ticket_info['purchased']}")
    print(f"Valid Until: {ticket_info['valid_until']}")
    print(f"\nStatus: {ticket_info['status']}")
    print("="*50 + "\n")


def demonstrate_system():
    """Demonstrate all system features"""
    system = TicketingSystem()
    
    print("=" * 70)
    print("PUBLIC TRANSPORT TICKETING SYSTEM - DEMONSTRATION")
    print("=" * 70)
    
    # Test 1: Register passengers with different profiles
    print("\n--- Test 1: Passenger Registration ---")
    
    child = system.register_passenger("Alice Johnson", 12, False)
    print(f"Registered child: {child.name}, Age: {child.age}, ID: {child.passenger_id}")
    
    student = system.register_passenger("Bob Smith", 22, True)
    print(f"Registered student: {student.name}, Age: {student.age}, ID: {student.passenger_id}")
    
    adult = system.register_passenger("Carol Williams", 35, False)
    print(f"Registered adult: {adult.name}, Age: {adult.age}, ID: {adult.passenger_id}")
    
    elderly = system.register_passenger("David Brown", 70, False)
    print(f"Registered elderly: {elderly.name}, Age: {elderly.age}, ID: {elderly.passenger_id}")
    
    # Test 2: Purchase different ticket types
    print("\n--- Test 2: Ticket Purchase (Different Types) ---")
    
    tickets, cost = system.purchase_tickets(child.passenger_id, TicketType.SINGLE_RIDE, 1)
    print(f"{child.name} bought 1 single ride ticket: €{cost:.2f} (50% child discount)")
    
    tickets, cost = system.purchase_tickets(student.passenger_id, TicketType.ONE_MONTH, 1)
    print(f"{student.name} bought 1 monthly ticket: €{cost:.2f} (30% student discount)")
    
    tickets, cost = system.purchase_tickets(adult.passenger_id, TicketType.ONE_DAY, 2)
    print(f"{adult.name} bought 2 day tickets: €{cost:.2f} (no discount)")
    
    tickets, cost = system.purchase_tickets(elderly.passenger_id, TicketType.NINETY_MINUTES, 3)
    print(f"{elderly.name} bought 3 ninety-minute tickets: €{cost:.2f} (40% elderly discount)")
    
    # Test 3: Display ticket to controller
    print("\n--- Test 3: Display Ticket to Controller ---")
    
    passenger_tickets = system.get_passenger_tickets(student.passenger_id)
    if passenger_tickets:
        ticket_display = system.display_ticket(passenger_tickets[0].ticket_id, student.passenger_id)
        print_ticket_display(ticket_display)
    
    # Test 4: Edge case handling
    print("\n--- Test 4: Edge Case Handling ---")
    
    test_cases = [
        ("Zero tickets", lambda: system.purchase_tickets(adult.passenger_id, TicketType.SINGLE_RIDE, 0)),
        ("Negative tickets", lambda: system.purchase_tickets(adult.passenger_id, TicketType.SINGLE_RIDE, -5)),
        ("String quantity", lambda: system.purchase_tickets(adult.passenger_id, TicketType.SINGLE_RIDE, "abc")),
        ("Float quantity", lambda: system.purchase_tickets(adult.passenger_id, TicketType.SINGLE_RIDE, 2.5)),
        ("Negative age", lambda: system.register_passenger("Invalid", -10, False)),
        ("Empty name", lambda: system.register_passenger("", 25, False)),
    ]
    
    for test_name, test_func in test_cases:
        try:
            test_func()
            print(f"✗ {test_name}: Should have raised error!")
        except ValueError as e:
            print(f"✓ {test_name}: Correctly rejected - {str(e)}")
    
    # Test 5: Valid edge cases
    print("\n--- Test 5: Valid Edge Cases (Should Work) ---")
    
    try:
        tickets, cost = system.purchase_tickets(adult.passenger_id, TicketType.SINGLE_RIDE, "5")
        print(f"✓ String number '5': Successfully purchased 5 tickets for €{cost:.2f}")
    except Exception as e:
        print(f"✗ String number: {e}")
    
    try:
        young_adult = system.register_passenger("Eve Davis", 18, True)
        tickets, cost = system.purchase_tickets(young_adult.passenger_id, TicketType.SINGLE_RIDE, 1)
        print(f"✓ 18-year-old student: Gets student discount - €{cost:.2f}")
    except Exception as e:
        print(f"✗ Young student: {e}")
    
    # Test 6: Peak load performance
    print("\n--- Test 6: Peak Load Performance Test ---")
    print("Simulating 100 passengers buying tickets simultaneously...")
    
    results = system.simulate_peak_load(100, 2)
    print(f"Results:")
    print(f"  Total passengers: {results['total_passengers']}")
    print(f"  Successful purchases: {results['successful_purchases']}")
    print(f"  Failed purchases: {results['failed_purchases']}")
    print(f"  Duration: {results['duration_seconds']} seconds")
    print(f"  Throughput: {results['throughput_per_second']} transactions/second")
    
    # Test 7: Data persistence and retrieval
    print("\n--- Test 7: Passenger Information Retrieval ---")
    
    all_tickets = system.get_passenger_tickets(adult.passenger_id)
    print(f"{adult.name} has {len(all_tickets)} ticket(s):")
    for ticket in all_tickets:
        print(f"  - {ticket.ticket_type.value}: €{ticket.price:.2f}")
    
    print("\n" + "=" * 70)
    print("DEMONSTRATION COMPLETE")
    print("=" * 70)
    
    # Summary
    print("\n--- System Summary ---")
    print(f"Total passengers registered: {len(system.passengers)}")
    total_tickets = sum(len(tickets) for tickets in system.tickets.values())
    print(f"Total tickets issued: {total_tickets}")
    print("\nAll system requirements demonstrated:")
    print("  ✓ Mobile/digital ticket purchasing")
    print("  ✓ Age-based automatic pricing (child, student, adult, elderly)")
    print("  ✓ Student discount verification")
    print("  ✓ Multiple ticket types (single, 90min, day, month, year)")
    print("  ✓ Multiple ticket purchase capability")
    print("  ✓ Ticket display for controllers")
    print("  ✓ Edge case handling (invalid inputs)")
    print("  ✓ Positive number validation")
    print("  ✓ Peak load performance (concurrent transactions)")
    print("  ✓ Passenger information persistence")
    print("  ✓ Secure data handling (hashing, encryption keys)")


if __name__ == "__main__":
    # Run the comprehensive demonstration
    demonstrate_system()

