import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional

# ----------------------
# Data Models
# ----------------------

@dataclass
class StudentRequest:
    request_id: str
    student_id: str
    location: str
    destination: str
    assigned_helper_id: Optional[str] = None
    status: str = "pending"

@dataclass
class Helper:
    helper_id: str
    name: str
    is_available: bool = True

# ----------------------
# Simulated Distributed Store
# ----------------------

class SharedStore:
    def __init__(self):
        self.requests = {}
        self.helpers = {}

store = SharedStore()

# ----------------------
# Distributed Queue
# ----------------------

class DistributedQueue:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def push(self, item):
        await self.queue.put(item)

    async def pop(self):
        return await self.queue.get()

request_queue = DistributedQueue()

# ----------------------
# Services (UNCHANGED LOGIC)
# ----------------------

class RequestService:
    async def create_request(self, student_id: str, location: str, destination: str):
        request_id = str(uuid.uuid4())
        request = StudentRequest(request_id, student_id, location, destination)
        store.requests[request_id] = request
        await request_queue.push(request_id)

class HelperService:
    def register_helper(self, name: str) -> str:
        helper_id = str(uuid.uuid4())
        store.helpers[helper_id] = Helper(helper_id, name)
        return helper_id

    def get_available_helper(self) -> Optional[Helper]:
        for helper in store.helpers.values():
            if helper.is_available:
                return helper
        return None

class MatchingService:
    def __init__(self, helper_service: HelperService):
        self.helper_service = helper_service

    async def run(self):
        while True:
            request_id = await request_queue.pop()
            request = store.requests.get(request_id)

            helper = self.helper_service.get_available_helper()

            if helper:
                self.assign(request, helper)
            else:
                await asyncio.sleep(0.5)
                await request_queue.push(request_id)

    def assign(self, request: StudentRequest, helper: Helper):
        helper.is_available = False
        request.assigned_helper_id = helper.helper_id
        request.status = "assigned"

class NotificationService:
    @staticmethod
    def notify_helper(helper: Helper, request: StudentRequest):
        pass

    @staticmethod
    def notify_student(request: StudentRequest):
        pass

class MessagingService:
    async def send_message(self, sender_id: str, receiver_id: str, message: str):
        pass

class CompletionService:
    def complete(self, request_id: str):
        request = store.requests.get(request_id)
        if not request:
            return

        helper = store.helpers.get(request.assigned_helper_id)
        if helper:
            helper.is_available = True

        request.status = "completed"

# ----------------------
# Worker
# ----------------------

async def start_worker(worker_id: int, matching_service: MatchingService):
    await matching_service.run()
