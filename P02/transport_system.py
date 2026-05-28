from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from enum import Enum
from typing import List, Dict
import hashlib

app = FastAPI()

# -----------------------------
# ENUMS
# -----------------------------

class UserType(str, Enum):
    student = "student"
    senior = "senior"
    regular = "regular"

class TicketType(str, Enum):
    single = "single"
    period = "period"
    bundle = "bundle"

class Platform(str, Enum):
    mobile = "mobile"
    web = "web"

# -----------------------------
# DATABASE
# -----------------------------

users_db: Dict[str, dict] = {}

# -----------------------------
# MODELS
# -----------------------------

class RegisterRequest(BaseModel):
    user_id: str
    password: str
    user_type: UserType

class User(BaseModel):
    user_id: str
    user_type: UserType

class TicketRequest(BaseModel):
    ticket_type: TicketType
    quantity: int

class OrderRequest(BaseModel):
    user: User
    platform: Platform
    tickets: List[TicketRequest]

# -----------------------------
# PRICING ENGINE (UNCHANGED)
# -----------------------------

class PricingEngine:
    BASE_PRICES = {
        TicketType.single: 30,
        TicketType.period: 100,
        TicketType.bundle: 200,
    }

    TYPE_MULTIPLIER = {
        TicketType.single: 1.0,
        TicketType.period: 0.9,
        TicketType.bundle: 0.8,
    }

    USER_DISCOUNT = {
        UserType.student: 0.85,
        UserType.senior: 0.80,
        UserType.regular: 1.0,
    }

    @classmethod
    def calculate_total(cls, user: User, tickets: List[TicketRequest]) -> float:
        total = 0.0

        for item in tickets:
            if item.quantity <= 0:
                raise HTTPException(status_code=400, detail="Invalid quantity")

            base_price = cls.BASE_PRICES[item.ticket_type]
            multiplier = cls.TYPE_MULTIPLIER[item.ticket_type]
            discount = cls.USER_DISCOUNT[user.user_type]

            price = base_price * multiplier * discount
            total += price * item.quantity

        return round(total, 2)

# -----------------------------
# AUTH + ROUTES (UNCHANGED)
# -----------------------------

users_db = {}

@app.post("/register")
def register(req: RegisterRequest):
    if req.user_id in users_db:
        raise HTTPException(status_code=400, detail="User already exists")

    users_db[req.user_id] = {
        "user_type": req.user_type,
        "password": hashlib.sha256(req.password.encode()).hexdigest()
    }

    return {"status": "account_created"}

@app.post("/calculate")
def calculate(order: OrderRequest):
    total = PricingEngine.calculate_total(order.user, order.tickets)
    return {"total_price": total}

@app.post("/purchase")
def purchase(order: OrderRequest):
    if not order.tickets:
        raise HTTPException(status_code=400, detail="No tickets selected")

    if order.user.user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")

    total = PricingEngine.calculate_total(order.user, order.tickets)

    return {
        "status": "success",
        "total_price": total,
        "platform": order.platform,
        "tickets_count": sum(t.quantity for t in order.tickets),
        "payment": "processed_securely"
    }

@app.get("/")
def root():
    return {"message": "Ticketing API running"}