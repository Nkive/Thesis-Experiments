import threading
import time
from queue import Queue
from typing import Dict, Optional
import uuid

# Thread-safe queue for incoming requests
requests_queue = Queue()

# Shared state (protected with lock)
available_helpers = set()
active_assignments: Dict[str, str] = {}  # request_id -> helper_id

lock = threading.Lock()


def anonymize_user_data(data: dict) -> dict:
    """Remove or mask sensitive information for GDPR compliance."""
    return {
        "request_id": data["request_id"],
        "location": data["location"],
        "destination": data["destination"],
        "time": data["time"]
    }


def submit_help_request(location: str, destination: str, request_time: str) -> dict:
    """Student submits a help request."""
    request_id = str(uuid.uuid4())

    request = {
        "request_id": request_id,
        "location": location,
        "destination": destination,
        "time": request_time,
    }

    requests_queue.put(request)

    return {
        "status": "queued",
        "message": "Your request has been received. Searching for a helper.",
        "request_id": request_id
    }


def register_helper(helper_id: str):
    """Helper becomes available."""
    with lock:
        available_helpers.add(helper_id)


def process_requests():
    """Continuously match requests with helpers."""
    while True:
        if not requests_queue.empty():
            request = requests_queue.get()

            with lock:
                if available_helpers:
                    helper_id = available_helpers.pop()
                    active_assignments[request["request_id"]] = helper_id

                    safe_request = anonymize_user_data(request)

                    print(f"[MATCHED] Helper {helper_id} assigned to request {safe_request}")
                else:
                    print("[INFO] No helper available. Request remains in queue.")
                    requests_queue.put(request)
                    time.sleep(2)  # avoid busy loop
        else:
            time.sleep(1)


def get_request_status(request_id: str) -> dict:
    """Check status of a request."""
    with lock:
        if request_id in active_assignments:
            return {
                "status": "assigned",
                "message": "A helper has been assigned and will contact you shortly."
            }

    return {
        "status": "pending",
        "message": "Still waiting for an available helper."
    }

def assign_helpers(helpers_list, student_data):
 
    if not helpers_list:
        return "No helpers available"

    safe_data = {k: v for k, v in student_data.items() if k != "student_id"}
    
    assignment = {
        "helper": helpers_list[0],
        "student": safe_data
    }
    
    return assignment

def assign_helpers(helpers_list, student_data):

    helper = helpers_list[0] 

    return {
        "helper": helper,
        "student": student_data 
    }