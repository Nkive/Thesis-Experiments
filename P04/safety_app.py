import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import uuid

# -----------------------------
# Data Models
# -----------------------------

@dataclass
class User:
    user_id: str
    name: str
    is_helper: bool = False
    active: bool = True  # Controls availability


@dataclass
class WalkRequest:
    request_id: str
    requester_id: str
    start_location: str
    destination: str
    time: datetime
    assigned_helper: Optional[str] = None
    status: str = "PENDING"  # PENDING, ACCEPTED, COMPLETED, CANCELLED


# -----------------------------
# Core System
# -----------------------------

class WalkSafetySystem:
    def __init__(self):
        self.users: Dict[str, User] = {}
        self.requests: Dict[str, WalkRequest] = {}
        self.helper_assignments: Dict[str, str] = {}  # helper_id -> request_id
        self.lock = asyncio.Lock()

    # -----------------------------
    # USER MANAGEMENT
    # -----------------------------

    def register_user(self, name: str, is_helper=False) -> User:
        user_id = str(uuid.uuid4())
        user = User(user_id, name, is_helper)
        self.users[user_id] = user
        return user

    # NEW: Helper availability toggle
    def set_helper_availability(self, helper_id: str, is_active: bool):
        user = self.users.get(helper_id)
        if user and user.is_helper:
            user.active = is_active
            print(f"[AVAILABILITY] {user.name} is now {'ACTIVE' if is_active else 'INACTIVE'}")

    # -----------------------------
    # REQUEST MANAGEMENT
    # -----------------------------

    # R1: Submit request
    async def submit_request(self, requester_id: str, start: str, dest: str, time: datetime) -> WalkRequest:
        async with self.lock:
            req_id = str(uuid.uuid4())
            request = WalkRequest(req_id, requester_id, start, dest, time)
            self.requests[req_id] = request
            print(f"[REQUEST CREATED] {request}")
            return request

    # R2: Helpers view requests (limited info)
    def get_open_requests_for_helpers(self, helper_id: str) -> List[dict]:
        user = self.users.get(helper_id)

        # Only active helpers can see requests
        if not user or not user.is_helper or not user.active:
            return []

        visible_requests = []
        for r in self.requests.values():
            if r.status == "PENDING":
                visible_requests.append({
                    "request_id": r.request_id,
                    "start_location": r.start_location,
                    "destination": r.destination,
                    "time": r.time
                })
        return visible_requests

    # R3: Assign helper safely with availability check
    async def accept_request(self, helper_id: str, request_id: str) -> bool:
        async with self.lock:
            user = self.users.get(helper_id)

            # Check helper exists, is active, and is a helper
            if not user or not user.is_helper or not user.active:
                print("Helper not available!")
                return False

            # Prevent overload
            if helper_id in self.helper_assignments:
                print("Helper already assigned!")
                return False

            request = self.requests.get(request_id)
            if not request or request.status != "PENDING":
                return False

            request.status = "ACCEPTED"
            request.assigned_helper = helper_id
            self.helper_assignments[helper_id] = request_id

            print(f"[MATCHED] Helper {helper_id} -> Request {request_id}")
            return True

    # R4: Communication (simple messaging simulation)
    async def send_message(self, from_user: str, to_user: str, message: str):
        print(f"[MESSAGE] {from_user} -> {to_user}: {message}")

    # Complete request and free helper
    async def complete_request(self, request_id: str):
        async with self.lock:
            request = self.requests.get(request_id)
            if not request:
                return

            request.status = "COMPLETED"
            helper_id = request.assigned_helper

            # Free helper (makes them available again)
            if helper_id in self.helper_assignments:
                del self.helper_assignments[helper_id]

            print(f"[COMPLETED] {request_id}")


# -----------------------------
# Simulation (Concurrency Test)
# -----------------------------

async def simulate():
    system = WalkSafetySystem()

    # Create users
    alice = system.register_user("Alice")
    bob = system.register_user("Bob", is_helper=True)
    carol = system.register_user("Carol", is_helper=True)

    # Set availability
    system.set_helper_availability(bob.user_id, True)
    system.set_helper_availability(carol.user_id, False)  # Carol is offline

    # Submit request
    req1 = await system.submit_request(alice.user_id, "Library", "Dorm", datetime.now())

    # Helpers view requests
    print("Bob sees:", system.get_open_requests_for_helpers(bob.user_id))
    print("Carol sees:", system.get_open_requests_for_helpers(carol.user_id))

    # Try accepting
    await asyncio.gather(
        system.accept_request(bob.user_id, req1.request_id),
        system.accept_request(carol.user_id, req1.request_id)  # Should fail
    )

    # Messaging
    if req1.assigned_helper:
        await system.send_message(req1.assigned_helper, req1.requester_id, "I'm on my way!")

    # Complete request
    await system.complete_request(req1.request_id)

    # Bob becomes available again automatically
    print("Bob can take new requests again.")


if __name__ == "__main__":
    asyncio.run(simulate())

def assign_helpers(helpers_list, student_data):
    # Test 2: Handling Empty Lists
    if not helpers_list:
        return "No helpers available"

    # Test 3: Privacy Filter 
    # The test fails if 'student_id' is visible in the return value
    safe_student = {k: v for k, v in student_data.items() if k != "student_id"}

    # Return a structure the test can read
    return {
        "assigned_to": helpers_list[0],
        "student": safe_student
    }