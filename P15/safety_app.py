"""
Student Helper Application System
A scalable system for managing safety companion requests between students
"""

import uuid
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import time


class RequestStatus(Enum):
    """Status states for helper requests"""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class HelperStatus(Enum):
    """Availability status for helpers"""
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"


@dataclass
class Location:
    """Represents a geographic location"""
    latitude: float
    longitude: float
    description: str = ""
    
    def __str__(self):
        return f"{self.description}" if self.description else f"({self.latitude}, {self.longitude})"


@dataclass
class Student:
    """Student user in the system"""
    student_id: str
    name: str
    phone: str
    email: str
    anonymous_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    def get_public_info(self) -> Dict:
        """Returns anonymized student information for helpers"""
        return {
            "anonymous_id": self.anonymous_id,
            "initials": f"{self.name.split()[0][0]}.{self.name.split()[-1][0]}." if len(self.name.split()) > 1 else self.name[0] + "."
        }


@dataclass
class Helper:
    """Volunteer helper in the system"""
    helper_id: str
    name: str
    phone: str
    email: str
    status: HelperStatus = HelperStatus.AVAILABLE
    current_location: Optional[Location] = None
    rating: float = 5.0
    total_assists: int = 0
    
    def get_public_info(self) -> Dict:
        """Returns helper information for students"""
        return {
            "helper_id": self.helper_id,
            "name": self.name,
            "rating": round(self.rating, 1),
            "total_assists": self.total_assists
        }


@dataclass
class HelpRequest:
    """Request for walking companion assistance"""
    request_id: str
    student_id: str
    pickup_location: Location
    destination: Location
    requested_time: datetime
    status: RequestStatus = RequestStatus.PENDING
    assigned_helper_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    accepted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: str = ""
    
    def get_helper_view(self, student_public_info: Dict) -> Dict:
        """Returns request information visible to helpers"""
        return {
            "request_id": self.request_id,
            "student": student_public_info,
            "pickup_location": str(self.pickup_location),
            "destination": str(self.destination),
            "requested_time": self.requested_time.strftime("%Y-%m-%d %H:%M"),
            "status": self.status.value,
            "notes": self.notes
        }


@dataclass
class Message:
    """Secure message between student and helper"""
    message_id: str
    request_id: str
    sender_id: str
    recipient_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    is_read: bool = False


