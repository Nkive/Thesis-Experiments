import threading
import queue
import hashlib
import uuid
import time
from collections import defaultdict
from typing import Dict, List, Optional

# =========================================================
# IN-MEMORY STORAGE
# =========================================================

users = {}
volunteers = {}
escort_requests = {}
active_chats = {}
ratings = defaultdict(list)

# =========================================================
# LOAD BALANCER
# =========================================================

class LoadBalancer:
    def __init__(self):
        self.servers = ["server_1", "server_2", "server_3"]
        self.current = 0
        self.lock = threading.Lock()

    def route_request(self):
        with self.lock:
            server = self.servers[self.current]
            self.current = (self.current + 1) % len(self.servers)
            return server


load_balancer = LoadBalancer()

# =========================================================
# USER MODELS
# =========================================================

class User:
    def __init__(self, name, school_email, student_id_photo):
        self.user_id = str(uuid.uuid4())
        self.name = name
        self.school_email = school_email
        self.student_id_photo = student_id_photo
        self.verified = False
        self.location = None

    def minimal_info(self):
        return {
            "name": self.name,
            "location": self.location
        }


class Volunteer(User):
    def __init__(self, name, school_email, student_id_photo):
        super().__init__(name, school_email, student_id_photo)
        self.available = True
        self.active_requests = 0
        self.max_requests = 3


# =========================================================
# STUDENT VERIFICATION
# =========================================================

def verify_school_email(email):
    allowed_domains = [
        "@university.edu",
        "@student.school.edu"
    ]

    for domain in allowed_domains:
        if email.endswith(domain):
            return True

    return False


def verify_student_id(student_id_photo):
    # Placeholder for OCR / AI verification
    return student_id_photo is not None


def verify_user(user):
    email_verified = verify_school_email(user.school_email)
    id_verified = verify_student_id(user.student_id_photo)

    user.verified = email_verified and id_verified
    return user.verified


# =========================================================
# REGISTRATION
# =========================================================

def register_student(name, email, student_id_photo):
    user = User(name, email, student_id_photo)

    if verify_user(user):
        users[user.user_id] = user
        return user.user_id

    return None


def register_volunteer(name, email, student_id_photo):
    volunteer = Volunteer(name, email, student_id_photo)

    if verify_user(volunteer):
        volunteers[volunteer.user_id] = volunteer
        return volunteer.user_id

    return None


# =========================================================
# VOLUNTEER MATCHING
# =========================================================

def get_available_volunteers():
    available = []

    for volunteer in volunteers.values():
        if volunteer.available and volunteer.active_requests < volunteer.max_requests:
            available.append(volunteer)

    return available


def select_best_volunteer():
    available = get_available_volunteers()

    if not available:
        return None

    # Least loaded volunteer
    available.sort(key=lambda v: v.active_requests)

    return available[0]


# =========================================================
# REQUEST SYSTEM
# =========================================================

class EscortRequest:
    def __init__(self, student_id, destination, meeting_point):
        self.request_id = str(uuid.uuid4())
        self.student_id = student_id
        self.destination = destination
        self.meeting_point = meeting_point
        self.status = "PENDING"
        self.assigned_volunteer = None
        self.timestamp = time.time()


request_queue = queue.Queue()


def create_escort_request(student_id, destination, meeting_point):
    server = load_balancer.route_request()

    request_obj = EscortRequest(
        student_id,
        destination,
        meeting_point
    )

    escort_requests[request_obj.request_id] = request_obj

    request_queue.put(request_obj.request_id)

    print(f"Request routed through {server}")

    return request_obj.request_id


# =========================================================
# REQUEST DISTRIBUTION
# =========================================================

def distribute_requests():
    while True:
        if not request_queue.empty():
            request_id = request_queue.get()

            request_obj = escort_requests[request_id]

            volunteer = select_best_volunteer()

            if volunteer:
                request_obj.assigned_volunteer = volunteer.user_id
                request_obj.status = "WAITING_ACCEPTANCE"

                volunteer.active_requests += 1

                notify_volunteer(volunteer.user_id, request_id)

        time.sleep(1)


