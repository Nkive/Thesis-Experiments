"""
Student Assistance Request System

A robust, thread-safe Python system for managing assistance requests between 
students and volunteer helpers. All classes and functionality in one file.

Author: Assistant
Date: 2026-04-29
"""

from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum
import threading


# ============================================================================
# ENUMERATIONS
# ============================================================================

class RequestStatus(Enum):
    """Status of an assistance request"""
    PENDING = "pending"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ============================================================================
# EXCEPTIONS
# ============================================================================

class AssistanceSystemError(Exception):
    """Base exception for assistance system errors"""
    pass


class StudentNotFoundError(AssistanceSystemError):
    """Raised when student is not found"""
    pass


class HelperNotFoundError(AssistanceSystemError):
    """Raised when helper is not found"""
    pass


class RequestNotFoundError(AssistanceSystemError):
    """Raised when request is not found"""
    pass


class InvalidOperationError(AssistanceSystemError):
    """Raised when an invalid operation is attempted"""
    pass


# ============================================================================
# STUDENT CLASS
# ============================================================================

class Student:
    """Represents a student in the assistance system"""
    
    def __init__(self, student_id: str, name: str):
        """
        Initialize a student
        
        Args:
            student_id: Unique identifier for the student
            name: Student's name (kept private, not shared with helpers)
        """
        self._student_id = student_id
        self._name = name
        self._created_at = datetime.now()
    
    @property
    def student_id(self) -> str:
        """Get student ID"""
        return self._student_id
    
    @property
    def name(self) -> str:
        """Get student name (private information)"""
        return self._name
    
    def get_public_info(self) -> dict:
        """
        Get public information about student that can be shared with helpers
        Returns only the student ID, not the name
        """
        return {
            'student_id': self._student_id
        }
    
    def __repr__(self) -> str:
        return f"Student(id={self._student_id}, name={self._name})"


# ============================================================================
# HELPER CLASS
# ============================================================================

class Helper:
    """Represents a volunteer helper in the assistance system"""
    
    def __init__(self, helper_id: str, name: str):
        """
        Initialize a helper
        
        Args:
            helper_id: Unique identifier for the helper
            name: Helper's name
        """
        self._helper_id = helper_id
        self._name = name
        self._available = True
        self._created_at = datetime.now()
    
    @property
    def helper_id(self) -> str:
        """Get helper ID"""
        return self._helper_id
    
    @property
    def name(self) -> str:
        """Get helper name"""
        return self._name
    
    @property
    def available(self) -> bool:
        """Check if helper is available"""
        return self._available
    
    def set_available(self, available: bool) -> None:
        """Set helper availability"""
        self._available = available
    
    def get_info(self) -> dict:
        """Get helper information"""
        return {
            'helper_id': self._helper_id,
            'name': self._name,
            'available': self._available
        }
    
    def __repr__(self) -> str:
        return f"Helper(id={self._helper_id}, name={self._name}, available={self._available})"


# ============================================================================
# ASSISTANCE REQUEST CLASS
# ============================================================================

