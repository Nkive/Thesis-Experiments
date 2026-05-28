"""
Smart Urban Transport System
A comprehensive ticket purchasing system with multi-tier pricing, concurrent transaction support,
error handling, and security features.
"""

import threading
import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from decimal import Decimal, ROUND_HALF_UP
import json


class UserType(Enum):
    """User categories with different pricing"""
    REGULAR = "regular"
    STUDENT = "student"
    SENIOR = "senior"


class TicketType(Enum):
    """Available ticket types"""
    SINGLE = "single"
    DAY_PASS = "day_pass"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class TransactionStatus(Enum):
    """Transaction processing states"""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PricingRule:
    """Pricing configuration for different user types and ticket types"""
    base_price: Decimal
    discount_percentage: Decimal
    
    def calculate_price(self) -> Decimal:
        """Calculate final price after discount"""
        discount = self.base_price * (self.discount_percentage / Decimal('100'))
        final_price = self.base_price - discount
        return final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@dataclass
class Offer:
    """Special promotional offers"""
    offer_id: str
    description: str
    user_type: UserType
    discount_percentage: Decimal
    valid_from: datetime
    valid_until: datetime
    min_tickets: int = 1
    
    def is_valid(self) -> bool:
        """Check if offer is currently valid"""
        now = datetime.now()
        return self.valid_from <= now <= self.valid_until
    
    def applies_to(self, user_type: UserType, ticket_count: int) -> bool:
        """Check if offer applies to given conditions"""
        return (self.user_type == user_type and 
                ticket_count >= self.min_tickets and 
                self.is_valid())


@dataclass
class User:
    """User account with secure information handling"""
    user_id: str
    user_type: UserType
    email_hash: str  # Hashed email for privacy
    payment_token: str  # Tokenized payment info
    created_at: datetime = field(default_factory=datetime.now)
    
    @staticmethod
    def hash_sensitive_data(data: str) -> str:
        """Hash sensitive information for storage"""
        return hashlib.sha256(data.encode()).hexdigest()
    
    @staticmethod
    def generate_secure_token() -> str:
        """Generate secure payment token"""
        return secrets.token_urlsafe(32)


@dataclass
class Ticket:
    """Individual ticket with unique identifier"""
    ticket_id: str
    user_id: str
    ticket_type: TicketType
    user_type: UserType
    price_paid: Decimal
    purchase_date: datetime
    valid_from: datetime
    valid_until: datetime
    is_used: bool = False
    
    def is_valid(self) -> bool:
        """Check if ticket is currently valid"""
        now = datetime.now()
        return self.valid_from <= now <= self.valid_until and not self.is_used
    
    def to_dict(self) -> dict:
        """Convert ticket to dictionary for mobile app"""
        return {
            'ticket_id': self.ticket_id,
            'ticket_type': self.ticket_type.value,
            'user_type': self.user_type.value,
            'price_paid': str(self.price_paid),
            'purchase_date': self.purchase_date.isoformat(),
            'valid_from': self.valid_from.isoformat(),
            'valid_until': self.valid_until.isoformat(),
            'is_used': self.is_used
        }


@dataclass
class Transaction:
    """Transaction record with thread-safe processing"""
    transaction_id: str
    user_id: str
    ticket_count: int
    total_amount: Decimal
    status: TransactionStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    ticket_ids: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


