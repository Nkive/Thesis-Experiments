from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional
import uuid

app = FastAPI()

# ---------------------------
# CONFIG
# ---------------------------
MAX_HELPERS_PER_REQUEST = 2

# ---------------------------
# MODELS
# ---------------------------
class WalkRequest(BaseModel):
    id: str
    student_id: str
    start_location: str
    end_location: str
    time: datetime
    assigned_helpers: List[str] = []

class CreateRequest(BaseModel):
    student_id: str
    start_location: str
    end_location: str
    time: datetime

class Helper(BaseModel):
    id: str
    name: str
    is_available: bool = True
    current_assignment: Optional[str] = None

# ---------------------------
# IN-MEMORY DATABASE
# ---------------------------
walk_requests = {}
helpers = {}

# ---------------------------
# HELPER FUNCTIONS
# ---------------------------
def is_helper_available(helper: Helper, request_time: datetime):
    if not helper.is_available:
        return False

    # Prevent double booking: simple time buffer logic
    if helper.current_assignment:
        assigned_request = walk_requests.get(helper.current_assignment)
        if assigned_request:
            delta = abs((assigned_request.time - request_time).total_seconds())
            if delta < 3600:  # 1 hour buffer
                return False

    return True


def sanitize_request_for_helper(req: WalkRequest):
    # Only share minimal info
    return {
        "request_id": req.id,
        "start_area": req.start_location,  # could generalize to "Library Area"
        "end_area": req.end_location,      # not exact dorm room
        "time": req.time
    }

# ---------------------------
# API ENDPOINTS
# ---------------------------

@app.post("/request")
def create_request(data: CreateRequest):
    req_id = str(uuid.uuid4())
    new_request = WalkRequest(
        id=req_id,
        student_id=data.student_id,
        start_location=data.start_location,
        end_location=data.end_location,
        time=data.time
    )
    walk_requests[req_id] = new_request
    return {"message": "Request created", "request_id": req_id}


@app.get("/requests")
def view_requests():
    # Helpers only see sanitized info
    return [sanitize_request_for_helper(r) for r in walk_requests.values()]


@app.post("/helper/register")
def register_helper(name: str):
    helper_id = str(uuid.uuid4())
    helpers[helper_id] = Helper(id=helper_id, name=name)
    return {"helper_id": helper_id}


@app.post("/helper/accept")
def accept_request(helper_id: str, request_id: str):
    if helper_id not in helpers:
        raise HTTPException(status_code=404, detail="Helper not found")

    if request_id not in walk_requests:
        raise HTTPException(status_code=404, detail="Request not found")

    helper = helpers[helper_id]
    req = walk_requests[request_id]

    # Check if request already has enough helpers
    if len(req.assigned_helpers) >= MAX_HELPERS_PER_REQUEST:
        raise HTTPException(status_code=400, detail="Request already has enough helpers")

    # Check availability
    if not is_helper_available(helper, req.time):
        raise HTTPException(status_code=400, detail="Helper not available")

    # Assign helper
    req.assigned_helpers.append(helper_id)
    helper.current_assignment = request_id
    helper.is_available = False

    return {"message": "Helper assigned successfully"}


@app.post("/helper/complete")
def complete_request(helper_id: str):
    if helper_id not in helpers:
        raise HTTPException(status_code=404, detail="Helper not found")

    helper = helpers[helper_id]

    if not helper.current_assignment:
        raise HTTPException(status_code=400, detail="No active assignment")

    req = walk_requests.get(helper.current_assignment)

    if req:
        req.assigned_helpers.remove(helper_id)

    helper.current_assignment = None
    helper.is_available = True

    return {"message": "Request completed"}

