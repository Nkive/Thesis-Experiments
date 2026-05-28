# Public Transport Ticketing App (Python - Flask)

# ------------------
# SAFE FLASK IMPORT (prevents pytest crash)
# ------------------
try:
    from flask import Flask, request, jsonify, session
    from flask import render_template_string
    app = Flask(__name__)
    app.secret_key = "super_secret_key"
except Exception:
    app = None

import hashlib

# In-memory storage (for demo only)
users = {}
logs = []

discounts = {
    "student": 20,
    "senior": 30,
    "regular": 0
}

prices = {
    "single": 2,
    "period": 50,
    "bundle": 15
}

# ---------- Helpers ----------
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def log(msg):
    logs.append(msg)

# ---------- ROUTES ONLY IF FLASK EXISTS ----------
if app is not None:

    @app.route("/")
    def home():
        return "Home"

    @app.route("/signup", methods=["POST"])
    def signup():
        u = request.form["username"]
        p = hash_pw(request.form["password"])
        role = request.form["role"]
        user_type = request.form.get("user_type", "regular")

        users[u] = {
            "password": p,
            "role": role,
            "user_type": user_type
        }

        log(f"SIGNUP: {u}")
        return "Signed up"

    @app.route("/login", methods=["POST"])
    def login():
        u = request.form["username"]
        p = hash_pw(request.form["password"])

        if u in users and users[u]["password"] == p:
            session["user"] = u
            return "Logged in"

        return "Invalid login"

    @app.route("/buy", methods=["POST"])
    def buy():
        if "user" not in session:
            return "Login required"

        u = session["user"]
        user = users[u]

        ticket = request.form["ticket"]
        qty = int(request.form["qty"])

        card = request.form["card"]
        cvv = request.form["cvv"]

        if len(card) < 12 or len(cvv) < 3:
            return "Invalid payment info"

        base = prices[ticket] * qty
        discount = discounts[user["user_type"]]
        total = base * (1 - discount/100)

        log(f"PURCHASE: {u}")
        return f"Paid {total}"

    @app.route("/set_discount", methods=["POST"])
    def set_discount():
        if "user" not in session:
            return "Login required"

        u = session["user"]
        if users[u]["role"] != "admin":
            return "Not authorized"

        discounts["student"] = int(request.form["student"])
        discounts["senior"] = int(request.form["senior"])
        discounts["regular"] = int(request.form["regular"])

        return "Updated"

# ------------------
# RUN SAFE
# ------------------
if __name__ == "__main__" and app is not None:
    app.run(debug=True)

def calculate_price(ticket_type, user_type, qty):
    base = prices[ticket_type] * qty
    discount = discounts[user_type]
    return base * (1 - discount / 100)