# =========================================================
# NOTIFICATION SYSTEM
# =========================================================

notifications = defaultdict(list)


def notify_volunteer(volunteer_id, request_id):
    notifications[volunteer_id].append({
        "request_id": request_id,
        "message": "New escort request available"
    })


def get_notifications(volunteer_id):
    return notifications[volunteer_id]


# =========================================================
# ACCEPT / DECLINE REQUESTS
# =========================================================

def volunteer_accept_request(volunteer_id, request_id):
    request_obj = escort_requests.get(request_id)

    if not request_obj:
        return False

    if request_obj.assigned_volunteer != volunteer_id:
        return False

    request_obj.status = "ACCEPTED"

    initialize_chat(request_id)

    return True


def volunteer_decline_request(volunteer_id, request_id):
    request_obj = escort_requests.get(request_id)

    if not request_obj:
        return False

    volunteer = volunteers[volunteer_id]

    volunteer.active_requests -= 1

    request_obj.assigned_volunteer = None
    request_obj.status = "PENDING"

    request_queue.put(request_id)

    return True


# =========================================================
# CHAT SYSTEM
# =========================================================

class ChatRoom:
    def __init__(self, request_id):
        self.request_id = request_id
        self.messages = []
        self.lock = threading.Lock()

    def send_message(self, sender_id, message):
        with self.lock:
            self.messages.append({
                "sender": sender_id,
                "message": message,
                "timestamp": time.time()
            })

    def get_messages(self):
        return self.messages


def initialize_chat(request_id):
    active_chats[request_id] = ChatRoom(request_id)


def send_chat_message(request_id, sender_id, message):
    chat = active_chats.get(request_id)

    if not chat:
        return False

    chat.send_message(sender_id, message)
    return True


def get_chat_messages(request_id):
    chat = active_chats.get(request_id)

    if not chat:
        return []

    return chat.get_messages()


# =========================================================
# PRIVACY PROTECTION
# =========================================================

def get_student_info_for_volunteer(student_id):
    user = users.get(student_id)

    if not user:
        return None

    return user.minimal_info()


def hash_sensitive_data(data):
    return hashlib.sha256(data.encode()).hexdigest()


# =========================================================
# RATING SYSTEM
# =========================================================

def submit_rating(from_user, to_user, score, comment=""):
    if score < 1 or score > 5:
        return False

    ratings[to_user].append({
        "from": from_user,
        "score": score,
        "comment": comment
    })

    return True


def get_average_rating(user_id):
    user_ratings = ratings[user_id]

    if not user_ratings:
        return 0

    total = sum(r["score"] for r in user_ratings)

    return round(total / len(user_ratings), 2)


# =========================================================
# HIGH TRAFFIC SUPPORT
# =========================================================

def start_request_workers(worker_count=5):
    workers = []

    for _ in range(worker_count):
        worker = threading.Thread(
            target=distribute_requests,
            daemon=True
        )

        worker.start()
        workers.append(worker)

    return workers


# =========================================================
# ACTIVE SESSION TRACKING
# =========================================================

active_sessions = {}


def create_session(user_id):
    token = str(uuid.uuid4())

    active_sessions[token] = {
        "user_id": user_id,
        "created": time.time()
    }

    return token


def validate_session(token):
    return token in active_sessions


# =========================================================
# REQUEST COMPLETION
# =========================================================

def complete_request(request_id):
    request_obj = escort_requests.get(request_id)

    if not request_obj:
        return False

    volunteer_id = request_obj.assigned_volunteer

    if volunteer_id:
        volunteers[volunteer_id].active_requests -= 1

    request_obj.status = "COMPLETED"

    return True


# =========================================================
# SCALE TEST
# =========================================================

def simulate_1000_users():
    for i in range(1000):
        register_student(
            f"Student_{i}",
            f"student{i}@university.edu",
            "student_card.png"
        )

    print("1000 users loaded successfully")
