"""
Student Walk Helper App - Backend System
A thread-safe system for matching students with walking helpers between buildings.
Handles concurrent requests and ensures no double-booking of helpers.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime
from enum import Enum


class RequestStatus(Enum):
    """Status of a walk request"""
    PENDING = "pending"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Student:
    """Student requiring assistance"""
    id: str
    name: str
    from_building: str
    to_building: str
    # Additional sensitive info not shared with helpers
    phone: str
    special_needs: Optional[str] = None
    
    def get_public_info(self) -> Dict:
        """Return only necessary information for helpers"""
        return {
            "request_id": self.id,
            "from_building": self.from_building,
            "to_building": self.to_building,
            "first_name": self.name.split()[0]  # Only first name for privacy
        }


@dataclass
class Helper:
    """Volunteer helper"""
    id: str
    name: str
    current_building: str
    is_available: bool = True
    current_assignment: Optional[str] = None  # Request ID if assigned


@dataclass
class WalkRequest:
    """A request for walking assistance"""
    id: str
    student: Student
    status: RequestStatus = RequestStatus.PENDING
    assigned_helper: Optional[Helper] = None
    created_at: datetime = field(default_factory=datetime.now)
    assigned_at: Optional[datetime] = None
    interested_helpers: List[str] = field(default_factory=list)  # Helper IDs who responded


class StudentWalkHelperSystem:
    """
    Thread-safe system for managing student walk requests and helper assignments.
    Handles concurrent requests without double-booking helpers.
    """
    
    def __init__(self):
        # Core data structures
        self.students: Dict[str, Student] = {}
        self.helpers: Dict[str, Helper] = {}
        self.requests: Dict[str, WalkRequest] = {}
        
        # Thread safety locks
        self._request_lock = threading.Lock()
        self._helper_lock = threading.Lock()
        self._assignment_lock = threading.Lock()
        
        # Performance optimization: index for quick lookups
        self._pending_requests: List[str] = []  # Request IDs
        
    def register_student(self, name: str, phone: str, special_needs: Optional[str] = None) -> str:
        """Register a new student in the system"""
        student_id = str(uuid.uuid4())
        student = Student(
            id=student_id,
            name=name,
            phone=phone,
            special_needs=special_needs,
            from_building="",  # Set when making request
            to_building=""
        )
        self.students[student_id] = student
        return student_id
    
    def register_helper(self, name: str, current_building: str) -> str:
        """Register a new helper in the system"""
        helper_id = str(uuid.uuid4())
        helper = Helper(
            id=helper_id,
            name=name,
            current_building=current_building
        )
        with self._helper_lock:
            self.helpers[helper_id] = helper
        return helper_id
    
    def create_walk_request(self, student_id: str, from_building: str, to_building: str) -> Optional[str]:
        """
        Create a new walk request. Thread-safe to handle multiple simultaneous requests.
        Returns request ID if successful, None if student not found.
        """
        if student_id not in self.students:
            return None
        
        request_id = str(uuid.uuid4())
        student = self.students[student_id]
        student.from_building = from_building
        student.to_building = to_building
        
        request = WalkRequest(
            id=request_id,
            student=student
        )
        
        with self._request_lock:
            self.requests[request_id] = request
            self._pending_requests.append(request_id)
        
        return request_id
    
    def get_available_requests(self, helper_id: str) -> List[Dict]:
        """
        Get all pending requests that a helper can respond to.
        Returns only public information about students.
        Thread-safe for concurrent helper access.
        """
        if helper_id not in self.helpers:
            return []
        
        available = []
        with self._request_lock:
            for request_id in self._pending_requests:
                request = self.requests.get(request_id)
                if request and request.status == RequestStatus.PENDING:
                    # Return only public info
                    request_info = request.student.get_public_info()
                    request_info['created_at'] = request.created_at.isoformat()
                    available.append(request_info)
        
        return available
    
    def helper_respond_to_request(self, helper_id: str, request_id: str) -> Dict:
        """
        Helper responds to a request. First responder gets assigned.
        Thread-safe to prevent double-booking.
        
        Returns:
            Dict with 'success' boolean and 'message' string
        """
        # Validate inputs
        if helper_id not in self.helpers:
            return {"success": False, "message": "Helper not found"}
        
        if request_id not in self.requests:
            return {"success": False, "message": "Request not found"}
        
        # Use fine-grained locking for high concurrency
        with self._assignment_lock:
            helper = self.helpers[helper_id]
            request = self.requests[request_id]
            
            # Check if helper is available
            if not helper.is_available or helper.current_assignment:
                return {"success": False, "message": "Helper is already assigned to another student"}
            
            # Check if request is still pending
            if request.status != RequestStatus.PENDING:
                return {"success": False, "message": "Request already assigned to another helper"}
            
            # First responder wins - assign the helper
            request.status = RequestStatus.ASSIGNED
            request.assigned_helper = helper
            request.assigned_at = datetime.now()
            request.interested_helpers.append(helper_id)
            
            helper.is_available = False
            helper.current_assignment = request_id
            
            # Remove from pending list
            with self._request_lock:
                if request_id in self._pending_requests:
                    self._pending_requests.remove(request_id)
            
            return {
                "success": True,
                "message": f"Successfully assigned to help {request.student.name.split()[0]}",
                "student_contact": request.student.phone,  # Only shown after assignment
                "from_building": request.student.from_building,
                "to_building": request.student.to_building
            }
    
    def complete_request(self, request_id: str) -> bool:
        """
        Mark a request as completed and free up the helper.
        Thread-safe.
        """
        if request_id not in self.requests:
            return False
        
        with self._assignment_lock:
            request = self.requests[request_id]
            
            if request.status != RequestStatus.ASSIGNED:
                return False
            
            request.status = RequestStatus.COMPLETED
            
            # Free up the helper
            if request.assigned_helper:
                helper = request.assigned_helper
                helper.is_available = True
                helper.current_assignment = None
            
            return True
    
    def cancel_request(self, request_id: str) -> bool:
        """
        Cancel a pending or assigned request.
        Thread-safe.
        """
        if request_id not in self.requests:
            return False
        
        with self._assignment_lock:
            request = self.requests[request_id]
            
            # Free up helper if assigned
            if request.assigned_helper:
                helper = request.assigned_helper
                helper.is_available = True
                helper.current_assignment = None
            
            request.status = RequestStatus.CANCELLED
            
            # Remove from pending list if still there
            with self._request_lock:
                if request_id in self._pending_requests:
                    self._pending_requests.remove(request_id)
            
            return True
    
    def get_helper_status(self, helper_id: str) -> Optional[Dict]:
        """Get current status of a helper"""
        if helper_id not in self.helpers:
            return None
        
        with self._helper_lock:
            helper = self.helpers[helper_id]
            return {
                "id": helper.id,
                "name": helper.name,
                "available": helper.is_available,
                "current_assignment": helper.current_assignment
            }
    
    def get_request_status(self, request_id: str) -> Optional[Dict]:
        """Get current status of a request"""
        if request_id not in self.requests:
            return None
        
        with self._request_lock:
            request = self.requests[request_id]
            return {
                "id": request.id,
                "status": request.status.value,
                "created_at": request.created_at.isoformat(),
                "assigned_helper": request.assigned_helper.name if request.assigned_helper else None,
                "assigned_at": request.assigned_at.isoformat() if request.assigned_at else None
            }
    
    def get_system_stats(self) -> Dict:
        """Get overall system statistics for monitoring performance"""
        with self._request_lock, self._helper_lock:
            total_helpers = len(self.helpers)
            available_helpers = sum(1 for h in self.helpers.values() if h.is_available)
            pending_requests = len(self._pending_requests)
            total_requests = len(self.requests)
            
            return {
                "total_helpers": total_helpers,
                "available_helpers": available_helpers,
                "busy_helpers": total_helpers - available_helpers,
                "pending_requests": pending_requests,
                "total_requests": total_requests
            }


# Example usage and testing simulation
if __name__ == "__main__":
    print("=== Student Walk Helper System Demo ===\n")
    
    # Initialize system
    system = StudentWalkHelperSystem()
    
    # Register some students
    student1_id = system.register_student("Alice Johnson", "555-0101", "Mobility assistance needed")
    student2_id = system.register_student("Bob Smith", "555-0102")
    student3_id = system.register_student("Carol Davis", "555-0103")
    
    # Register some helpers
    helper1_id = system.register_helper("David Helper", "Engineering Building")
    helper2_id = system.register_helper("Emma Volunteer", "Library")
    helper3_id = system.register_helper("Frank Guide", "Science Building")
    
    print(f"Registered 3 students and 3 helpers\n")
    
    # Simulate concurrent requests
    print("=== Simulating Concurrent Request Scenario ===\n")
    
    # Create multiple requests
    req1 = system.create_walk_request(student1_id, "Library", "Engineering Building")
    req2 = system.create_walk_request(student2_id, "Science Building", "Student Center")
    req3 = system.create_walk_request(student3_id, "Dormitory A", "Library")
    
    print(f"Created 3 walk requests\n")
    
    # Multiple helpers try to respond to the same request (simulating race condition)
    print("=== Testing Race Condition: Multiple helpers respond to same request ===")
    
    def helper_response_thread(helper_id, request_id, helper_name):
        """Simulate helper responding in a separate thread"""
        time.sleep(0.001)  # Small delay to simulate network latency
        result = system.helper_respond_to_request(helper_id, request_id)
        print(f"{helper_name}: {result['message']}")
    
    # Create threads for concurrent responses to req1
    threads = []
    threads.append(threading.Thread(target=helper_response_thread, args=(helper1_id, req1, "David")))
    threads.append(threading.Thread(target=helper_response_thread, args=(helper2_id, req1, "Emma")))
    threads.append(threading.Thread(target=helper_response_thread, args=(helper3_id, req1, "Frank")))
    
    # Start all threads simultaneously
    for t in threads:
        t.start()
    
    # Wait for all to complete
    for t in threads:
        t.join()
    
    print("\n=== Checking System State ===")
    stats = system.get_system_stats()
    print(f"Available helpers: {stats['available_helpers']}/{stats['total_helpers']}")
    print(f"Pending requests: {stats['pending_requests']}")
    
    # Check who got assigned
    req1_status = system.get_request_status(req1)
    print(f"\nRequest 1 assigned to: {req1_status['assigned_helper']}")
    
    # Other helpers respond to remaining requests
    print("\n=== Assigning Remaining Requests ===")
    system.helper_respond_to_request(helper2_id, req2)
    print(f"Request 2 status: {system.get_request_status(req2)['status']}")
    
    # Complete a request
    print("\n=== Completing Request ===")
    system.complete_request(req1)
    print(f"Request 1 completed. Helper {helper1_id} is now available again.")
    
    # Final stats
    print("\n=== Final System Stats ===")
    final_stats = system.get_system_stats()
    print(f"Total helpers: {final_stats['total_helpers']}")
    print(f"Available helpers: {final_stats['available_helpers']}")
    print(f"Pending requests: {final_stats['pending_requests']}")
    print(f"Total requests processed: {final_stats['total_requests']}")
    
    print("\n=== Demo Complete ===")