class AssistanceRequest:
    """Represents a request for assistance"""
    
    def __init__(
        self, 
        request_id: str,
        student_id: str,
        current_location: str,
        destination: str,
        requested_time: datetime
    ):
        """
        Initialize an assistance request
        
        Args:
            request_id: Unique identifier for the request
            student_id: ID of the student making the request
            current_location: Where the student currently is
            destination: Where the student wants to go
            requested_time: When the student needs help
        """
        self._request_id = request_id
        self._student_id = student_id
        self._current_location = current_location
        self._destination = destination
        self._requested_time = requested_time
        self._created_at = datetime.now()
        self._status = RequestStatus.PENDING
        self._assigned_helper_id: Optional[str] = None
        self._responded_helper_ids: set[str] = set()
    
    @property
    def request_id(self) -> str:
        """Get request ID"""
        return self._request_id
    
    @property
    def student_id(self) -> str:
        """Get student ID"""
        return self._student_id
    
    @property
    def current_location(self) -> str:
        """Get current location"""
        return self._current_location
    
    @property
    def destination(self) -> str:
        """Get destination"""
        return self._destination
    
    @property
    def requested_time(self) -> datetime:
        """Get requested time"""
        return self._requested_time
    
    @property
    def status(self) -> RequestStatus:
        """Get request status"""
        return self._status
    
    @property
    def assigned_helper_id(self) -> Optional[str]:
        """Get assigned helper ID if any"""
        return self._assigned_helper_id
    
    def add_response(self, helper_id: str) -> bool:
        """
        Record that a helper has responded to this request
        
        Args:
            helper_id: ID of the helper responding
            
        Returns:
            True if response was recorded, False if helper already responded
        """
        if helper_id in self._responded_helper_ids:
            return False
        
        self._responded_helper_ids.add(helper_id)
        return True
    
    def assign_helper(self, helper_id: str) -> bool:
        """
        Assign a helper to this request
        
        Args:
            helper_id: ID of the helper to assign
            
        Returns:
            True if assignment successful, False if already assigned
        """
        if self._status != RequestStatus.PENDING:
            return False
        
        if helper_id not in self._responded_helper_ids:
            return False
        
        self._assigned_helper_id = helper_id
        self._status = RequestStatus.ASSIGNED
        return True
    
    def get_responded_helpers(self) -> set[str]:
        """Get set of helper IDs who have responded"""
        return self._responded_helper_ids.copy()
    
    def complete(self) -> bool:
        """
        Mark request as completed
        
        Returns:
            True if marked complete, False if not in assigned state
        """
        if self._status != RequestStatus.ASSIGNED:
            return False
        
        self._status = RequestStatus.COMPLETED
        return True
    
    def cancel(self) -> bool:
        """
        Cancel the request
        
        Returns:
            True if cancelled, False if already completed
        """
        if self._status == RequestStatus.COMPLETED:
            return False
        
        self._status = RequestStatus.CANCELLED
        return True
    
    def get_public_info(self) -> dict:
        """
        Get public information about the request (for helpers to view)
        Does not include sensitive student information
        """
        return {
            'request_id': self._request_id,
            'student_id': self._student_id,  # Only ID, not name
            'current_location': self._current_location,
            'destination': self._destination,
            'requested_time': self._requested_time.isoformat(),
            'status': self._status.value,
            'response_count': len(self._responded_helper_ids)
        }
    
    def get_full_info(self) -> dict:
        """Get full information about the request (for internal use)"""
        return {
            **self.get_public_info(),
            'assigned_helper_id': self._assigned_helper_id,
            'created_at': self._created_at.isoformat(),
            'responded_helpers': list(self._responded_helper_ids)
        }
    
    def __repr__(self) -> str:
        return (f"AssistanceRequest(id={self._request_id}, student={self._student_id}, "
                f"from={self._current_location}, to={self._destination}, "
                f"status={self._status.value})")


# ============================================================================
# ASSISTANCE SYSTEM CLASS
# ============================================================================

