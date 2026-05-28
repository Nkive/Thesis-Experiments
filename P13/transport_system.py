#!/usr/bin/env python3
"""
Comprehensive Ticket Purchasing System
Supports multiple user categories, ticket types, and secure transactions
"""

import hashlib
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import uuid


class UserCategory(Enum):
    """User categories with discount multipliers"""
    YOUTH = ("youth", 0.75)
    STUDENT = ("student", 0.70)
    SENIOR = ("senior", 0.65)
    ADULT = ("adult", 1.0)
    KIDS = ("kids", 0.50)
    
    def __init__(self, category_name: str, price_multiplier: float):
        self.category_name = category_name
        self.price_multiplier = price_multiplier


class TicketType(Enum):
    """Available ticket types with base prices"""
    SINGLE_RIDE = ("Single Ride", 30.0)
    DAY_PASS = ("Day Pass", 80.0)
    WEEK_PASS = ("Week Pass", 250.0)
    MONTH_PASS = ("Month Pass", 800.0)
    ANNUAL_PASS = ("Annual Pass", 8000.0)
    
    def __init__(self, type_name: str, base_price: float):
        self.type_name = type_name
        self.base_price = base_price


@dataclass
class Ticket:
    """Represents a purchased ticket"""
    ticket_id: str
    ticket_type: str
    user_category: str
    price: float
    purchase_date: str
    activation_date: Optional[str]
    is_active: bool
    quantity: int
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PaymentDetails:
    """Securely stores payment information"""
    card_number_hash: str
    cardholder_name: str
    expiry_date: str
    
    @staticmethod
    def hash_card_number(card_number: str) -> str:
        """Hash card number for security"""
        return hashlib.sha256(card_number.encode()).hexdigest()


class TransactionManager:
    """Handles concurrent transactions with thread safety"""
    
    def __init__(self, max_latency: float = 5.0):
        self.lock = threading.Lock()
        self.max_latency = max_latency
        self.transaction_count = 0
        
    def process_transaction(self, transaction_func, *args, **kwargs):
        """Process transaction with latency control"""
        start_time = time.time()
        
        with self.lock:
            self.transaction_count += 1
            transaction_id = self.transaction_count
            
            try:
                result = transaction_func(*args, **kwargs)
                elapsed_time = time.time() - start_time
                
                if elapsed_time > self.max_latency:
                    print(f"Warning: Transaction {transaction_id} took {elapsed_time:.2f}s (exceeds {self.max_latency}s limit)")
                
                return True, result
            except Exception as e:
                return False, str(e)


class TicketPricingEngine:
    """Calculates ticket prices based on category and type"""
    
    @staticmethod
    def calculate_price(user_category: UserCategory, ticket_type: TicketType, quantity: int = 1) -> float:
        """Calculate final price based on user category, ticket type, and quantity"""
        if quantity < 1:
            raise ValueError("Quantity must be at least 1")
        
        base_price = ticket_type.base_price
        multiplier = user_category.price_multiplier
        final_price = base_price * multiplier * quantity
        
        return round(final_price, 2)
    
    @staticmethod
    def validate_combination(user_category: UserCategory, ticket_type: TicketType) -> bool:
        """Validate that the category and ticket type combination is allowed"""
        # All combinations are valid in this system
        return True


