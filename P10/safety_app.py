from datetime import datetime, timedelta
from typing import Optional, List, Dict
from enum import Enum
import uuid


class RequestStatus(Enum):
    PENDING = "pending"
    OFFERED = "offered"
    MATCHED = "matched"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class HelperStatus(Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"


class User:
    def __init__(self, user_id: str, name: str, phone: str):
        self.user_id = user_id
        self.name = name
        self.phone = phone
        self.created_at = datetime.now()


class Student(User):
    def __init__(self, user_id: str, name: str, phone: str):
        super().__init__(user_id, name, phone)
        self.active_requests: List[str] = []
    
    def create_request(self, start_location: str, end_location: str, notes: str = ""):
        return WalkRequest(
            student_id=self.user_id,
            student_name=self.name,
            start_location=start_location,
            end_location=end_location,
            notes=notes
        )


class Helper(User):
    def __init__(self, user_id: str, name: str, phone: str):
        super().__init__(user_id, name, phone)
        self.status = HelperStatus.AVAILABLE
        self.current_assignment: Optional[str] = None
        self.completed_walks = 0
    
    def get_public_profile(self) -> Dict:
        return {
            "helper_id": self.user_id,
            "name": self.name,
            "completed_walks": self.completed_walks,
            "status": self.status.value
        }


class WalkRequest:
    def __init__(self, student_id: str, student_name: str, start_location: str, 
                 end_location: str, notes: str = ""):
        self.request_id = str(uuid.uuid4())
        self.student_id = student_id
        self.student_name = student_name
        self.start_location = start_location
        self.end_location = end_location
        self.notes = notes
        self.status = RequestStatus.PENDING
        self.created_at = datetime.now()
        self.matched_helper_id: Optional[str] = None
        self.offered_helpers: List[str] = []
        self.offer_expiry: Optional[datetime] = None
    
    def get_public_info(self) -> Dict:
        return {
            "request_id": self.request_id,
            "start_location": self.start_location,
            "end_location": self.end_location,
            "requested_time": self.created_at.strftime("%H:%M"),
            "notes": self.notes if self.notes else "No additional notes"
        }
    
    def get_full_info(self, for_helper_id: str) -> Dict:
        info = self.get_public_info()
        if self.matched_helper_id == for_helper_id:
            info["student_name"] = self.student_name
            info["student_id"] = self.student_id
        return info
    
    def is_expired(self) -> bool:
        if self.status == RequestStatus.OFFERED and self.offer_expiry:
            return datetime.now() > self.offer_expiry
        return False


class CommunicationChannel:
    def __init__(self, request_id: str, student_id: str, helper_id: str):
        self.channel_id = str(uuid.uuid4())
        self.request_id = request_id
        self.student_id = student_id
        self.helper_id = helper_id
        self.messages: List[Dict] = []
        self.created_at = datetime.now()
    
    def send_message(self, sender_id: str, message: str) -> bool:
        if sender_id not in [self.student_id, self.helper_id]:
            return False
        
        self.messages.append({
            "sender_id": sender_id,
            "message": message,
            "timestamp": datetime.now()
        })
        return True
    
    def get_messages(self, user_id: str) -> List[Dict]:
        if user_id not in [self.student_id, self.helper_id]:
            return []
        return self.messages


class CampusSafetySystem:
    def __init__(self, offer_timeout_seconds: int = 30):
        self.students: Dict[str, Student] = {}
        self.helpers: Dict[str, Helper] = {}
        self.requests: Dict[str, WalkRequest] = {}
        self.request_queue: List[str] = []
        self.communication_channels: Dict[str, CommunicationChannel] = {}
        self.offer_timeout = timedelta(seconds=offer_timeout_seconds)
    
    def register_student(self, name: str, phone: str) -> Student:
        student_id = f"STU-{uuid.uuid4().hex[:8]}"
        student = Student(student_id, name, phone)
        self.students[student_id] = student
        return student
    
    def register_helper(self, name: str, phone: str) -> Helper:
        helper_id = f"HLP-{uuid.uuid4().hex[:8]}"
        helper = Helper(helper_id, name, phone)
        self.helpers[helper_id] = helper
        return helper
    
    def submit_request(self, student_id: str, start_location: str, 
                      end_location: str, notes: str = "") -> Optional[WalkRequest]:
        if student_id not in self.students:
            raise ValueError(f"Student {student_id} not found")
        
        student = self.students[student_id]
        
        for req_id in student.active_requests:
            if req_id in self.requests:
                req = self.requests[req_id]
                if req.status in [RequestStatus.PENDING, RequestStatus.OFFERED, RequestStatus.MATCHED]:
                    raise ValueError("Student already has an active request")
        
        request = student.create_request(start_location, end_location, notes)
        self.requests[request.request_id] = request
        self.request_queue.append(request.request_id)
        student.active_requests.append(request.request_id)
        
        return request
    
    def get_available_helpers(self) -> List[Helper]:
        return [h for h in self.helpers.values() if h.status == HelperStatus.AVAILABLE]
    
    def view_requests(self, helper_id: str) -> List[Dict]:
        if helper_id not in self.helpers:
            raise ValueError(f"Helper {helper_id} not found")
        
        self._process_expired_offers()
        
        available_requests = []
        for req_id in self.request_queue:
            if req_id in self.requests:
                req = self.requests[req_id]
                if req.status == RequestStatus.PENDING:
                    available_requests.append(req.get_public_info())
        
        return available_requests
    
    def offer_help(self, helper_id: str, request_id: str) -> bool:
        if helper_id not in self.helpers:
            raise ValueError(f"Helper {helper_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        helper = self.helpers[helper_id]
        request = self.requests[request_id]
        
        if helper.status != HelperStatus.AVAILABLE:
            raise ValueError("Helper is not available")
        
        if request.status != RequestStatus.PENDING:
            return False
        
        if helper_id in request.offered_helpers:
            return False
        
        request.offered_helpers.append(helper_id)
        request.status = RequestStatus.OFFERED
        request.offer_expiry = datetime.now() + self.offer_timeout
        
        return True
    
    def accept_helper(self, student_id: str, request_id: str, helper_id: str) -> bool:
        if student_id not in self.students:
            raise ValueError(f"Student {student_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        if helper_id not in self.helpers:
            raise ValueError(f"Helper {helper_id} not found")
        
        request = self.requests[request_id]
        helper = self.helpers[helper_id]
        
        if request.student_id != student_id:
            raise ValueError("Student does not own this request")
        
        if helper_id not in request.offered_helpers:
            raise ValueError("Helper has not offered to help with this request")
        
        if request.status not in [RequestStatus.PENDING, RequestStatus.OFFERED]:
            return False
        
        if helper.status != HelperStatus.AVAILABLE:
            raise ValueError("Helper is no longer available")
        
        request.status = RequestStatus.MATCHED
        request.matched_helper_id = helper_id
        helper.status = HelperStatus.BUSY
        helper.current_assignment = request_id
        
        if request_id in self.request_queue:
            self.request_queue.remove(request_id)
        
        channel = CommunicationChannel(request_id, student_id, helper_id)
        self.communication_channels[channel.channel_id] = channel
        
        return True
    
    def get_offered_helpers(self, student_id: str, request_id: str) -> List[Dict]:
        if student_id not in self.students:
            raise ValueError(f"Student {student_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        request = self.requests[request_id]
        
        if request.student_id != student_id:
            raise ValueError("Student does not own this request")
        
        helpers_info = []
        for helper_id in request.offered_helpers:
            if helper_id in self.helpers:
                helper = self.helpers[helper_id]
                helpers_info.append(helper.get_public_profile())
        
        return helpers_info
    
    def complete_walk(self, helper_id: str, request_id: str) -> bool:
        if helper_id not in self.helpers:
            raise ValueError(f"Helper {helper_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        helper = self.helpers[helper_id]
        request = self.requests[request_id]
        
        if request.matched_helper_id != helper_id:
            raise ValueError("Helper is not assigned to this request")
        
        if request.status != RequestStatus.MATCHED:
            return False
        
        request.status = RequestStatus.COMPLETED
        helper.status = HelperStatus.AVAILABLE
        helper.current_assignment = None
        helper.completed_walks += 1
        
        return True
    
    def cancel_request(self, student_id: str, request_id: str) -> bool:
        if student_id not in self.students:
            raise ValueError(f"Student {student_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        request = self.requests[request_id]
        student = self.students[student_id]
        
        if request.student_id != student_id:
            raise ValueError("Student does not own this request")
        
        if request.status == RequestStatus.COMPLETED:
            return False
        
        if request.status == RequestStatus.MATCHED and request.matched_helper_id:
            helper = self.helpers[request.matched_helper_id]
            helper.status = HelperStatus.AVAILABLE
            helper.current_assignment = None
        
        request.status = RequestStatus.CANCELLED
        
        if request_id in self.request_queue:
            self.request_queue.remove(request_id)
        
        if request_id in student.active_requests:
            student.active_requests.remove(request_id)
        
        return True
    
    def send_message(self, sender_id: str, request_id: str, message: str) -> bool:
        if sender_id not in self.students and sender_id not in self.helpers:
            raise ValueError(f"User {sender_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        request = self.requests[request_id]
        
        if request.status != RequestStatus.MATCHED:
            raise ValueError("Request is not in matched state")
        
        channel = None
        for ch in self.communication_channels.values():
            if ch.request_id == request_id:
                channel = ch
                break
        
        if not channel:
            raise ValueError("Communication channel not found")
        
        return channel.send_message(sender_id, message)
    
    def get_messages(self, user_id: str, request_id: str) -> List[Dict]:
        if user_id not in self.students and user_id not in self.helpers:
            raise ValueError(f"User {user_id} not found")
        
        if request_id not in self.requests:
            raise ValueError(f"Request {request_id} not found")
        
        channel = None
        for ch in self.communication_channels.values():
            if ch.request_id == request_id:
                channel = ch
                break
        
        if not channel:
            return []
        
        return channel.get_messages(user_id)
    
    def set_helper_status(self, helper_id: str, status: HelperStatus) -> bool:
        if helper_id not in self.helpers:
            raise ValueError(f"Helper {helper_id} not found")
        
        helper = self.helpers[helper_id]
        
        if helper.current_assignment and status != HelperStatus.BUSY:
            raise ValueError("Cannot change status while assigned to a request")
        
        helper.status = status
        return True
    
    def _process_expired_offers(self):
        for request in self.requests.values():
            if request.is_expired():
                request.status = RequestStatus.PENDING
                request.offered_helpers = []
                request.offer_expiry = None
    
    def get_system_stats(self) -> Dict:
        total_requests = len(self.requests)
        pending = sum(1 for r in self.requests.values() if r.status == RequestStatus.PENDING)
        matched = sum(1 for r in self.requests.values() if r.status == RequestStatus.MATCHED)
        completed = sum(1 for r in self.requests.values() if r.status == RequestStatus.COMPLETED)
        available_helpers = len(self.get_available_helpers())
        
        return {
            "total_students": len(self.students),
            "total_helpers": len(self.helpers),
            "available_helpers": available_helpers,
            "total_requests": total_requests,
            "pending_requests": pending,
            "matched_requests": matched,
            "completed_requests": completed,
            "queue_length": len(self.request_queue)
        }


if __name__ == "__main__":
    system = CampusSafetySystem(offer_timeout_seconds=30)
    
    student1 = system.register_student("Alice Johnson", "555-0101")
    student2 = system.register_student("Bob Smith", "555-0102")
    
    helper1 = system.register_helper("Charlie Brown", "555-0201")
    helper2 = system.register_helper("Diana Prince", "555-0202")
    helper3 = system.register_helper("Ethan Hunt", "555-0203")
    
    print("=== Campus Safety Companion System Demo ===\n")
    
    print("--- Scenario 1: Basic Request Flow ---")
    request1 = system.submit_request(
        student1.user_id,
        "Main Library",
        "North Dormitory",
        "Prefer someone familiar with the route"
    )
    print(f"Student {student1.name} created request: {request1.request_id}")
    print(f"Route: {request1.start_location} → {request1.end_location}")
    
    available_requests = system.view_requests(helper1.user_id)
    print(f"\nHelper {helper1.name} sees {len(available_requests)} available request(s)")
    
    system.offer_help(helper1.user_id, request1.request_id)
    print(f"Helper {helper1.name} offered to help")
    
    offered_helpers = system.get_offered_helpers(student1.user_id, request1.request_id)
    print(f"Student {student1.name} sees {len(offered_helpers)} helper(s) offered")
    
    system.accept_helper(student1.user_id, request1.request_id, helper1.user_id)
    print(f"Student {student1.name} accepted helper {helper1.name}")
    
    system.send_message(student1.user_id, request1.request_id, "Hi! I'm by the main entrance")
    system.send_message(helper1.user_id, request1.request_id, "Great! I'll be there in 2 minutes")
    
    messages = system.get_messages(student1.user_id, request1.request_id)
    print(f"\nCommunication channel has {len(messages)} message(s)")
    
    system.complete_walk(helper1.user_id, request1.request_id)
    print(f"Walk completed! Helper {helper1.name} is now available again")
    
    print("\n--- Scenario 2: Multiple Helpers Offering ---")
    request2 = system.submit_request(
        student2.user_id,
        "Science Building",
        "East Parking Lot"
    )
    print(f"Student {student2.name} created request: {request2.request_id}")
    
    system.offer_help(helper1.user_id, request2.request_id)
    system.offer_help(helper2.user_id, request2.request_id)
    system.offer_help(helper3.user_id, request2.request_id)
    print(f"Three helpers offered: {helper1.name}, {helper2.name}, {helper3.name}")
    
    offered_helpers = system.get_offered_helpers(student2.user_id, request2.request_id)
    print(f"Student sees {len(offered_helpers)} helper options")
    
    system.accept_helper(student2.user_id, request2.request_id, helper2.user_id)
    print(f"Student chose {helper2.name}")
    
    system.complete_walk(helper2.user_id, request2.request_id)
    
    print("\n--- Scenario 3: No Helpers Available ---")
    system.set_helper_status(helper1.user_id, HelperStatus.OFFLINE)
    system.set_helper_status(helper3.user_id, HelperStatus.OFFLINE)
    
    request3 = system.submit_request(
        student1.user_id,
        "Gym",
        "South Dormitory"
    )
    print(f"Student {student1.name} created request: {request3.request_id}")
    
    available_helpers = system.get_available_helpers()
    print(f"Available helpers: {len(available_helpers)}")
    if len(available_helpers) == 0:
        print("System detected no helpers available - student should be notified to wait")
    
    print("\n--- System Statistics ---")
    stats = system.get_system_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")
    
    print("\n--- Edge Case: Student Cancels Request ---")
    system.cancel_request(student1.user_id, request3.request_id)
    print(f"Request {request3.request_id} cancelled")
    print(f"Request status: {system.requests[request3.request_id].status.value}")




# --- BRIDGE FUNCTION FOR UNIT TESTS ---
def assign_helpers(available_helpers: list, student_request: dict) -> list:
    """
    Bridge to map the class-based CampusSafetySystem to the 
    functional unit test requirement.
    """
    test_system = CampusSafetySystem()
    
    # 1. Register Helpers
    helper_objects = []
    for h_name in available_helpers:
        # In this system, available_helpers is a list of names
        helper_objects.append(test_system.register_helper(h_name, "000-0000"))
    
    # 2. Register Student and Submit Request
    # student_request is expected to be a dict like {"id": "123", "name": "Alice"}
    stu_name = student_request.get("name", "Test Student")
    stu_phone = student_request.get("phone", "000-0000")
    student = test_system.register_student(stu_name, stu_phone)
    
    # Override generated ID with test ID if provided for test consistency
    if "id" in student_request:
        test_system.students[student_request["id"]] = student
        student.user_id = student_request["id"]

    try:
        req = test_system.submit_request(student.user_id, "Start", "End")
        
        # 3. Simulate helpers offering help
        for h in test_system.helpers.values():
            test_system.offer_help(h.user_id, req.request_id)
            
        # 4. Return public profiles of offered helpers (as the test expects)
        return test_system.get_offered_helpers(student.user_id, req.request_id)
    except Exception:
        return []