class StudentHelperSystem:
    """
    Main system class for managing student safety helper requests.
    Handles request creation, helper assignment, communication, and scalability.
    """
    
    def __init__(self):
        # Core data storage
        self.students: Dict[str, Student] = {}
        self.helpers: Dict[str, Helper] = {}
        self.requests: Dict[str, HelpRequest] = {}
        self.messages: Dict[str, List[Message]] = {}  # Keyed by request_id
        
        # Thread-safe locks for concurrent access
        self.request_lock = threading.RLock()
        self.helper_lock = threading.RLock()
        self.student_lock = threading.RLock()
        self.message_lock = threading.RLock()
        
        # Assignment tracking to prevent conflicts
        self.pending_assignments: Dict[str, str] = {}  # request_id -> helper_id
        self.assignment_lock = threading.RLock()
        
    # ==================== User Management ====================
    
    def register_student(self, student_id: str, name: str, phone: str, email: str) -> Student:
        """Register a new student in the system"""
        with self.student_lock:
            if student_id in self.students:
                raise ValueError(f"Student {student_id} already registered")
            
            student = Student(
                student_id=student_id,
                name=name,
                phone=phone,
                email=email
            )
            self.students[student_id] = student
            return student
    
    def register_helper(self, helper_id: str, name: str, phone: str, email: str) -> Helper:
        """Register a new volunteer helper"""
        with self.helper_lock:
            if helper_id in self.helpers:
                raise ValueError(f"Helper {helper_id} already registered")
            
            helper = Helper(
                helper_id=helper_id,
                name=name,
                phone=phone,
                email=email
            )
            self.helpers[helper_id] = helper
            return helper
    
    def update_helper_status(self, helper_id: str, status: HelperStatus) -> None:
        """Update helper availability status"""
        with self.helper_lock:
            if helper_id not in self.helpers:
                raise ValueError(f"Helper {helper_id} not found")
            self.helpers[helper_id].status = status
    
    def update_helper_location(self, helper_id: str, location: Location) -> None:
        """Update helper's current location"""
        with self.helper_lock:
            if helper_id not in self.helpers:
                raise ValueError(f"Helper {helper_id} not found")
            self.helpers[helper_id].current_location = location
    
    # ==================== Request Management ====================
    
    def create_request(
        self,
        student_id: str,
        pickup_location: Location,
        destination: Location,
        requested_time: datetime,
        notes: str = ""
    ) -> HelpRequest:
        """Create a new help request from a student"""
        with self.request_lock:
            if student_id not in self.students:
                raise ValueError(f"Student {student_id} not registered")
            
            request_id = str(uuid.uuid4())
            request = HelpRequest(
                request_id=request_id,
                student_id=student_id,
                pickup_location=pickup_location,
                destination=destination,
                requested_time=requested_time,
                notes=notes
            )
            
            self.requests[request_id] = request
            self.messages[request_id] = []  # Initialize message thread
            return request
    
    def get_available_requests(self) -> List[Dict]:
        """Get all pending requests visible to helpers"""
        with self.request_lock:
            available = []
            for request in self.requests.values():
                if request.status == RequestStatus.PENDING:
                    student = self.students[request.student_id]
                    available.append(
                        request.get_helper_view(student.get_public_info())
                    )
            return available
    
    def get_student_requests(self, student_id: str) -> List[HelpRequest]:
        """Get all requests for a specific student"""
        with self.request_lock:
            return [
                req for req in self.requests.values()
                if req.student_id == student_id
            ]
    
    def get_helper_requests(self, helper_id: str) -> List[Dict]:
        """Get all requests assigned to a specific helper"""
        with self.request_lock:
            assigned = []
            for request in self.requests.values():
                if request.assigned_helper_id == helper_id:
                    student = self.students[request.student_id]
                    assigned.append(
                        request.get_helper_view(student.get_public_info())
                    )
            return assigned
    
    # ==================== Assignment Logic ====================
    
    def assign_helper_to_request(self, request_id: str, helper_id: str) -> Tuple[bool, str]:
        """
        Assign a helper to a request with conflict prevention.
        Returns (success: bool, message: str)
        """
        with self.assignment_lock:
            # Validate request exists and is pending
            with self.request_lock:
                if request_id not in self.requests:
                    return False, "Request not found"
                
                request = self.requests[request_id]
                if request.status != RequestStatus.PENDING:
                    return False, f"Request is already {request.status.value}"
            
            # Validate helper exists and is available
            with self.helper_lock:
                if helper_id not in self.helpers:
                    return False, "Helper not found"
                
                helper = self.helpers[helper_id]
                if helper.status != HelperStatus.AVAILABLE:
                    return False, f"Helper is currently {helper.status.value}"
            
            # Check for pending assignment conflicts
            if request_id in self.pending_assignments:
                if self.pending_assignments[request_id] != helper_id:
                    return False, "Request is being assigned to another helper"
            
            # Mark as pending assignment
            self.pending_assignments[request_id] = helper_id
            
            try:
                # Perform the assignment
                with self.request_lock:
                    request.status = RequestStatus.ASSIGNED
                    request.assigned_helper_id = helper_id
                    request.accepted_at = datetime.now()
                
                with self.helper_lock:
                    helper.status = HelperStatus.BUSY
                
                return True, "Assignment successful"
            
            finally:
                # Clean up pending assignment
                if request_id in self.pending_assignments:
                    del self.pending_assignments[request_id]
    
    def start_assistance(self, request_id: str, helper_id: str) -> Tuple[bool, str]:
        """Mark assistance as in progress"""
        with self.request_lock:
            if request_id not in self.requests:
                return False, "Request not found"
            
            request = self.requests[request_id]
            
            if request.assigned_helper_id != helper_id:
                return False, "Helper not assigned to this request"
            
            if request.status != RequestStatus.ASSIGNED:
                return False, f"Request must be assigned (current: {request.status.value})"
            
            request.status = RequestStatus.IN_PROGRESS
            return True, "Assistance started"
    
    def complete_request(self, request_id: str, helper_id: str) -> Tuple[bool, str]:
        """Mark a request as completed"""
        with self.request_lock:
            if request_id not in self.requests:
                return False, "Request not found"
            
            request = self.requests[request_id]
            
            if request.assigned_helper_id != helper_id:
                return False, "Helper not assigned to this request"
            
            request.status = RequestStatus.COMPLETED
            request.completed_at = datetime.now()
            
            # Update helper stats and make available again
            with self.helper_lock:
                helper = self.helpers[helper_id]
                helper.status = HelperStatus.AVAILABLE
                helper.total_assists += 1
            
            return True, "Request completed"
    
    def cancel_request(self, request_id: str, student_id: str) -> Tuple[bool, str]:
        """Cancel a request (only by the requesting student)"""
        with self.request_lock:
            if request_id not in self.requests:
                return False, "Request not found"
            
            request = self.requests[request_id]
            
            if request.student_id != student_id:
                return False, "Only the requesting student can cancel"
            
            if request.status == RequestStatus.COMPLETED:
                return False, "Cannot cancel completed request"
            
            # If assigned, free up the helper
            if request.assigned_helper_id:
                with self.helper_lock:
                    if request.assigned_helper_id in self.helpers:
                        self.helpers[request.assigned_helper_id].status = HelperStatus.AVAILABLE
            
            request.status = RequestStatus.CANCELLED
            return True, "Request cancelled"
    
    # ==================== Secure Messaging ====================
    
    def send_message(
        self,
        request_id: str,
        sender_id: str,
        content: str
    ) -> Tuple[bool, str, Optional[Message]]:
        """
        Send a message between student and helper for a specific request.
        Returns (success: bool, message: str, Message object or None)
        """
        with self.message_lock:
            if request_id not in self.requests:
                return False, "Request not found", None
            
            request = self.requests[request_id]
            
            # Validate sender is part of this request
            is_student = sender_id == request.student_id
            is_helper = sender_id == request.assigned_helper_id
            
            if not (is_student or is_helper):
                return False, "Unauthorized: sender not part of this request", None
            
            # Determine recipient
            recipient_id = request.assigned_helper_id if is_student else request.student_id
            
            if not recipient_id:
                return False, "No helper assigned to this request yet", None
            
            # Create message
            message = Message(
                message_id=str(uuid.uuid4()),
                request_id=request_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                content=content
            )
            
            self.messages[request_id].append(message)
            return True, "Message sent", message
    
    def get_messages(self, request_id: str, user_id: str) -> List[Dict]:
        """
        Get all messages for a request that the user is authorized to see.
        Returns anonymized message history.
        """
        with self.message_lock:
            if request_id not in self.requests:
                return []
            
            request = self.requests[request_id]
            
            # Verify user is part of this request
            is_student = user_id == request.student_id
            is_helper = user_id == request.assigned_helper_id
            
            if not (is_student or is_helper):
                return []
            
            messages = []
            for msg in self.messages.get(request_id, []):
                # Determine sender label (anonymized)
                sender_label = "You" if msg.sender_id == user_id else "Other"
                
                messages.append({
                    "message_id": msg.message_id,
                    "sender": sender_label,
                    "content": msg.content,
                    "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "is_read": msg.is_read
                })
            
            return messages
    
    def mark_messages_read(self, request_id: str, user_id: str) -> None:
        """Mark all messages in a request as read by the user"""
        with self.message_lock:
            for msg in self.messages.get(request_id, []):
                if msg.recipient_id == user_id and not msg.is_read:
                    msg.is_read = True
    
    # ==================== Helper Matching (Scalability) ====================
    
    def find_best_helpers(
        self,
        request_id: str,
        max_helpers: int = 10
    ) -> List[Dict]:
        """
        Find best available helpers for a request based on proximity and rating.
        This would integrate with a geographic service in production.
        Returns list of helper info sorted by suitability.
        """
        with self.helper_lock, self.request_lock:
            if request_id not in self.requests:
                return []
            
            request = self.requests[request_id]
            available_helpers = [
                h for h in self.helpers.values()
                if h.status == HelperStatus.AVAILABLE
            ]
            
            # Sort by rating and total assists (simple scoring)
            # In production, would include proximity calculation
            scored_helpers = []
            for helper in available_helpers:
                score = (helper.rating * 0.7) + (min(helper.total_assists, 50) / 50 * 0.3)
                scored_helpers.append((score, helper))
            
            scored_helpers.sort(key=lambda x: x[0], reverse=True)
            
            return [
                helper.get_public_info()
                for _, helper in scored_helpers[:max_helpers]
            ]
    
    # ==================== Statistics & Monitoring ====================
    
    def get_system_stats(self) -> Dict:
        """Get overall system statistics for monitoring and scalability"""
        with self.request_lock, self.helper_lock, self.student_lock:
            return {
                "total_students": len(self.students),
                "total_helpers": len(self.helpers),
                "available_helpers": sum(
                    1 for h in self.helpers.values()
                    if h.status == HelperStatus.AVAILABLE
                ),
                "total_requests": len(self.requests),
                "pending_requests": sum(
                    1 for r in self.requests.values()
                    if r.status == RequestStatus.PENDING
                ),
                "active_requests": sum(
                    1 for r in self.requests.values()
                    if r.status in [RequestStatus.ASSIGNED, RequestStatus.IN_PROGRESS]
                ),
                "completed_requests": sum(
                    1 for r in self.requests.values()
                    if r.status == RequestStatus.COMPLETED
                ),
                "total_messages": sum(len(msgs) for msgs in self.messages.values())
            }
    
    def update_helper_rating(
        self,
        helper_id: str,
        new_rating: float
    ) -> Tuple[bool, str]:
        """Update helper rating after completed assistance"""
        with self.helper_lock:
            if helper_id not in self.helpers:
                return False, "Helper not found"
            
            helper = self.helpers[helper_id]
            
            # Simple moving average (in production, use weighted average)
            if helper.total_assists > 0:
                helper.rating = (
                    (helper.rating * (helper.total_assists - 1) + new_rating)
                    / helper.total_assists
                )
            else:
                helper.rating = new_rating
            
            return True, f"Rating updated to {helper.rating:.1f}"