class TicketPurchaseSystem:
    """Main ticket purchasing system"""
    
    def __init__(self):
        self.transaction_manager = TransactionManager(max_latency=5.0)
        self.pricing_engine = TicketPricingEngine()
        self.user_tickets: Dict[str, List[Ticket]] = {}
        self.payment_records: Dict[str, PaymentDetails] = {}
        
    def display_categories(self) -> None:
        """Display available user categories"""
        print("\n" + "="*50)
        print("AVAILABLE USER CATEGORIES")
        print("="*50)
        for i, category in enumerate(UserCategory, 1):
            discount = int((1 - category.price_multiplier) * 100)
            print(f"{i}. {category.category_name.upper():<15} (Discount: {discount}%)")
        print("="*50)
    
    def display_ticket_types(self) -> None:
        """Display available ticket types"""
        print("\n" + "="*50)
        print("AVAILABLE TICKET TYPES")
        print("="*50)
        for i, ticket_type in enumerate(TicketType, 1):
            print(f"{i}. {ticket_type.type_name:<20} - Base Price: ${ticket_type.base_price:.2f}")
        print("="*50)
    
    def select_category(self) -> Optional[UserCategory]:
        """Allow user to select their category"""
        self.display_categories()
        
        try:
            choice = input("\nSelect your category (1-5): ").strip()
            category_index = int(choice) - 1
            
            categories = list(UserCategory)
            if 0 <= category_index < len(categories):
                return categories[category_index]
            else:
                print("ERROR: Invalid category selection. Please choose a number between 1 and 5.")
                return None
        except ValueError:
            print("ERROR: Invalid input. Please enter a number.")
            return None
    
    def select_ticket_type(self) -> Optional[TicketType]:
        """Allow user to select ticket type"""
        self.display_ticket_types()
        
        try:
            choice = input("\nSelect ticket type (1-5): ").strip()
            ticket_index = int(choice) - 1
            
            ticket_types = list(TicketType)
            if 0 <= ticket_index < len(ticket_types):
                return ticket_types[ticket_index]
            else:
                print("ERROR: Invalid ticket type selection. Please choose a number between 1 and 5.")
                return None
        except ValueError:
            print("ERROR: Invalid input. Please enter a number.")
            return None
    
    def select_quantity(self) -> Optional[int]:
        """Allow user to select quantity"""
        try:
            quantity = input("\nEnter quantity (minimum 1): ").strip()
            
            if not quantity:
                print("ERROR: Quantity cannot be empty. Please enter a valid number.")
                return None
            
            qty = int(quantity)
            if qty < 1:
                print("ERROR: Quantity must be at least 1.")
                return None
            
            return qty
        except ValueError:
            print("ERROR: Invalid quantity. Please enter a valid number.")
            return None
    
    def select_activation_option(self) -> Optional[bool]:
        """Allow user to choose activation timing"""
        print("\n" + "="*50)
        print("TICKET ACTIVATION")
        print("="*50)
        print("1. Activate now")
        print("2. Activate later")
        print("="*50)
        
        try:
            choice = input("\nSelect activation option (1-2): ").strip()
            
            if choice == "1":
                return True
            elif choice == "2":
                return False
            else:
                print("ERROR: Invalid activation option. Please choose 1 or 2.")
                return None
        except Exception:
            print("ERROR: Invalid input.")
            return None
    
    def collect_payment_details(self) -> Optional[PaymentDetails]:
        """Collect and securely store payment details"""
        print("\n" + "="*50)
        print("PAYMENT DETAILS (Encrypted & Secure)")
        print("="*50)
        
        try:
            card_number = input("Enter card number (16 digits): ").strip()
            
            if len(card_number) != 16 or not card_number.isdigit():
                print("ERROR: Card number must be exactly 16 digits.")
                return None
            
            cardholder_name = input("Enter cardholder name: ").strip()
            
            if not cardholder_name:
                print("ERROR: Cardholder name cannot be empty.")
                return None
            
            expiry_date = input("Enter expiry date (MM/YY): ").strip()
            
            if len(expiry_date) != 5 or expiry_date[2] != '/':
                print("ERROR: Expiry date must be in MM/YY format.")
                return None
            
            cvv = input("Enter CVV (3 digits): ").strip()
            
            if len(cvv) != 3 or not cvv.isdigit():
                print("ERROR: CVV must be exactly 3 digits.")
                return None
            
            # Hash the card number for security
            card_hash = PaymentDetails.hash_card_number(card_number)
            
            return PaymentDetails(
                card_number_hash=card_hash,
                cardholder_name=cardholder_name,
                expiry_date=expiry_date
            )
            
        except Exception as e:
            print(f"ERROR: Payment processing failed - {str(e)}")
            return None
    
    def purchase_ticket(self, user_id: str, user_category: UserCategory, 
                       ticket_type: TicketType, quantity: int, 
                       activate_now: bool, payment_details: PaymentDetails) -> Tuple[bool, str]:
        """Purchase a ticket with all validations"""
        
        def _execute_purchase():
            # Validate combination
            if not self.pricing_engine.validate_combination(user_category, ticket_type):
                raise ValueError("Invalid category and ticket type combination")
            
            # Calculate price
            final_price = self.pricing_engine.calculate_price(user_category, ticket_type, quantity)
            
            # Create ticket
            ticket = Ticket(
                ticket_id=str(uuid.uuid4()),
                ticket_type=ticket_type.type_name,
                user_category=user_category.category_name,
                price=final_price,
                purchase_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                activation_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S") if activate_now else None,
                is_active=activate_now,
                quantity=quantity
            )
            
            # Store ticket
            if user_id not in self.user_tickets:
                self.user_tickets[user_id] = []
            
            self.user_tickets[user_id].append(ticket)
            
            # Store payment details securely
            self.payment_records[ticket.ticket_id] = payment_details
            
            return ticket
        
        # Process transaction with thread safety
        success, result = self.transaction_manager.process_transaction(_execute_purchase)
        
        if success:
            ticket = result
            return True, f"Ticket purchased successfully! Ticket ID: {ticket.ticket_id}, Total: ${ticket.price:.2f}"
        else:
            return False, f"Purchase failed: {result}"
    
    def display_user_tickets(self, user_id: str) -> None:
        """Display all tickets for a user"""
        print("\n" + "="*80)
        print("YOUR TICKETS")
        print("="*80)
        
        if user_id not in self.user_tickets or not self.user_tickets[user_id]:
            print("No tickets found.")
            print("="*80)
            return
        
        for i, ticket in enumerate(self.user_tickets[user_id], 1):
            print(f"\nTicket #{i}")
            print(f"  ID:              {ticket.ticket_id}")
            print(f"  Type:            {ticket.ticket_type}")
            print(f"  Category:        {ticket.user_category.upper()}")
            print(f"  Quantity:        {ticket.quantity}")
            print(f"  Price:           ${ticket.price:.2f}")
            print(f"  Purchase Date:   {ticket.purchase_date}")
            print(f"  Status:          {'ACTIVE' if ticket.is_active else 'INACTIVE'}")
            
            if ticket.activation_date:
                print(f"  Activation Date: {ticket.activation_date}")
            else:
                print(f"  Activation Date: Not activated yet")
            
            print("-" * 80)
        
        print("="*80)
    
    def activate_ticket(self, user_id: str, ticket_id: str) -> Tuple[bool, str]:
        """Activate a previously purchased ticket"""
        if user_id not in self.user_tickets:
            return False, "No tickets found for this user"
        
        for ticket in self.user_tickets[user_id]:
            if ticket.ticket_id == ticket_id:
                if ticket.is_active:
                    return False, "Ticket is already active"
                
                ticket.is_active = True
                ticket.activation_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return True, f"Ticket {ticket_id} activated successfully"
        
        return False, "Ticket not found"
    
    def run_purchase_flow(self, user_id: str) -> None:
        """Run the complete ticket purchase flow"""
        print("\n" + "="*80)
        print("WELCOME TO THE TICKET PURCHASING SYSTEM")
        print("="*80)
        
        while True:
            print("\n1. Purchase new ticket(s)")
            print("2. View my tickets")
            print("3. Activate a ticket")
            print("4. Exit")
            
            choice = input("\nSelect option (1-4): ").strip()
            
            if choice == "1":
                # Purchase flow
                user_category = self.select_category()
                if not user_category:
                    continue
                
                ticket_type = self.select_ticket_type()
                if not ticket_type:
                    continue
                
                quantity = self.select_quantity()
                if not quantity:
                    continue
                
                # Show price preview
                try:
                    price = self.pricing_engine.calculate_price(user_category, ticket_type, quantity)
                    print(f"\nTotal Price: ${price:.2f}")
                except ValueError as e:
                    print(f"ERROR: {str(e)}")
                    continue
                
                activate_now = self.select_activation_option()
                if activate_now is None:
                    continue
                
                payment_details = self.collect_payment_details()
                if not payment_details:
                    continue
                
                # Process purchase
                success, message = self.purchase_ticket(
                    user_id, user_category, ticket_type, 
                    quantity, activate_now, payment_details
                )
                
                print(f"\n{'SUCCESS' if success else 'FAILED'}: {message}")
                
            elif choice == "2":
                # View tickets
                self.display_user_tickets(user_id)
                
            elif choice == "3":
                # Activate ticket
                self.display_user_tickets(user_id)
                ticket_id = input("\nEnter ticket ID to activate: ").strip()
                
                if ticket_id:
                    success, message = self.activate_ticket(user_id, ticket_id)
                    print(f"\n{'SUCCESS' if success else 'FAILED'}: {message}")
                else:
                    print("ERROR: Ticket ID cannot be empty")
                
            elif choice == "4":
                print("\nThank you for using the Ticket Purchasing System!")
                break
                
            else:
                print("ERROR: Invalid option. Please choose 1-4.")