class AssistanceSystem:
    """
    Main system managing assistance requests between students and helpers
    Thread-safe for handling multiple concurrent requests and responses
    """
    
    def __init__(self):
        """Initialize the assistance system"""
        self._students: Dict[str, Student] = {}
        self._helpers: Dict[str, Helper] = {}
        self._requests: Dict[str, AssistanceRequest] = {}
        
        # Thread locks for thread safety
        self._student_lock = threading.RLock()
        self._helper_lock = threading.RLock()
        self._request_lock = threading.RLock()
        
        # Counters for generating IDs
        self._request_counter = 0
        self._counter_lock = threading.Lock()
    
    def register_student(self, student_id: str, name: str) -> Student:
        """
        Register a new student in the system
        
        Args:
            student_id: Unique identifier for the student
            name: Student's name
            
        Returns:
            The registered Student object
            
        Raises:
            InvalidOperationError: If student ID already exists
        """
        with self._student_lock:
            if student_id in self._students:
                raise InvalidOperationError(f"Student with ID {student_id} already exists")
            
            student = Student(student_id, name)
            self._students[student_id] = student
            return student
    
    def register_helper(self, helper_id: str, name: str) -> Helper:
        """
        Register a new helper in the system
        
        Args:
            helper_id: Unique identifier for the helper
            name: Helper's name
            
        Returns:
            The registered Helper object
            
        Raises:
            InvalidOperationError: If helper ID already exists
        """
        with self._helper_lock:
            if helper_id in self._helpers:
                raise InvalidOperationError(f"Helper with ID {helper_id} already exists")
            
            helper = Helper(helper_id, name)
            self._helpers[helper_id] = helper
            return helper
    
    def _generate_request_id(self) -> str:
        """Generate a unique request ID"""
        with self._counter_lock:
            self._request_counter += 1
            return f"REQ{self._request_counter:06d}"
    
    def create_request(
        self,
        student_id: str,
        current_location: str,
        destination: str,
        requested_time: datetime
    ) -> AssistanceRequest:
        """
        Create a new assistance request
        
        Args:
            student_id: ID of the student making the request
            current_location: Where the student currently is
            destination: Where the student wants to go
            requested_time: When the student needs help
            
        Returns:
            The created AssistanceRequest object
            
        Raises:
            StudentNotFoundError: If student ID doesn't exist
            InvalidOperationError: If request data is invalid
        """
        with self._student_lock:
            if student_id not in self._students:
                raise StudentNotFoundError(f"Student with ID {student_id} not found")
        
        if not current_location or not destination:
            raise InvalidOperationError("Current location and destination must be specified")
        
        if requested_time < datetime.now():
            raise InvalidOperationError("Requested time cannot be in the past")
        
        request_id = self._generate_request_id()
        
        with self._request_lock:
            request = AssistanceRequest(
                request_id=request_id,
                student_id=student_id,
                current_location=current_location,
                destination=destination,
                requested_time=requested_time
            )
            self._requests[request_id] = request
            return request
    
    def get_available_helpers(self) -> List[Helper]:
        """
        Get list of all available helpers
        
        Returns:
            List of available Helper objects
        """
        with self._helper_lock:
            return [h for h in self._helpers.values() if h.available]
    
    def get_pending_requests(self) -> List[AssistanceRequest]:
        """
        Get all pending assistance requests
        
        Returns:
            List of pending AssistanceRequest objects
        """
        with self._request_lock:
            return [r for r in self._requests.values() if r.status == RequestStatus.PENDING]
    
    def get_pending_requests_for_helper(self) -> List[dict]:
        """
        Get pending requests formatted for helper viewing
        Returns only public information about students
        
        Returns:
            List of dictionaries with public request information
        """
        pending = self.get_pending_requests()
        return [req.get_public_info() for req in pending]
    
    def helper_respond_to_request(self, helper_id: str, request_id: str) -> bool:
        """
        Record a helper's response to an assistance request
        
        Args:
            helper_id: ID of the helper responding
            request_id: ID of the request being responded to
            
        Returns:
            True if response recorded successfully
            
        Raises:
            HelperNotFoundError: If helper doesn't exist
            RequestNotFoundError: If request doesn't exist
            InvalidOperationError: If helper already responded
        """
        with self._helper_lock:
            if helper_id not in self._helpers:
                raise HelperNotFoundError(f"Helper with ID {helper_id} not found")
        
        with self._request_lock:
            if request_id not in self._requests:
                raise RequestNotFoundError(f"Request with ID {request_id} not found")
            
            request = self._requests[request_id]
            
            if request.status != RequestStatus.PENDING:
                raise InvalidOperationError(f"Request {request_id} is no longer pending")
            
            success = request.add_response(helper_id)
            if not success:
                raise InvalidOperationError(f"Helper {helper_id} has already responded to this request")
            
            return True
    
    def assign_helper_to_request(self, request_id: str, helper_id: str) -> bool:
        """
        Assign a specific helper to a request
        Only one helper can be assigned per request
        
        Args:
            request_id: ID of the request
            helper_id: ID of the helper to assign
            
        Returns:
            True if assignment successful
            
        Raises:
            RequestNotFoundError: If request doesn't exist
            HelperNotFoundError: If helper doesn't exist
            InvalidOperationError: If assignment is not valid
        """
        with self._request_lock:
            if request_id not in self._requests:
                raise RequestNotFoundError(f"Request with ID {request_id} not found")
            
            request = self._requests[request_id]
            
            with self._helper_lock:
                if helper_id not in self._helpers:
                    raise HelperNotFoundError(f"Helper with ID {helper_id} not found")
                
                helper = self._helpers[helper_id]
                
                # Check if helper has responded to this request
                if helper_id not in request.get_responded_helpers():
                    raise InvalidOperationError(f"Helper {helper_id} has not responded to this request")
                
                # Assign helper to request
                success = request.assign_helper(helper_id)
                if not success:
                    raise InvalidOperationError(f"Cannot assign helper to request {request_id}")
                
                return True
    
    def get_request_responses(self, request_id: str) -> List[dict]:
        """
        Get all helper responses for a specific request
        
        Args:
            request_id: ID of the request
            
        Returns:
            List of helper information dictionaries
            
        Raises:
            RequestNotFoundError: If request doesn't exist
        """
        with self._request_lock:
            if request_id not in self._requests:
                raise RequestNotFoundError(f"Request with ID {request_id} not found")
            
            request = self._requests[request_id]
            responded_helper_ids = request.get_responded_helpers()
            
            with self._helper_lock:
                helpers_info = []
                for helper_id in responded_helper_ids:
                    if helper_id in self._helpers:
                        helpers_info.append(self._helpers[helper_id].get_info())
                
                return helpers_info
    
    def complete_request(self, request_id: str) -> bool:
        """
        Mark a request as completed
        
        Args:
            request_id: ID of the request to complete
            
        Returns:
            True if completed successfully
            
        Raises:
            RequestNotFoundError: If request doesn't exist
            InvalidOperationError: If request cannot be completed
        """
        with self._request_lock:
            if request_id not in self._requests:
                raise RequestNotFoundError(f"Request with ID {request_id} not found")
            
            request = self._requests[request_id]
            success = request.complete()
            
            if not success:
                raise InvalidOperationError(f"Request {request_id} cannot be completed")
            
            return True
    
    def cancel_request(self, request_id: str) -> bool:
        """
        Cancel a request
        
        Args:
            request_id: ID of the request to cancel
            
        Returns:
            True if cancelled successfully
            
        Raises:
            RequestNotFoundError: If request doesn't exist
            InvalidOperationError: If request cannot be cancelled
        """
        with self._request_lock:
            if request_id not in self._requests:
                raise RequestNotFoundError(f"Request with ID {request_id} not found")
            
            request = self._requests[request_id]
            success = request.cancel()
            
            if not success:
                raise InvalidOperationError(f"Request {request_id} cannot be cancelled")
            
            return True
    
    def get_request(self, request_id: str) -> AssistanceRequest:
        """
        Get a specific request by ID
        
        Args:
            request_id: ID of the request
            
        Returns:
            The AssistanceRequest object
            
        Raises:
            RequestNotFoundError: If request doesn't exist
        """
        with self._request_lock:
            if request_id not in self._requests:
                raise RequestNotFoundError(f"Request with ID {request_id} not found")
            
            return self._requests[request_id]
    
    def get_student(self, student_id: str) -> Student:
        """
        Get a specific student by ID
        
        Args:
            student_id: ID of the student
            
        Returns:
            The Student object
            
        Raises:
            StudentNotFoundError: If student doesn't exist
        """
        with self._student_lock:
            if student_id not in self._students:
                raise StudentNotFoundError(f"Student with ID {student_id} not found")
            
            return self._students[student_id]
    
    def get_helper(self, helper_id: str) -> Helper:
        """
        Get a specific helper by ID
        
        Args:
            helper_id: ID of the helper
            
        Returns:
            The Helper object
            
        Raises:
            HelperNotFoundError: If helper doesn't exist
        """
        with self._helper_lock:
            if helper_id not in self._helpers:
                raise HelperNotFoundError(f"Helper with ID {helper_id} not found")
            
            return self._helpers[helper_id]
    
    def get_system_status(self) -> dict:
        """
        Get overall system status
        
        Returns:
            Dictionary with system statistics
        """
        with self._student_lock, self._helper_lock, self._request_lock:
            available_helpers = len([h for h in self._helpers.values() if h.available])
            pending_requests = len([r for r in self._requests.values() if r.status == RequestStatus.PENDING])
            
            return {
                'total_students': len(self._students),
                'total_helpers': len(self._helpers),
                'available_helpers': available_helpers,
                'total_requests': len(self._requests),
                'pending_requests': pending_requests,
                'has_available_helpers': available_helpers > 0
            }


