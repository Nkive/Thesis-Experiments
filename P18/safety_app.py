from dataclasses import dataclass, field
from typing import Dict, List, Optional
import threading
import time
import uuid


# -----------------------------
# DATA MODELS
# -----------------------------

@dataclass
class Student:
    student_id: str
    name: str
    phone: str  # sensitive (NOT shared with helpers)


@dataclass
class Helper:
    helper_id: str
    is_available: bool = True
    active_assignment: Optional[str] = None


@dataclass
class HelpRequest:
    request_id: str
    student_id: str
    current_location: str
    destination: str
    requested_time: float

    status: str = "active"  # active, assigned, completed, cancelled
    assigned_helper: Optional[str] = None


@dataclass
class Message:
    sender_id: str
    content: str
    timestamp: float


# -----------------------------
# CORE SYSTEM
# -----------------------------

class CampusSafetySystem:
    def __init__(self):
        self.students: Dict[str, Student] = {}
        self.helpers: Dict[str, Helper] = {}
        self.requests: Dict[str, HelpRequest] = {}
        self.chats: Dict[str, List[Message]] = {}

        self.lock = threading.Lock()

    # -----------------------------
    # STUDENTS & HELPERS
    # -----------------------------

    def register_student(self, student_id: str, name: str, phone: str):
        with self.lock:
            self.students[student_id] = Student(student_id, name, phone)

    def register_helper(self, helper_id: str):
        with self.lock:
            self.helpers[helper_id] = Helper(helper_id)

    # -----------------------------
    # 1. SUBMIT REQUEST
    # -----------------------------

    def submit_request(self, student_id: str, current_location: str, destination: str, requested_time: float):
        with self.lock:
            request_id = str(uuid.uuid4())

            req = HelpRequest(
                request_id=request_id,
                student_id=student_id,
                current_location=current_location,
                destination=destination,
                requested_time=requested_time
            )

            self.requests[request_id] = req
            self._notify_student(student_id, f"Request {request_id} submitted.")
            return request_id

    # -----------------------------
    # 2. VIEW ACTIVE REQUESTS (PRIVACY FILTERED)
    # -----------------------------

    def get_active_requests_for_helpers(self) -> List[dict]:
        """Helpers only see minimal safe info."""
        with self.lock:
            return [
                {
                    "request_id": r.request_id,
                    "current_location": r.current_location,
                    "destination": r.destination,
                    "requested_time": r.requested_time
                }
                for r in self.requests.values()
                if r.status == "active"
            ]

    # -----------------------------
    # 3. ACCEPT REQUEST (ONLY ONE HELPER GUARANTEED)
    # -----------------------------

    def accept_request(self, helper_id: str, request_id: str) -> bool:
        with self.lock:
            if helper_id not in self.helpers or request_id not in self.requests:
                return False

            helper = self.helpers[helper_id]
            request = self.requests[request_id]

            # MUST ensure only ONE helper can accept
            if request.status != "active":
                return False

            if not helper.is_available:
                return False

            # assign atomically
            request.status = "assigned"
            request.assigned_helper = helper_id

            helper.is_available = False
            helper.active_assignment = request_id

            # create chat room
            self.chats[request_id] = []

            # notifications
            self._notify_student(
                request.student_id,
                f"Helper {helper_id} has been assigned to your request."
            )
            self._notify_helper(
                helper_id,
                f"You have been assigned to request {request_id}"
            )

            return True

    # -----------------------------
    # 4. SAFE COMMUNICATION SYSTEM
    # -----------------------------

    def send_message(self, sender_id: str, request_id: str, content: str):
        with self.lock:
            if request_id not in self.chats:
                return False

            msg = Message(
                sender_id=sender_id,
                content=content,
                timestamp=time.time()
            )

            self.chats[request_id].append(msg)
            return True

    def get_chat(self, request_id: str) -> List[Message]:
        with self.lock:
            return self.chats.get(request_id, [])

    # -----------------------------
    # 5. PRIVACY CONTROL
    # -----------------------------

    def get_student_safe_view(self, request_id: str):
        """Student sees full info including helper once assigned."""
        with self.lock:
            req = self.requests.get(request_id)
            if not req:
                return None

            return {
                "request_id": req.request_id,
                "status": req.status,
                "helper_id": req.assigned_helper
            }

    def get_helper_safe_student_info(self, request_id: str):
        """Helpers NEVER see private student data."""
        with self.lock:
            req = self.requests.get(request_id)
            if not req:
                return None

            student = self.students.get(req.student_id)

            return {
                "current_location": req.current_location,
                "destination": req.destination,
                "requested_time": req.requested_time,
                "student_name": student.name if student else None  # minimal safe info
            }

    # -----------------------------
    # 6. CANCEL REQUEST
    # -----------------------------

    def cancel_request(self, student_id: str, request_id: str):
        with self.lock:
            if request_id not in self.requests:
                return False

            req = self.requests[request_id]

            if req.student_id != student_id:
                return False

            req.status = "cancelled"

            # free helper if assigned
            if req.assigned_helper:
                helper = self.helpers.get(req.assigned_helper)
                if helper:
                    helper.is_available = True
                    helper.active_assignment = None

            self._notify_student(student_id, f"Request {request_id} cancelled.")
            return True

    # -----------------------------
    # 7. AUTO MATCHING LOOP (HANDLES NO HELPERS AVAILABLE CASE)
    # -----------------------------

    def auto_match_loop(self, interval: float = 2.0):
        """Keeps system responsive and assigns when helpers appear."""
        while True:
            with self.lock:
                available_helpers = [
                    h for h in self.helpers.values()
                    if h.is_available
                ]

                active_requests = [
                    r for r in self.requests.values()
                    if r.status == "active"
                ]

                for helper in available_helpers:
                    for req in active_requests:
                        # assign first available
                        self._safe_assign(helper.helper_id, req.request_id)
                        active_requests.remove(req)
                        break

            time.sleep(interval)

    def _safe_assign(self, helper_id: str, request_id: str):
        """Internal assignment (ensures atomic single-helper rule)."""
        helper = self.helpers[helper_id]
        req = self.requests[request_id]

        if req.status != "active":
            return False

        req.status = "assigned"
        req.assigned_helper = helper_id

        helper.is_available = False
        helper.active_assignment = request_id

        self.chats[request_id] = []

        self._notify_student(req.student_id, f"Helper {helper_id} assigned.")
        self._notify_helper(helper_id, f"You are assigned to {request_id}")

        return True

    # -----------------------------
    # 8. NOTIFICATIONS (SIMULATED)
    # -----------------------------

    def _notify_student(self, student_id: str, message: str):
        student = self.students.get(student_id)
        if student:
            print(f"[NOTIFY STUDENT {student.name}] {message}")

    def _notify_helper(self, helper_id: str, message: str):
        print(f"[NOTIFY HELPER {helper_id}] {message}")