class PricingEngine:
    """Manages pricing rules and calculations"""
    
    def __init__(self):
        # Base prices for ticket types
        self.base_prices = {
            TicketType.SINGLE: Decimal('3.00'),
            TicketType.DAY_PASS: Decimal('10.00'),
            TicketType.WEEKLY: Decimal('35.00'),
            TicketType.MONTHLY: Decimal('120.00')
        }
        
        # Discount percentages by user type
        self.discounts = {
            UserType.REGULAR: Decimal('0'),
            UserType.STUDENT: Decimal('30'),
            UserType.SENIOR: Decimal('40')
        }
        
        # Thread lock for price calculations
        self._lock = threading.Lock()
    
    def get_price(self, ticket_type: TicketType, user_type: UserType, 
                  quantity: int = 1, applied_offer: Optional[Offer] = None) -> Decimal:
        """
        Calculate price with thread-safe operations
        
        Args:
            ticket_type: Type of ticket
            user_type: User category
            quantity: Number of tickets
            applied_offer: Additional promotional offer
        
        Returns:
            Total price as Decimal
        """
        with self._lock:
            try:
                if quantity <= 0:
                    raise ValueError(f"Invalid quantity: {quantity}. Must be positive.")
                
                if ticket_type not in self.base_prices:
                    raise ValueError(f"Invalid ticket type: {ticket_type}")
                
                base = self.base_prices[ticket_type]
                discount = self.discounts.get(user_type, Decimal('0'))
                
                # Apply user type discount
                rule = PricingRule(base, discount)
                unit_price = rule.calculate_price()
                
                # Apply additional offer if applicable
                if applied_offer and applied_offer.applies_to(user_type, quantity):
                    additional_discount = unit_price * (applied_offer.discount_percentage / Decimal('100'))
                    unit_price -= additional_discount
                
                total = (unit_price * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                return total
                
            except Exception as e:
                raise ValueError(f"Price calculation error: {str(e)}")
    
    def get_pricing_breakdown(self, ticket_type: TicketType, user_type: UserType, 
                             quantity: int = 1) -> Dict:
        """Get detailed pricing breakdown for transparency"""
        base = self.base_prices[ticket_type]
        discount = self.discounts[user_type]
        
        discount_amount = base * (discount / Decimal('100'))
        unit_price = base - discount_amount
        total = unit_price * quantity
        
        return {
            'base_price': str(base),
            'discount_percentage': str(discount),
            'discount_amount': str(discount_amount.quantize(Decimal('0.01'))),
            'unit_price': str(unit_price.quantize(Decimal('0.01'))),
            'quantity': quantity,
            'total': str(total.quantize(Decimal('0.01')))
        }


class OfferManager:
    """Manages promotional offers for different user types"""
    
    def __init__(self):
        self.offers: Dict[str, Offer] = {}
        self._lock = threading.Lock()
        self._initialize_default_offers()
    
    def _initialize_default_offers(self):
        """Set up default promotional offers"""
        now = datetime.now()
        
        # Student offer: Buy 5+ tickets, get extra 10% off
        student_bulk = Offer(
            offer_id="STUDENT_BULK_2024",
            description="Buy 5 or more tickets and get an extra 10% off!",
            user_type=UserType.STUDENT,
            discount_percentage=Decimal('10'),
            valid_from=now,
            valid_until=now + timedelta(days=90),
            min_tickets=5
        )
        
        # Senior offer: Weekday special - extra 15% off
        senior_weekday = Offer(
            offer_id="SENIOR_WEEKDAY_2024",
            description="Weekday Special: Extra 15% off for seniors!",
            user_type=UserType.SENIOR,
            discount_percentage=Decimal('15'),
            valid_from=now,
            valid_until=now + timedelta(days=90),
            min_tickets=1
        )
        
        self.add_offer(student_bulk)
        self.add_offer(senior_weekday)
    
    def add_offer(self, offer: Offer):
        """Add new promotional offer"""
        with self._lock:
            self.offers[offer.offer_id] = offer
    
    def get_applicable_offers(self, user_type: UserType, ticket_count: int) -> List[Offer]:
        """Get all applicable offers for user and purchase"""
        with self._lock:
            applicable = []
            for offer in self.offers.values():
                if offer.applies_to(user_type, ticket_count):
                    applicable.append(offer)
            return applicable
    
    def get_best_offer(self, user_type: UserType, ticket_count: int) -> Optional[Offer]:
        """Get the best available offer (highest discount)"""
        offers = self.get_applicable_offers(user_type, ticket_count)
        if not offers:
            return None
        return max(offers, key=lambda o: o.discount_percentage)


class TicketValidator:
    """Validates ticket purchase requests and inputs"""
    
    @staticmethod
    def validate_purchase_request(user: User, ticket_type: TicketType, 
                                  quantity: int) -> Tuple[bool, Optional[str]]:
        """
        Validate purchase request with comprehensive error handling
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Validate user
            if not user or not user.user_id:
                return False, "Invalid user: User ID is required"
            
            if not isinstance(user.user_type, UserType):
                return False, f"Invalid user type: {user.user_type}"
            
            # Validate ticket type
            if not isinstance(ticket_type, TicketType):
                return False, f"Invalid ticket type: {ticket_type}"
            
            # Validate quantity
            if not isinstance(quantity, int):
                return False, f"Invalid quantity type: must be integer, got {type(quantity)}"
            
            if quantity <= 0:
                return False, f"Invalid quantity: {quantity}. Must be greater than 0"
            
            if quantity > 50:  # Reasonable limit for bulk purchases
                return False, f"Quantity too high: {quantity}. Maximum 50 tickets per transaction"
            
            # Validate payment token
            if not user.payment_token or len(user.payment_token) < 16:
                return False, "Invalid payment information"
            
            return True, None
            
        except Exception as e:
            return False, f"Validation error: {str(e)}"
    
    @staticmethod
    def sanitize_input(value: str, max_length: int = 100) -> str:
        """Sanitize string inputs to prevent injection attacks"""
        if not isinstance(value, str):
            raise ValueError("Input must be a string")
        
        # Remove potentially dangerous characters
        sanitized = ''.join(char for char in value if char.isalnum() or char in ' ._-@')
        return sanitized[:max_length]


class TransactionManager:
    """Thread-safe transaction processing"""
    
    def __init__(self):
        self.transactions: Dict[str, Transaction] = {}
        self._lock = threading.Lock()
        self._transaction_counter = 0
    
    def create_transaction(self, user_id: str, ticket_count: int, 
                          total_amount: Decimal) -> Transaction:
        """Create new transaction with unique ID"""
        with self._lock:
            self._transaction_counter += 1
            transaction_id = f"TXN{int(time.time())}{self._transaction_counter:06d}"
            
            transaction = Transaction(
                transaction_id=transaction_id,
                user_id=user_id,
                ticket_count=ticket_count,
                total_amount=total_amount,
                status=TransactionStatus.PENDING,
                created_at=datetime.now()
            )
            
            self.transactions[transaction_id] = transaction
            return transaction
    
    def complete_transaction(self, transaction_id: str, ticket_ids: List[str]):
        """Mark transaction as completed"""
        with self._lock:
            if transaction_id in self.transactions:
                transaction = self.transactions[transaction_id]
                transaction.status = TransactionStatus.COMPLETED
                transaction.completed_at = datetime.now()
                transaction.ticket_ids = ticket_ids
    
    def fail_transaction(self, transaction_id: str, error_message: str):
        """Mark transaction as failed"""
        with self._lock:
            if transaction_id in self.transactions:
                transaction = self.transactions[transaction_id]
                transaction.status = TransactionStatus.FAILED
                transaction.error_message = error_message


class SmartTransportSystem:
    """Main system orchestrating all components"""
    
    def __init__(self):
        self.users: Dict[str, User] = {}
        self.tickets: Dict[str, Ticket] = {}
        self.pricing_engine = PricingEngine()
        self.offer_manager = OfferManager()
        self.transaction_manager = TransactionManager()
        self.validator = TicketValidator()
        
        # Thread locks for concurrent operations
        self._user_lock = threading.Lock()
        self._ticket_lock = threading.Lock()
        self._ticket_counter = 0
    
    def register_user(self, email: str, user_type: UserType, 
                     payment_info: str) -> Tuple[Optional[User], Optional[str]]:
        """
        Register new user with secure data handling
        
        Returns:
            Tuple of (User object or None, error message or None)
        """
        try:
            # Sanitize inputs
            email = self.validator.sanitize_input(email)
            
            # Validate user type
            if not isinstance(user_type, UserType):
                return None, f"Invalid user type: {user_type}"
            
            with self._user_lock:
                # Generate secure IDs and tokens
                user_id = f"USR{secrets.token_hex(8).upper()}"
                email_hash = User.hash_sensitive_data(email)
                payment_token = User.generate_secure_token()
                
                # Create user
                user = User(
                    user_id=user_id,
                    user_type=user_type,
                    email_hash=email_hash,
                    payment_token=payment_token
                )
                
                self.users[user_id] = user
                return user, None
                
        except Exception as e:
            return None, f"Registration error: {str(e)}"
    
    def purchase_tickets(self, user_id: str, ticket_type: TicketType, 
                        quantity: int = 1) -> Tuple[Optional[List[Ticket]], Optional[str]]:
        """
        Purchase tickets through mobile application with full error handling
        
        Args:
            user_id: User identifier
            ticket_type: Type of ticket to purchase
            quantity: Number of tickets to buy
        
        Returns:
            Tuple of (List of Ticket objects or None, error message or None)
        """
        try:
            # Get user
            with self._user_lock:
                user = self.users.get(user_id)
                if not user:
                    return None, f"User not found: {user_id}"
            
            # Validate purchase request
            is_valid, error_msg = self.validator.validate_purchase_request(
                user, ticket_type, quantity
            )
            if not is_valid:
                return None, error_msg
            
            # Get best applicable offer
            best_offer = self.offer_manager.get_best_offer(user.user_type, quantity)
            
            # Calculate total price
            try:
                total_price = self.pricing_engine.get_price(
                    ticket_type, user.user_type, quantity, best_offer
                )
            except ValueError as e:
                return None, str(e)
            
            # Create transaction
            transaction = self.transaction_manager.create_transaction(
                user_id, quantity, total_price
            )
            
            # Generate tickets with thread safety
            tickets = []
            try:
                with self._ticket_lock:
                    for i in range(quantity):
                        self._ticket_counter += 1
                        ticket_id = f"TKT{int(time.time())}{self._ticket_counter:08d}"
                        
                        # Calculate validity period
                        now = datetime.now()
                        valid_from = now
                        
                        if ticket_type == TicketType.SINGLE:
                            valid_until = now + timedelta(hours=2)
                        elif ticket_type == TicketType.DAY_PASS:
                            valid_until = now.replace(hour=23, minute=59, second=59)
                        elif ticket_type == TicketType.WEEKLY:
                            valid_until = now + timedelta(days=7)
                        elif ticket_type == TicketType.MONTHLY:
                            valid_until = now + timedelta(days=30)
                        else:
                            valid_until = now + timedelta(hours=2)
                        
                        # Create ticket
                        ticket = Ticket(
                            ticket_id=ticket_id,
                            user_id=user_id,
                            ticket_type=ticket_type,
                            user_type=user.user_type,
                            price_paid=total_price / quantity,
                            purchase_date=now,
                            valid_from=valid_from,
                            valid_until=valid_until
                        )
                        
                        self.tickets[ticket_id] = ticket
                        tickets.append(ticket)
                
                # Complete transaction
                ticket_ids = [t.ticket_id for t in tickets]
                self.transaction_manager.complete_transaction(
                    transaction.transaction_id, ticket_ids
                )
                
                return tickets, None
                
            except Exception as e:
                # Rollback on error
                self.transaction_manager.fail_transaction(
                    transaction.transaction_id, str(e)
                )
                return None, f"Ticket generation error: {str(e)}"
                
        except Exception as e:
            return None, f"Purchase error: {str(e)}"
    
    def get_ticket_info(self, ticket_id: str) -> Optional[Dict]:
        """Retrieve ticket information for mobile app"""
        with self._ticket_lock:
            ticket = self.tickets.get(ticket_id)
            if ticket:
                return ticket.to_dict()
            return None
    
    def get_user_tickets(self, user_id: str) -> List[Dict]:
        """Get all tickets for a user"""
        with self._ticket_lock:
            user_tickets = [
                ticket.to_dict() 
                for ticket in self.tickets.values() 
                if ticket.user_id == user_id
            ]
            return user_tickets
    
    def get_pricing_info(self, ticket_type: TicketType, user_type: UserType, 
                        quantity: int = 1) -> Dict:
        """Get pricing information for mobile app display"""
        try:
            breakdown = self.pricing_engine.get_pricing_breakdown(
                ticket_type, user_type, quantity
            )
            
            # Add applicable offers
            offers = self.offer_manager.get_applicable_offers(user_type, quantity)
            breakdown['available_offers'] = [
                {
                    'offer_id': offer.offer_id,
                    'description': offer.description,
                    'discount': str(offer.discount_percentage) + '%'
                }
                for offer in offers
            ]
            
            return breakdown
        except Exception as e:
            return {'error': str(e)}
    
    def validate_ticket(self, ticket_id: str) -> Tuple[bool, str]:
        """Validate ticket for use"""
        with self._ticket_lock:
            ticket = self.tickets.get(ticket_id)
            
            if not ticket:
                return False, "Ticket not found"
            
            if ticket.is_used:
                return False, "Ticket already used"
            
            if not ticket.is_valid():
                return False, "Ticket expired"
            
            # Mark as used
            ticket.is_used = True
            return True, "Ticket valid"
    
    def get_system_stats(self) -> Dict:
        """Get system statistics for monitoring"""
        with self._user_lock, self._ticket_lock:
            stats = {
                'total_users': len(self.users),
                'total_tickets_issued': len(self.tickets),
                'active_tickets': sum(1 for t in self.tickets.values() if t.is_valid()),
                'users_by_type': {
                    'regular': sum(1 for u in self.users.values() if u.user_type == UserType.REGULAR),
                    'student': sum(1 for u in self.users.values() if u.user_type == UserType.STUDENT),
                    'senior': sum(1 for u in self.users.values() if u.user_type == UserType.SENIOR)
                },
                'transactions': {
                    'completed': sum(1 for t in self.transaction_manager.transactions.values() 
                                   if t.status == TransactionStatus.COMPLETED),
                    'failed': sum(1 for t in self.transaction_manager.transactions.values() 
                                if t.status == TransactionStatus.FAILED)
                }
            }
            return stats


def demo_concurrent_purchases():
    """Demonstrate concurrent ticket purchases"""
    print("=" * 70)
    print("SMART URBAN TRANSPORT SYSTEM - DEMONSTRATION")
    print("=" * 70)
    
    # Initialize system
    system = SmartTransportSystem()
    
    # Register different user types
    print("\n1. REGISTERING USERS")
    print("-" * 70)
    
    regular_user, _ = system.register_user("john@email.com", UserType.REGULAR, "payment_info_1")
    student_user, _ = system.register_user("alice@student.edu", UserType.STUDENT, "payment_info_2")
    senior_user, _ = system.register_user("bob@senior.com", UserType.SENIOR, "payment_info_3")
    
    print(f"✓ Regular User: {regular_user.user_id}")
    print(f"✓ Student User: {student_user.user_id}")
    print(f"✓ Senior User: {senior_user.user_id}")
    
    # Show pricing differences
    print("\n2. PRICING FOR DIFFERENT USER TYPES (Single Ticket)")
    print("-" * 70)
    
    for user_type in [UserType.REGULAR, UserType.STUDENT, UserType.SENIOR]:
        info = system.get_pricing_info(TicketType.SINGLE, user_type, 1)
        print(f"\n{user_type.value.upper()}:")
        print(f"  Base Price: ${info['base_price']}")
        print(f"  Discount: {info['discount_percentage']}%")
        print(f"  Final Price: ${info['total']}")
    
    # Show special offers
    print("\n3. SPECIAL OFFERS")
    print("-" * 70)
    
    student_offers = system.offer_manager.get_applicable_offers(UserType.STUDENT, 5)
    senior_offers = system.offer_manager.get_applicable_offers(UserType.SENIOR, 1)
    
    print("\nSTUDENT OFFERS:")
    for offer in student_offers:
        print(f"  • {offer.description}")
    
    print("\nSENIOR OFFERS:")
    for offer in senior_offers:
        print(f"  • {offer.description}")
    
    # Simulate concurrent purchases
    print("\n4. CONCURRENT TICKET PURCHASES")
    print("-" * 70)
    
    def purchase_thread(user, ticket_type, quantity, label):
        tickets, error = system.purchase_tickets(user.user_id, ticket_type, quantity)
        if tickets:
            total = sum(t.price_paid for t in tickets)
            print(f"✓ {label}: Purchased {quantity} {ticket_type.value} ticket(s) for ${total:.2f}")
        else:
            print(f"✗ {label}: Error - {error}")
    
    # Create multiple threads to simulate concurrent purchases
    threads = [
        threading.Thread(target=purchase_thread, args=(regular_user, TicketType.SINGLE, 2, "Regular User")),
        threading.Thread(target=purchase_thread, args=(student_user, TicketType.WEEKLY, 1, "Student User")),
        threading.Thread(target=purchase_thread, args=(senior_user, TicketType.DAY_PASS, 3, "Senior User")),
        threading.Thread(target=purchase_thread, args=(student_user, TicketType.SINGLE, 6, "Student Bulk")),
    ]
    
    # Start all threads
    for thread in threads:
        thread.start()
    
    # Wait for all to complete
    for thread in threads:
        thread.join()
    
    # Error handling demonstration
    print("\n5. ERROR HANDLING EXAMPLES")
    print("-" * 70)
    
    # Test invalid quantity
    _, error = system.purchase_tickets(regular_user.user_id, TicketType.SINGLE, -5)
    print(f"Invalid quantity: {error}")
    
    # Test invalid user
    _, error = system.purchase_tickets("INVALID_USER", TicketType.SINGLE, 1)
    print(f"Invalid user: {error}")
    
    # Test excessive quantity
    _, error = system.purchase_tickets(regular_user.user_id, TicketType.SINGLE, 100)
    print(f"Excessive quantity: {error}")
    
    # System statistics
    print("\n6. SYSTEM STATISTICS")
    print("-" * 70)
    
    stats = system.get_system_stats()
    print(f"Total Users: {stats['total_users']}")
    print(f"Total Tickets Issued: {stats['total_tickets_issued']}")
    print(f"Active Tickets: {stats['active_tickets']}")
    print(f"\nUsers by Type:")
    print(f"  Regular: {stats['users_by_type']['regular']}")
    print(f"  Student: {stats['users_by_type']['student']}")
    print(f"  Senior: {stats['users_by_type']['senior']}")
    print(f"\nTransactions:")
    print(f"  Completed: {stats['transactions']['completed']}")
    print(f"  Failed: {stats['transactions']['failed']}")
    
    # Show user tickets
    print("\n7. USER TICKET DETAILS")
    print("-" * 70)
    
    for user in [regular_user, student_user, senior_user]:
        tickets = system.get_user_tickets(user.user_id)
        print(f"\n{user.user_type.value.upper()} ({user.user_id}):")
        for ticket in tickets:
            print(f"  • {ticket['ticket_id']}: {ticket['ticket_type']} - ${ticket['price_paid']}")
    
    print("\n" + "=" * 70)
    print("DEMONSTRATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo_concurrent_purchases()