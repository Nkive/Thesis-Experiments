import time
import threading
from datetime import datetime, timedelta

# -----------------------------
# Simple in-memory data storage
# -----------------------------
users = {}
helpers = {}
requests = {}

# -----------------------------
# Utility functions
# -----------------------------
def send_email(user_email, message):
    print(f"[EMAIL to {user_email}]: {message}")


def send_notification(user_id, message):
    print(f"[NOTIFICATION to {user_id}]: {message}")


# -----------------------------
# Models (simple dict-based)
# -----------------------------
def register_user(user_id, email):
    users[user_id] = {
        "email": email,
        "shared_info": {"location": True, "destination": True, "time": True}
    }
    send_email(email, "Registration successful!")


def register_helper(helper_id):
    helpers[helper_id] = {
        "available": True
    }


# -----------------------------
# Request Handling
# -----------------------------
def create_request(user_id, location, destination, time_needed):
    request_id = len(requests) + 1
    requests[request_id] = {
        "user_id": user_id,
        "location": location,
        "destination": destination,
        "time": time_needed,
        "helpers": [],
        "status": "waiting"
    }

    notify_helpers(request_id)
    estimate_wait_time(request_id)

    return request_id


def notify_helpers(request_id):
    for helper_id, helper in helpers.items():
        if helper["available"]:
            send_notification(helper_id, f"New request available: {request_id}")


def helper_accept_request(helper_id, request_id):
    req = requests.get(request_id)

    if not req:
        return

    if len(req["helpers"]) >= 1:
        send_notification(helper_id, "Request already has enough helpers. Cancelled.")
        return

    req["helpers"].append(helper_id)
    helpers[helper_id]["available"] = False

    send_notification(req["user_id"], f"Helper {helper_id} accepted your request.")

    # Cancel extra helpers automatically
    cancel_extra_helpers(request_id)


def cancel_extra_helpers(request_id):
    req = requests[request_id]

    if len(req["helpers"]) > 1:
        extra_helpers = req["helpers"][1:]
        req["helpers"] = req["helpers"][:1]

        for helper_id in extra_helpers:
            helpers[helper_id]["available"] = True
            send_notification(helper_id, "You were removed due to excess helpers.")


def cancel_specific_helper(user_id, request_id, helper_id):
    req = requests.get(request_id)

    if not req or req["user_id"] != user_id:
        return

    if helper_id in req["helpers"]:
        req["helpers"].remove(helper_id)
        helpers[helper_id]["available"] = True
        send_notification(helper_id, "You were cancelled by the user.")


# -----------------------------
# Privacy Control
# -----------------------------
def get_shared_info(user_id, request_id):
    user = users[user_id]
    req = requests[request_id]

    info = {}
    if user["shared_info"]["location"]:
        info["location"] = req["location"]
    if user["shared_info"]["destination"]:
        info["destination"] = req["destination"]
    if user["shared_info"]["time"]:
        info["time"] = req["time"]

    return info


# -----------------------------
# Wait Time Estimation
# -----------------------------
def estimate_wait_time(request_id):
    available_helpers = sum(1 for h in helpers.values() if h["available"])

    if available_helpers == 0:
        wait_time = "Approx. 10-15 minutes"
    else:
        wait_time = "Less than 5 minutes"

    user_id = requests[request_id]["user_id"]
    send_notification(user_id, f"Estimated wait time: {wait_time}")


# -----------------------------
# Example Simulation
# -----------------------------
if __name__ == "__main__":
    # Register users and helpers
    register_user("user1", "user1@example.com")
    register_helper("helper1")
    register_helper("helper2")

    # Create request
    req_id = create_request("user1", "Point A", "Point B", "12:00")

    # Helpers accept request
    helper_accept_request("helper1", req_id)
    helper_accept_request("helper2", req_id)

    # User cancels helper
    cancel_specific_helper("user1", req_id, "helper1")

    # Check shared info
    print("Shared info:", get_shared_info("user1", req_id))
