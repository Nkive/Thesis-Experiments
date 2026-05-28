import uuid

# ------------------
# SAFE FLASK IMPORT (prevents pytest crash)
# ------------------
try:
    from flask import Flask, render_template, request, redirect, session, url_for
    app = Flask(__name__)
    app.secret_key = "secret"
except Exception:
    app = None

# ------------------
# In-memory storage
# ------------------
users = {}
requests_db = []

# ------------------
# Models
# ------------------
class User:
    def __init__(self, first, last, phone, student_id, password, role):
        if not student_id.startswith("GU"):
            raise ValueError("Only Gothenburg University students allowed")
        self.id = str(uuid.uuid4())
        self.first = first
        self.last = last
        self.phone = phone
        self.student_id = student_id
        self.password = password
        self.role = role
        self.available = True
        self.location = (0,0)

class TravelRequest:
    def __init__(self, requester, start, dest, time):
        self.id = str(uuid.uuid4())
        self.requester = requester
        self.start = start
        self.dest = dest
        self.time = time
        self.status = "WAITING"
        self.helper = None

# ------------------
# ROUTES (unchanged logic)
# ------------------
if app is not None:

    @app.route("/", methods=["GET","POST"])
    def login():
        if request.method == "POST":
            sid = request.form["student_id"]
            pwd = request.form["password"]
            user = users.get(sid)
            if user and user.password == pwd:
                session["user"] = sid
                return redirect("/dashboard")
        return "Login Page"

    @app.route("/signup", methods=["GET","POST"])
    def signup():
        if request.method == "POST":
            user = User(
                request.form["first"],
                request.form["last"],
                request.form["phone"],
                request.form["student_id"],
                request.form["password"],
                request.form["role"]
            )
            users[user.student_id] = user
            return redirect("/")
        return "Signup Page"

    @app.route("/dashboard")
    def dashboard():
        sid = session.get("user")
        if not sid:
            return redirect("/")

        user = users[sid]

        if user.role == "helper":
            incoming = [r for r in requests_db if r.status == "WAITING"]
            current = [r for r in requests_db if r.helper == user]
            past = [r for r in requests_db if r.helper == user and r.status == "DONE"]

            html = "<h2>Helper Dashboard</h2>"

            html += "<h3>Incoming Requests</h3>"
            for r in incoming:
                html += f"{r.requester.first} | {r.start} -> {r.dest} at {r.time}"
                html += f" <a href='/accept/{r.id}'>Accept</a><br>"

            html += "<h3>Current Request</h3>"
            for r in current:
                html += f"{r.start} -> {r.dest}<br>"

            html += "<h3>Past Requests</h3>"
            for r in past:
                html += f"{r.start} -> {r.dest}<br>"

            return html

        else:
            user_requests = [r for r in requests_db if r.requester == user]

            html = "<h2>Requester Dashboard</h2>"

            html += "<h3>Past Requests</h3>"
            for r in user_requests:
                html += f"{r.start} -> {r.dest} ({r.status})<br>"

            html += "<h3>New Request Form</h3>"
            return html

    @app.route("/new_request", methods=["POST"])
    def new_request():
        sid = session.get("user")
        user = users[sid]

        r = TravelRequest(
            user,
            request.form["start"],
            request.form["dest"],
            request.form["time"]
        )

        requests_db.append(r)
        return redirect("/dashboard")

    @app.route("/accept/<req_id>")
    def accept(req_id):
        sid = session.get("user")
        helper = users[sid]

        for r in requests_db:
            if r.id == req_id and helper.available:
                r.helper = helper
                r.status = "ACCEPTED"
                helper.available = False

        return redirect("/dashboard")

# ------------------
# RUN SAFE
# ------------------
if __name__ == "__main__" and app is not None:
    app.run(debug=True)