# Example usage demonstration
if __name__ == "__main__":
    # Initialize system
    system = StudentHelperSystem()
    
    # Register users
    student = system.register_student(
        "S001",
        "Alice Johnson",
        "+1234567890",
        "alice@university.edu"
    )
    
    helper = system.register_helper(
        "H001",
        "Bob Smith",
        "+0987654321",
        "bob@university.edu"
    )
    
    # Create a request
    pickup = Location(57.7089, 11.9746, "University Library")
    destination = Location(57.6963, 11.9842, "Student Residence")
    
    request = system.create_request(
        student_id="S001",
        pickup_location=pickup,
        destination=destination,
        requested_time=datetime.now(),
        notes="Prefer someone familiar with the route"
    )
    
    print(f"Request created: {request.request_id}")
    print(f"Available requests: {len(system.get_available_requests())}")
    
    # Assign helper
    success, msg = system.assign_helper_to_request(request.request_id, "H001")
    print(f"Assignment: {msg}")
    
    # Send messages
    system.send_message(request.request_id, "S001", "I'm at the library entrance")
    system.send_message(request.request_id, "H001", "On my way, be there in 3 minutes")
    
    # View messages
    messages = system.get_messages(request.request_id, "S001")
    print(f"\nMessages for student ({len(messages)}):")
    for msg in messages:
        print(f"  [{msg['timestamp']}] {msg['sender']}: {msg['content']}")
    
    # Complete request
    system.start_assistance(request.request_id, "H001")
    system.complete_request(request.request_id, "H001")
    
    # View stats
    stats = system.get_system_stats()
    print(f"\nSystem Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