# ============================================================================
# EXAMPLE USAGE (DEMONSTRATION)
# ============================================================================

def demo_assistance_system():
    """Demonstrate the assistance system functionality"""
    from datetime import timedelta
    
    # Initialize the system
    system = AssistanceSystem()
    
    print("=== Student Assistance System Demo ===\n")
    
    # Register students
    print("1. Registering students...")
    student1 = system.register_student("S001", "Alice Johnson")
    student2 = system.register_student("S002", "Bob Smith")
    student3 = system.register_student("S003", "Carol Davis")
    print(f"   Registered: {student1}, {student2}, {student3}\n")
    
    # Register helpers
    print("2. Registering helpers...")
    helper1 = system.register_helper("H001", "David Brown")
    helper2 = system.register_helper("H002", "Emma Wilson")
    helper3 = system.register_helper("H003", "Frank Miller")
    print(f"   Registered: {helper1}, {helper2}, {helper3}\n")
    
    # Check system status
    print("3. System status:")
    status = system.get_system_status()
    print(f"   Total students: {status['total_students']}")
    print(f"   Total helpers: {status['total_helpers']}")
    print(f"   Available helpers: {status['available_helpers']}")
    print(f"   Has available helpers: {status['has_available_helpers']}\n")
    
    # Create assistance requests
    print("4. Students creating assistance requests...")
    request1 = system.create_request(
        student_id="S001",
        current_location="Library Building A",
        destination="Science Building B",
        requested_time=datetime.now() + timedelta(minutes=15)
    )
    print(f"   Created: {request1}")
    
    request2 = system.create_request(
        student_id="S002",
        current_location="Main Hall",
        destination="Gymnasium",
        requested_time=datetime.now() + timedelta(minutes=30)
    )
    print(f"   Created: {request2}")
    
    request3 = system.create_request(
        student_id="S003",
        current_location="Dormitory C",
        destination="Cafeteria",
        requested_time=datetime.now() + timedelta(hours=1)
    )
    print(f"   Created: {request3}\n")
    
    # Helpers view pending requests (only public info shown)
    print("5. Helpers viewing pending requests (privacy-protected)...")
    pending_requests = system.get_pending_requests_for_helper()
    for req in pending_requests:
        print(f"   Request ID: {req['request_id']}")
        print(f"   Student ID: {req['student_id']}")  # Only ID, not name
        print(f"   From: {req['current_location']} → To: {req['destination']}")
        print(f"   Time needed: {req['requested_time']}")
        print(f"   Status: {req['status']}\n")
    
    # Scenario 1: Multiple helpers respond to request 1
    print("6. Multiple helpers responding to Request 1...")
    system.helper_respond_to_request("H001", request1.request_id)
    print(f"   Helper H001 (David) responded")
    
    system.helper_respond_to_request("H002", request1.request_id)
    print(f"   Helper H002 (Emma) responded")
    
    system.helper_respond_to_request("H003", request1.request_id)
    print(f"   Helper H003 (Frank) responded\n")
    
    # Get responses for request 1
    print("7. Viewing responses for Request 1...")
    responses = system.get_request_responses(request1.request_id)
    print(f"   Total responses: {len(responses)}")
    for helper_info in responses:
        print(f"   - {helper_info['name']} (ID: {helper_info['helper_id']})")
    print()
    
    # Assign only one helper to request 1
    print("8. Assigning ONE helper to Request 1...")
    system.assign_helper_to_request(request1.request_id, "H002")
    print(f"   Helper H002 (Emma) assigned to Request 1")
    print(f"   Request status: {system.get_request(request1.request_id).status.value}\n")
    
    # Try to assign another helper (should fail)
    print("9. Attempting to assign another helper to Request 1...")
    try:
        system.assign_helper_to_request(request1.request_id, "H001")
        print("   ERROR: Should not have succeeded!")
    except InvalidOperationError as e:
        print(f"   Correctly prevented: {e}\n")
    
    # Scenario 2: No helpers respond to request 2
    print("10. Checking Request 2 (no helper responses yet)...")
    responses = system.get_request_responses(request2.request_id)
    if len(responses) == 0:
        print("   No helpers have responded to this request yet")
        print("   System should display this to the student\n")
    
    # Scenario 3: One helper responds to request 3
    print("11. One helper responding to Request 3...")
    system.helper_respond_to_request("H001", request3.request_id)
    print(f"   Helper H001 (David) responded")
    
    responses = system.get_request_responses(request3.request_id)
    print(f"   Total responses: {len(responses)}")
    
    # Assign the only helper
    system.assign_helper_to_request(request3.request_id, "H001")
    print(f"   Helper H001 assigned to Request 3\n")
    
    # Complete a request
    print("12. Completing Request 1...")
    system.complete_request(request1.request_id)
    print(f"   Request 1 completed")
    print(f"   Status: {system.get_request(request1.request_id).status.value}\n")
    
    # Cancel a request
    print("13. Student cancelling Request 2...")
    system.cancel_request(request2.request_id)
    print(f"   Request 2 cancelled")
    print(f"   Status: {system.get_request(request2.request_id).status.value}\n")
    
    # Final system status
    print("14. Final system status:")
    status = system.get_system_status()
    print(f"   Total requests: {status['total_requests']}")
    print(f"   Pending requests: {status['pending_requests']}")
    print(f"   Available helpers: {status['available_helpers']}\n")
    
    # Demonstrate error handling
    print("15. Error handling demonstrations...")
    
    # Try to create request for non-existent student
    try:
        system.create_request(
            student_id="S999",
            current_location="Nowhere",
            destination="Somewhere",
            requested_time=datetime.now() + timedelta(hours=1)
        )
    except StudentNotFoundError as e:
        print(f"   ✓ Caught expected error: {e}")
    
    # Try to respond with non-existent helper
    try:
        system.helper_respond_to_request("H999", request3.request_id)
    except HelperNotFoundError as e:
        print(f"   ✓ Caught expected error: {e}")
    
    # Try to access non-existent request
    try:
        system.get_request("REQ999999")
    except RequestNotFoundError as e:
        print(f"   ✓ Caught expected error: {e}")
    
    print("\n=== Demo Complete ===")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    demo_assistance_system()