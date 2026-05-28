from datetime import datetime, timedelta
import uuid
import math
import threading

# ----------------------------
# Concurrency Lock (NEW)
# ----------------------------
lock = threading.Lock()

# ----------------------------
# In-memory storage
# ----------------------------

safety_requests = {}
helpers = {}
students = {}
messages = {}

# ----------------------------
# Status Constants
# ----------------------------

REQUEST_PENDING = "PENDING"
REQUEST_ACCEPTED = "ACCEPTED"
REQUEST_COMPLETED = "COMPLETED"
REQUEST_CANCELLED = "CANCELLED"
REQUEST_NO_HELPER = "NO_HELPER_FOUND"

# ----------------------------
# Models
# ----------------------------

class SafetyWalkRequest:
    def __init__(self, student_id, current_location, destination, preferred_time):
        self.request_id = str(uuid.uuid4())
        self.student_id = student_id
        self.current_location = current_location
        self.destination = destination
        self.preferred_time = preferred_time

        self.status = REQUEST_PENDING
        self.assigned_helper = None
        self.helper_responses = []
        self.created_at = datetime.now()


class Helper:
    def __init__(self, helper_id, name, location):
        self.helper_id = helper_id
        self.name = name
        self.location = location
        self.is_busy = False


class Student:
    def __init__(self, student_id, name, phone, emergency_contact):
        self.student_id = student_id
        self.name = name
        self.phone = phone
        self.emergency_contact = emergency_contact

# ----------------------------
# Utility
# ----------------------------

def calculate_distance(loc1, loc2):
    return math.sqrt((loc1[0]-loc2[0])**2 + (loc1[1]-loc2[1])**2)


def send_notification(user_id, message):
    print(f"[NOTIFICATION -> {user_id}] {message}")

# ----------------------------
# 1. Create Request
# ----------------------------

def create_safety_walk_request(student_id, current_location, destination, preferred_time):
    with lock:
        request = SafetyWalkRequest(student_id, current_location, destination, preferred_time)
        safety_requests[request.request_id] = request
        return request.request_id

# ----------------------------
# 2. View Nearby Requests
# ----------------------------

def view_nearby_requests(helper_id, max_distance=10):
    with lock:
        if helper_id not in helpers:
            return []

        helper = helpers[helper_id]
        result = []

        for req in safety_requests.values():
            if req.status != REQUEST_PENDING:
                continue

            if calculate_distance(helper.location, req.current_location) <= max_distance:
                result.append(req)

        return result

# ----------------------------
# 3. Accept Request
# ----------------------------

def accept_request(helper_id, request_id):
    with lock:
        if helper_id not in helpers:
            return "Helper not found"

        if request_id not in safety_requests:
            return "Request not found"

        helper = helpers[helper_id]
        req = safety_requests[request_id]

        if helper.is_busy:
            return "Helper already assigned"

        if req.status != REQUEST_PENDING:
            return "Request not available"

        # multiple helpers can respond
        if helper_id not in req.helper_responses:
            req.helper_responses.append(helper_id)

        # assign only one
        req.assigned_helper = helper_id
        req.status = REQUEST_ACCEPTED

        helper.is_busy = True

        send_notification(req.student_id, f"{helper.name} accepted your request")

        return "Accepted"

# ----------------------------
# 4. Decline Request
# ----------------------------

def decline_request(helper_id, request_id):
    with lock:
        if request_id not in safety_requests:
            return "Request not found"

        req = safety_requests[request_id]

        if helper_id not in req.helper_responses:
            req.helper_responses.append(helper_id)

        return "Declined"

# ----------------------------
# 5. CANCEL REQUEST (NEW REQUIREMENT)
# ----------------------------

def cancel_request(student_id, request_id):
    with lock:
        if request_id not in safety_requests:
            return "Request not found"

        req = safety_requests[request_id]

        if req.student_id != student_id:
            return "Unauthorized"

        # Only allow cancel before completion
        if req.status == REQUEST_COMPLETED:
            return "Cannot cancel completed request"

        req.status = REQUEST_CANCELLED

        # free helper if assigned
        if req.assigned_helper:
            helpers[req.assigned_helper].is_busy = False
            req.assigned_helper = None

        send_notification(student_id, "Your request has been cancelled")

        return "Cancelled"

# ----------------------------
# 6. Complete Walk
# ----------------------------

def complete_walk(request_id):
    with lock:
        if request_id not in safety_requests:
            return "Request not found"

        req = safety_requests[request_id]
        req.status = REQUEST_COMPLETED

        if req.assigned_helper:
            helpers[req.assigned_helper].is_busy = False

        return "Completed"

# ----------------------------
# 7. Protect Sensitive Info
# ----------------------------

def get_request_details_for_helper(helper_id, request_id):
    with lock:
        if request_id not in safety_requests:
            return "Request not found"

        req = safety_requests[request_id]

        if req.assigned_helper != helper_id:
            return "Access denied"

        student = students[req.student_id]

        # only minimal safe data
        return {
            "student_name": student.name,
            "pickup": req.current_location,
            "destination": req.destination,
            "time": req.preferred_time,
            "status": req.status
        }

# ----------------------------
# 8. Request Status (NEW REQUIREMENT)
# ----------------------------

def get_request_status(request_id):
    with lock:
        if request_id not in safety_requests:
            return "Request not found"

        req = safety_requests[request_id]

        return {
            "request_id": req.request_id,
            "status": req.status,
            "assigned_helper": req.assigned_helper
        }

# ----------------------------
# 9. No Helper Timeout
# ----------------------------

def check_unassigned_requests(timeout_minutes=5):
    with lock:
        now = datetime.now()

        for req in safety_requests.values():
            if req.status == REQUEST_PENDING:
                if now - req.created_at > timedelta(minutes=timeout_minutes):
                    req.status = REQUEST_NO_HELPER
                    send_notification(req.student_id, "No helper available")

# ----------------------------
# 10. Messaging (secure)
# ----------------------------

def send_message(request_id, sender_id, text):
    with lock:
        req = safety_requests.get(request_id)
        if not req:
            return "Request not found"

        allowed = [req.student_id, req.assigned_helper]
        if sender_id not in allowed:
            return "Unauthorized"

        messages.setdefault(request_id, []).append({
            "sender": sender_id,
            "text": text,
            "time": datetime.now()
        })

        return "Sent"


def get_messages(request_id):
    return messages.get(request_id, [])

# ----------------------------
# Example Setup
# ----------------------------

students["s1"] = Student("s1", "Alice", "070123", "Mom: 070999")
helpers["h1"] = Helper("h1", "Bob", (10, 10))

req_id = create_safety_walk_request("s1", (12, 12), "Library", "22:00")

print(get_request_status(req_id))
print(accept_request("h1", req_id))
print(get_request_status(req_id))
print(cancel_request("s1", req_id))  # will now be blocked if completed or allowed otherwise