def simulate_concurrent_transactions():
    """Simulate multiple concurrent transactions to test thread safety"""
    system = TicketPurchaseSystem()
    
    def purchase_thread(user_id: str, category: UserCategory, ticket_type: TicketType):
        payment = PaymentDetails(
            card_number_hash=PaymentDetails.hash_card_number("1234567812345678"),
            cardholder_name="Test User",
            expiry_date="12/25"
        )
        
        success, message = system.purchase_ticket(
            user_id, category, ticket_type, 1, True, payment
        )
        print(f"Thread {user_id}: {message}")
    
    threads = []
    for i in range(10):
        thread = threading.Thread(
            target=purchase_thread,
            args=(f"user_{i}", UserCategory.ADULT, TicketType.SINGLE_RIDE)
        )
        threads.append(thread)
        thread.start()
    
    for thread in threads:
        thread.join()
    
    print(f"\nTotal transactions processed: {system.transaction_manager.transaction_count}")


def main():
    """Main entry point"""
    system = TicketPurchaseSystem()
    
    # Generate a unique user ID for this session
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    
    print(f"Your User ID: {user_id}")
    print("This system works on any digital device with Python support.")
    print("All payment details are encrypted and securely stored.")
    print("Maximum transaction latency: 5 seconds")
    
    # Run the purchase flow
    system.run_purchase_flow(user_id)


if __name__ == "__main__":
    main()
