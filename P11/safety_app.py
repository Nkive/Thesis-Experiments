"""
SafeWalk - Campus Safety Companion App
A single-file async Python application connecting students who need walking
companions with volunteer helpers on campus.

Run with: python safewalk.py
Then open: http://localhost:8000
"""

import asyncio
import json
import uuid
import time
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from threading import Thread, Lock
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum
from queue import Queue
from collections import defaultdict


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------

class RequestStatus(str, Enum):
    PENDING   = "pending"
    MATCHED   = "matched"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class WalkRequest:
    id: str
    origin: str
    destination: str
    time_needed: str          # free-text, e.g. "now", "in 10 min"
    # Student identity kept private; only a display alias is shared
    student_alias: str
    status: RequestStatus = RequestStatus.PENDING
    helper_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def public_view(self) -> dict:
        """Return only the information helpers are allowed to see."""
        return {
            "id":           self.id,
            "origin":       self.origin,
            "destination":  self.destination,
            "time_needed":  self.time_needed,
            "student_alias": self.student_alias,
            "status":       self.status,
            "created_at":   self.created_at,
        }


@dataclass
class Helper:
    id: str
    display_name: str
    is_available: bool = True
    assigned_request: Optional[str] = None


@dataclass
class ChatMessage:
    request_id: str
    sender_role: str          # "student" | "helper"
    sender_alias: str
    text: str
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Application State  (in-memory, thread-safe)
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self):
        self._lock = Lock()
        self.requests: dict[str, WalkRequest] = {}
        self.helpers:  dict[str, Helper]       = {}
        # chat history keyed by request_id
        self.messages: dict[str, list[ChatMessage]] = defaultdict(list)
        # SSE subscriber queues keyed by a client token
        self._sse_subscribers: dict[str, Queue] = {}

    # -- Walk Requests -------------------------------------------------------

    def create_request(self, origin: str, destination: str,
                       time_needed: str, student_alias: str) -> WalkRequest:
        with self._lock:
            req = WalkRequest(
                id=str(uuid.uuid4())[:8],
                origin=origin,
                destination=destination,
                time_needed=time_needed,
                student_alias=student_alias,
            )
            self.requests[req.id] = req
        self._broadcast({"event": "request_created", "data": req.public_view()})
        return req

    def list_pending_requests(self) -> list[dict]:
        with self._lock:
            return [r.public_view() for r in self.requests.values()
                    if r.status == RequestStatus.PENDING]

    def get_request(self, req_id: str) -> Optional[WalkRequest]:
        with self._lock:
            return self.requests.get(req_id)

    def cancel_request(self, req_id: str) -> bool:
        with self._lock:
            req = self.requests.get(req_id)
            if not req or req.status not in (RequestStatus.PENDING, RequestStatus.MATCHED):
                return False
            if req.helper_id:
                helper = self.helpers.get(req.helper_id)
                if helper:
                    helper.is_available = True
                    helper.assigned_request = None
            req.status = RequestStatus.CANCELLED
        self._broadcast({"event": "request_updated", "data": req.public_view()})
        return True

    def complete_request(self, req_id: str) -> bool:
        with self._lock:
            req = self.requests.get(req_id)
            if not req or req.status != RequestStatus.MATCHED:
                return False
            helper = self.helpers.get(req.helper_id)
            if helper:
                helper.is_available = True
                helper.assigned_request = None
            req.status = RequestStatus.COMPLETED
        self._broadcast({"event": "request_updated", "data": req.public_view()})
        return True

    # -- Helpers -------------------------------------------------------------

    def register_helper(self, display_name: str) -> Helper:
        with self._lock:
            helper = Helper(
                id=str(uuid.uuid4())[:8],
                display_name=display_name,
            )
            self.helpers[helper.id] = helper
        return helper

    def respond_to_request(self, req_id: str, helper_id: str) -> tuple[bool, str]:
        """Assign helper to request.  Returns (success, reason)."""
        with self._lock:
            req = self.requests.get(req_id)
            if not req:
                return False, "Request not found."
            if req.status != RequestStatus.PENDING:
                return False, "Request is no longer available."
            helper = self.helpers.get(helper_id)
            if not helper:
                return False, "Helper not found."
            if not helper.is_available:
                return False, "You are already assigned to another request."
            # Assign
            req.status    = RequestStatus.MATCHED
            req.helper_id = helper_id
            helper.is_available      = False
            helper.assigned_request  = req_id
        self._broadcast({"event": "request_updated", "data": req.public_view()})
        return True, "Matched!"

    # -- Chat ----------------------------------------------------------------

    def post_message(self, req_id: str, sender_role: str,
                     sender_alias: str, text: str) -> Optional[ChatMessage]:
        with self._lock:
            req = self.requests.get(req_id)
            if not req or req.status not in (RequestStatus.PENDING, RequestStatus.MATCHED):
                return None
            msg = ChatMessage(req_id=req_id, sender_role=sender_role,
                              sender_alias=sender_alias, text=text)
            self.messages[req_id].append(msg)
        payload = {
            "event": "chat_message",
            "data": {
                "request_id":   msg.request_id,
                "sender_role":  msg.sender_role,
                "sender_alias": msg.sender_alias,
                "text":         msg.text,
                "timestamp":    msg.timestamp,
            }
        }
        self._broadcast(payload)
        return msg

    def get_messages(self, req_id: str) -> list[dict]:
        with self._lock:
            return [
                {
                    "request_id":   m.request_id,
                    "sender_role":  m.sender_role,
                    "sender_alias": m.sender_alias,
                    "text":         m.text,
                    "timestamp":    m.timestamp,
                }
                for m in self.messages[req_id]
            ]

    # -- SSE -----------------------------------------------------------------

    def subscribe(self) -> tuple[str, Queue]:
        token = str(uuid.uuid4())
        q: Queue = Queue(maxsize=200)
        with self._lock:
            self._sse_subscribers[token] = q
        return token, q

    def unsubscribe(self, token: str):
        with self._lock:
            self._sse_subscribers.pop(token, None)

    def _broadcast(self, payload: dict):
        msg = f"data: {json.dumps(payload)}\n\n"
        with self._lock:
            dead = []
            for token, q in self._sse_subscribers.items():
                try:
                    q.put_nowait(msg)
                except Exception:
                    dead.append(token)
            for t in dead:
                self._sse_subscribers.pop(t, None)


# ---------------------------------------------------------------------------
# Global state singleton
# ---------------------------------------------------------------------------

state = AppState()


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

def json_response(handler, code: int, data):
    body = json.dumps(data).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw)
    except Exception:
        return {}


class SafeWalkHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Suppress default access log noise
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/requests":
            json_response(self, 200, state.list_pending_requests())
        elif path.startswith("/api/requests/") and path.endswith("/messages"):
            req_id = path.split("/")[3]
            json_response(self, 200, state.get_messages(req_id))
        elif path == "/api/events":
            self._handle_sse()
        else:
            json_response(self, 404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        body   = read_body(self)

        # POST /api/requests  — student submits a walk request
        if path == "/api/requests":
            origin       = (body.get("origin") or "").strip()
            destination  = (body.get("destination") or "").strip()
            time_needed  = (body.get("time_needed") or "now").strip()
            alias        = (body.get("student_alias") or "Anonymous").strip()
            if not origin or not destination:
                return json_response(self, 400, {"error": "origin and destination required"})
            req = state.create_request(origin, destination, time_needed, alias)
            json_response(self, 201, req.public_view())

        # POST /api/requests/<id>/respond  — helper volunteers
        elif path.endswith("/respond"):
            parts  = path.split("/")
            req_id = parts[3] if len(parts) >= 4 else ""
            helper_id = (body.get("helper_id") or "").strip()
            if not helper_id:
                return json_response(self, 400, {"error": "helper_id required"})
            ok, reason = state.respond_to_request(req_id, helper_id)
            if ok:
                json_response(self, 200, {"message": reason})
            else:
                json_response(self, 409, {"error": reason})

        # POST /api/requests/<id>/complete
        elif path.endswith("/complete"):
            parts  = path.split("/")
            req_id = parts[3] if len(parts) >= 4 else ""
            ok = state.complete_request(req_id)
            if ok:
                json_response(self, 200, {"message": "Walk completed."})
            else:
                json_response(self, 400, {"error": "Cannot complete request."})

        # POST /api/requests/<id>/cancel
        elif path.endswith("/cancel"):
            parts  = path.split("/")
            req_id = parts[3] if len(parts) >= 4 else ""
            ok = state.cancel_request(req_id)
            if ok:
                json_response(self, 200, {"message": "Request cancelled."})
            else:
                json_response(self, 400, {"error": "Cannot cancel request."})

        # POST /api/requests/<id>/messages
        elif "/messages" in path and not path.endswith("/respond"):
            parts  = path.split("/")
            req_id = parts[3] if len(parts) >= 4 else ""
            role   = (body.get("sender_role") or "").strip()
            alias  = (body.get("sender_alias") or "Unknown").strip()
            text   = (body.get("text") or "").strip()
            if not role or not text:
                return json_response(self, 400, {"error": "sender_role and text required"})
            msg = state.post_message(req_id, role, alias, text)
            if msg:
                json_response(self, 201, {"message": "sent"})
            else:
                json_response(self, 400, {"error": "Cannot send message to this request."})

        # POST /api/helpers  — helper registers
        elif path == "/api/helpers":
            name = (body.get("display_name") or "").strip()
            if not name:
                return json_response(self, 400, {"error": "display_name required"})
            helper = state.register_helper(name)
            json_response(self, 201, {"id": helper.id, "display_name": helper.display_name})

        else:
            json_response(self, 404, {"error": "Not found"})

    def _handle_sse(self):
        """Server-Sent Events endpoint for real-time updates."""
        token, q = state.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            # Send a heartbeat immediately so client knows it's connected
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=20)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except Exception:
                    # Timeout: send keep-alive ping
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
        finally:
            state.unsubscribe(token)

    def _serve_html(self):
        html = build_html()
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# HTML / CSS / JS  (single-page application embedded as a string)
# ---------------------------------------------------------------------------

def build_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>SafeWalk — Campus Safety Companion</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&display=swap" rel="stylesheet"/>
<style>
  :root {
    --night: #0a0e1a;
    --deep: #111827;
    --panel: #161d2e;
    --border: #1f2d45;
    --muted: #263347;
    --text: #e8edf5;
    --sub:  #7a8ba8;
    --gold: #f5c842;
    --gold2:#e0a800;
    --teal: #38d9c0;
    --rose: #f06080;
    --blue: #4a90e8;
    --r: 12px;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; background: var(--night); color: var(--text);
    font-family: 'DM Sans', sans-serif; font-size: 15px; }

  /* Starfield background */
  body::before {
    content: '';
    position: fixed; inset: 0; z-index: 0;
    background-image:
      radial-gradient(1px 1px at 15% 25%, rgba(255,255,255,.55) 0%, transparent 100%),
      radial-gradient(1px 1px at 72% 11%, rgba(255,255,255,.4)  0%, transparent 100%),
      radial-gradient(1.5px 1.5px at 38% 60%, rgba(255,255,255,.35) 0%, transparent 100%),
      radial-gradient(1px 1px at 85% 80%, rgba(255,255,255,.5)  0%, transparent 100%),
      radial-gradient(1px 1px at 52% 44%, rgba(255,255,255,.3)  0%, transparent 100%),
      radial-gradient(1px 1px at 6%  90%, rgba(255,255,255,.45) 0%, transparent 100%),
      radial-gradient(1px 1px at 93% 38%, rgba(255,255,255,.3)  0%, transparent 100%),
      radial-gradient(1px 1px at 28% 85%, rgba(255,255,255,.4)  0%, transparent 100%),
      radial-gradient(2px 2px at 62% 72%, rgba(245,200,66,.25)  0%, transparent 100%),
      radial-gradient(1px 1px at 47% 5%,  rgba(255,255,255,.5)  0%, transparent 100%);
    pointer-events: none;
  }

  #app { position: relative; z-index: 1; min-height: 100vh; display: flex; flex-direction: column; }

  /* Header */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 32px;
    border-bottom: 1px solid var(--border);
    background: rgba(10,14,26,.85);
    backdrop-filter: blur(12px);
    position: sticky; top: 0; z-index: 100;
  }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo-icon {
    width: 40px; height: 40px; border-radius: 50%;
    background: linear-gradient(135deg, var(--gold), var(--teal));
    display: flex; align-items: center; justify-content: center;
    font-size: 20px;
  }
  .logo-text { font-family: 'DM Serif Display', serif; font-size: 22px; color: var(--gold); letter-spacing: .02em; }
  .logo-tag  { font-size: 11px; color: var(--sub); font-weight: 300; margin-left: 4px; }

  /* Mode tabs */
  .tabs { display: flex; gap: 6px; }
  .tab {
    padding: 8px 18px; border-radius: 999px; border: 1px solid var(--border);
    background: transparent; color: var(--sub); cursor: pointer;
    font-family: 'DM Sans', sans-serif; font-size: 13px; font-weight: 500;
    transition: all .2s;
  }
  .tab.active { background: var(--gold); border-color: var(--gold); color: var(--night); }
  .tab:hover:not(.active) { border-color: var(--gold); color: var(--gold); }

  /* Layout */
  main { flex: 1; display: grid; grid-template-columns: 360px 1fr; gap: 0; }

  /* Sidebar */
  .sidebar {
    border-right: 1px solid var(--border);
    background: rgba(17,24,39,.6);
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .sidebar-header {
    padding: 20px 24px 12px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
  }
  .sidebar-title { font-family: 'DM Serif Display', serif; font-size: 17px; color: var(--text); }
  .count-badge {
    background: var(--muted); border-radius: 999px;
    padding: 2px 10px; font-size: 12px; color: var(--sub); font-weight: 500;
  }
  .request-list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .request-list::-webkit-scrollbar { width: 4px; }
  .request-list::-webkit-scrollbar-track { background: transparent; }
  .request-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  /* Request card */
  .req-card {
    border: 1px solid var(--border); border-radius: var(--r);
    padding: 14px 16px; cursor: pointer; transition: all .2s;
    background: rgba(22,29,46,.6);
    animation: fadeIn .3s ease;
  }
  .req-card:hover { border-color: var(--gold); background: rgba(245,200,66,.04); }
  .req-card.selected { border-color: var(--teal); background: rgba(56,217,192,.05); }
  .req-card-top { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 8px; }
  .req-alias { font-weight: 600; font-size: 14px; color: var(--text); }
  .req-time  { font-size: 11px; color: var(--sub); }
  .req-route { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--sub); flex-wrap: wrap; }
  .req-route .loc { color: var(--text); font-weight: 500; }
  .req-route .arrow { color: var(--gold); flex-shrink: 0; }
  .req-when { margin-top: 8px; font-size: 12px; color: var(--teal); display: flex; align-items: center; gap: 5px; }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
  .empty-state {
    flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 10px; color: var(--sub); padding: 40px;
  }
  .empty-state .big { font-size: 40px; }
  .empty-state p { text-align: center; font-size: 13px; line-height: 1.6; }

  /* Main panel */
  .main-panel { display: flex; flex-direction: column; overflow: hidden; }

  /* Form section */
  .form-section {
    padding: 28px 32px;
    border-bottom: 1px solid var(--border);
    background: rgba(10,14,26,.4);
  }
  .section-title { font-family: 'DM Serif Display', serif; font-size: 19px; margin-bottom: 18px; color: var(--text); }
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .form-grid .full { grid-column: 1/-1; }
  label { display: block; font-size: 12px; color: var(--sub); margin-bottom: 5px; font-weight: 500; letter-spacing: .04em; text-transform: uppercase; }
  input, select {
    width: 100%; padding: 10px 14px;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text);
    font-family: 'DM Sans', sans-serif; font-size: 14px;
    transition: border-color .2s;
    outline: none;
  }
  input:focus, select:focus { border-color: var(--gold); }
  input::placeholder { color: var(--sub); }

  /* Buttons */
  .btn {
    padding: 10px 22px; border: none; border-radius: 8px; cursor: pointer;
    font-family: 'DM Sans', sans-serif; font-weight: 600; font-size: 14px;
    transition: all .2s; display: inline-flex; align-items: center; gap: 6px;
  }
  .btn-gold { background: var(--gold); color: var(--night); }
  .btn-gold:hover { background: var(--gold2); }
  .btn-teal { background: rgba(56,217,192,.15); color: var(--teal); border: 1px solid var(--teal); }
  .btn-teal:hover { background: rgba(56,217,192,.25); }
  .btn-rose { background: rgba(240,96,128,.15); color: var(--rose); border: 1px solid var(--rose); }
  .btn-rose:hover { background: rgba(240,96,128,.25); }
  .btn-ghost { background: transparent; color: var(--sub); border: 1px solid var(--border); }
  .btn-ghost:hover { border-color: var(--text); color: var(--text); }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }

  /* Detail panel */
  .detail-panel {
    flex: 1; display: flex; flex-direction: column;
    padding: 28px 32px;
    overflow: hidden;
  }
  .detail-empty {
    flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
    color: var(--sub); gap: 12px;
  }
  .detail-empty .big { font-size: 52px; }
  .detail-empty p { font-size: 14px; text-align: center; max-width: 280px; line-height: 1.6; }

  .detail-header { margin-bottom: 20px; }
  .detail-meta { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
  .chip {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 12px; border-radius: 999px; font-size: 12px; font-weight: 500;
  }
  .chip-gold { background: rgba(245,200,66,.12); color: var(--gold); border: 1px solid rgba(245,200,66,.3); }
  .chip-teal { background: rgba(56,217,192,.1);  color: var(--teal); border: 1px solid rgba(56,217,192,.25); }
  .chip-sub  { background: var(--muted); color: var(--sub); }

  /* Chat */
  .chat-box {
    flex: 1; border: 1px solid var(--border); border-radius: var(--r);
    display: flex; flex-direction: column; overflow: hidden;
    background: rgba(17,24,39,.5);
    min-height: 0;
  }
  .chat-messages {
    flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px;
  }
  .chat-messages::-webkit-scrollbar { width: 4px; }
  .chat-messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  .msg { display: flex; gap: 8px; animation: fadeIn .2s ease; }
  .msg.helper { flex-direction: row-reverse; }
  .msg-bubble {
    max-width: 70%; padding: 9px 14px; border-radius: 14px;
    font-size: 13px; line-height: 1.5;
  }
  .msg.student .msg-bubble { background: var(--muted); color: var(--text); border-bottom-left-radius: 4px; }
  .msg.helper  .msg-bubble { background: rgba(56,217,192,.15); color: var(--teal); border: 1px solid rgba(56,217,192,.2); border-bottom-right-radius: 4px; }
  .msg-alias { font-size: 11px; color: var(--sub); margin-bottom: 3px; }
  .msg.helper .msg-alias { text-align: right; }
  .msg-meta { display: flex; flex-direction: column; }
  .chat-input-row {
    padding: 12px 16px; border-top: 1px solid var(--border);
    display: flex; gap: 8px;
  }
  .chat-input-row input { flex: 1; }

  /* Helper registration inset */
  .helper-register {
    padding: 16px 24px; border-top: 1px solid var(--border);
    background: rgba(10,14,26,.5);
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  .helper-register label { display: inline; font-size: 13px; color: var(--sub); text-transform: none; letter-spacing: 0; margin: 0; white-space: nowrap; }
  .helper-register input { max-width: 220px; }
  .helper-badge {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 8px 16px; border-radius: 999px;
    background: rgba(56,217,192,.1); border: 1px solid rgba(56,217,192,.3);
    color: var(--teal); font-size: 13px; font-weight: 500;
  }

  /* Toast */
  #toast {
    position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%);
    padding: 10px 22px; border-radius: 999px;
    background: var(--deep); border: 1px solid var(--border);
    color: var(--text); font-size: 13px;
    opacity: 0; pointer-events: none;
    transition: opacity .3s;
    z-index: 9999;
    white-space: nowrap;
  }
  #toast.show { opacity: 1; }

  /* Status indicator */
  .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .status-live { background: var(--teal); box-shadow: 0 0 6px var(--teal); animation: pulse 2s infinite; }
  .status-off  { background: var(--rose); }

  @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.4} }
  @keyframes fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }

  /* Responsive */
  @media(max-width:760px){
    main { grid-template-columns: 1fr; }
    .sidebar { border-right: none; border-bottom: 1px solid var(--border); max-height: 45vh; }
    header { padding: 14px 16px; }
    .tabs .tab { padding: 7px 12px; font-size: 12px; }
  }
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="logo">
      <div class="logo-icon">🌙</div>
      <div>
        <span class="logo-text">SafeWalk</span>
        <span class="logo-tag">Campus Safety Companion</span>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:16px">
      <span id="liveStatus" style="font-size:12px;color:var(--sub)">
        <span class="status-dot status-off"></span>Connecting…
      </span>
      <div class="tabs">
        <button class="tab active" onclick="switchMode('student')">🎒 I Need a Walk</button>
        <button class="tab" onclick="switchMode('helper')">🤝 I'll Help</button>
      </div>
    </div>
  </header>

  <main>
    <!-- Sidebar: pending requests -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <span class="sidebar-title">Live Requests</span>
        <span class="count-badge" id="reqCount">0</span>
      </div>
      <div class="request-list" id="requestList">
        <div class="empty-state">
          <div class="big">🌙</div>
          <p>No pending walk requests.<br/>Be the first to ask for company!</p>
        </div>
      </div>
    </aside>

    <!-- Main panel -->
    <div class="main-panel">
      <!-- Student mode -->
      <div id="studentPanel">
        <div class="form-section">
          <div class="section-title">Request a Walking Companion</div>
          <div class="form-grid">
            <div>
              <label>Your Alias (shown to helper)</label>
              <input id="sAlias" placeholder="e.g. BlueSweater" maxlength="30"/>
            </div>
            <div>
              <label>When do you need to leave?</label>
              <input id="sTime" placeholder="e.g. Right now, In 10 min…" maxlength="40"/>
            </div>
            <div>
              <label>Starting From</label>
              <input id="sOrigin" placeholder="e.g. Main Library, East Gate…" maxlength="60"/>
            </div>
            <div>
              <label>Going To</label>
              <input id="sDest" placeholder="e.g. Maple Hall, Science Building…" maxlength="60"/>
            </div>
          </div>
          <div class="btn-row">
            <button class="btn btn-gold" onclick="submitRequest()">🚶 Request Walk</button>
          </div>
        </div>

        <!-- Student detail / chat -->
        <div class="detail-panel" id="studentDetailPanel">
          <div class="detail-empty" id="studentDetailEmpty">
            <div class="big">🔦</div>
            <p>Submit a request above, then select it from the list to chat with your helper.</p>
          </div>
          <div id="studentDetail" style="display:none;flex:1;flex-direction:column;overflow:hidden">
            <div class="detail-header">
              <div class="section-title" id="sdTitle">Your Walk Request</div>
              <div class="detail-meta" id="sdMeta"></div>
            </div>
            <div class="chat-box">
              <div class="chat-messages" id="sdChat"></div>
              <div class="chat-input-row">
                <input id="sdMsg" placeholder="Message your helper…" onkeydown="if(event.key==='Enter')sendMsg('student')"/>
                <button class="btn btn-teal" onclick="sendMsg('student')">Send</button>
              </div>
            </div>
            <div class="btn-row" style="margin-top:14px">
              <button class="btn btn-ghost" onclick="cancelCurrentRequest()">Cancel Request</button>
              <button class="btn btn-teal" onclick="completeCurrentRequest()">✅ Walk Complete</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Helper mode -->
      <div id="helperPanel" style="display:none;flex:1;flex-direction:column;overflow:hidden">
        <!-- Helper registration bar -->
        <div class="helper-register">
          <div id="helperUnreg" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <label>Register as a helper:</label>
            <input id="hName" placeholder="Your display name" maxlength="30" style="max-width:220px"
                   onkeydown="if(event.key==='Enter')registerHelper()"/>
            <button class="btn btn-teal" onclick="registerHelper()">Register</button>
          </div>
          <div id="helperReg" style="display:none">
            <span class="helper-badge">🤝 <span id="helperNameBadge"></span> — Ready to Help</span>
          </div>
        </div>

        <!-- Helper detail / chat -->
        <div class="detail-panel" id="helperDetailPanel">
          <div class="detail-empty" id="helperDetailEmpty">
            <div class="big">👀</div>
            <p>Select a request from the list to see details and volunteer as a companion.</p>
          </div>
          <div id="helperDetail" style="display:none;flex:1;flex-direction:column;overflow:hidden">
            <div class="detail-header">
              <div class="section-title" id="hdTitle">Walk Request</div>
              <div class="detail-meta" id="hdMeta"></div>
            </div>
            <div class="btn-row" id="hdBtns" style="margin-bottom:16px"></div>
            <div class="chat-box">
              <div class="chat-messages" id="hdChat"></div>
              <div class="chat-input-row" id="hdChatInput" style="display:none">
                <input id="hdMsg" placeholder="Message the student…" onkeydown="if(event.key==='Enter')sendMsg('helper')"/>
                <button class="btn btn-teal" onclick="sendMsg('helper')">Send</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>
</div>
<div id="toast"></div>

<script>
// ---- State ----------------------------------------------------------------
let mode = 'student';
let pendingRequests = [];
let selectedReqId = null;
let myRequestId   = null;   // student's own active request
let myHelperId    = null;
let myHelperName  = '';
let myHelperAssigned = null; // req_id helper is assigned to
let studentAlias  = '';

// ---- Mode switch ----------------------------------------------------------
function switchMode(m) {
  mode = m;
  document.querySelectorAll('.tab').forEach((t,i)=>{
    t.classList.toggle('active', (m==='student'&&i===0)||(m==='helper'&&i===1));
  });
  document.getElementById('studentPanel').style.display = m==='student' ? 'flex' : 'none';
  document.getElementById('studentPanel').style.flexDirection = 'column';
  document.getElementById('helperPanel').style.display  = m==='helper'  ? 'flex' : 'none';
  selectedReqId = null;
  renderRequestList();
}

// ---- Toast ----------------------------------------------------------------
let toastTimer;
function toast(msg, color='var(--teal)') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = color;
  el.style.color = color;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>el.classList.remove('show'), 2800);
}

// ---- Request list ---------------------------------------------------------
function renderRequestList() {
  const list = document.getElementById('requestList');
  const pending = pendingRequests.filter(r=>r.status==='pending'||r.status==='matched');
  document.getElementById('reqCount').textContent = pending.filter(r=>r.status==='pending').length;
  if(pending.length===0){
    list.innerHTML = `<div class="empty-state"><div class="big">🌙</div><p>No pending walk requests.<br/>Be the first to ask for company!</p></div>`;
    return;
  }
  list.innerHTML = pending.map(r=>{
    const ago = timeAgo(r.created_at*1000);
    const sel = r.id===selectedReqId ? 'selected' : '';
    return `<div class="req-card ${sel}" onclick="selectRequest('${r.id}')">
      <div class="req-card-top">
        <span class="req-alias">${esc(r.student_alias)}</span>
        <span class="req-time">${ago}</span>
      </div>
      <div class="req-route">
        <span class="loc">${esc(r.origin)}</span>
        <span class="arrow">→</span>
        <span class="loc">${esc(r.destination)}</span>
      </div>
      <div class="req-when"><span class="dot"></span>${esc(r.time_needed)}</div>
    </div>`;
  }).join('');
}

function timeAgo(ms) {
  const s = Math.floor((Date.now()-ms)/1000);
  if(s<60) return 'just now';
  if(s<3600) return Math.floor(s/60)+'m ago';
  return Math.floor(s/3600)+'h ago';
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ---- Select request -------------------------------------------------------
function selectRequest(id) {
  selectedReqId = id;
  renderRequestList();
  if(mode==='student') showStudentDetail(id);
  else showHelperDetail(id);
}

// ---- Student: submit request ----------------------------------------------
async function submitRequest() {
  if(myRequestId){ toast('You already have an active request. Cancel it first.','var(--rose)'); return; }
  const alias  = document.getElementById('sAlias').value.trim() || 'Anonymous';
  const time   = document.getElementById('sTime').value.trim()   || 'Now';
  const origin = document.getElementById('sOrigin').value.trim();
  const dest   = document.getElementById('sDest').value.trim();
  if(!origin||!dest){ toast('Please enter origin and destination.','var(--rose)'); return; }
  studentAlias = alias;
  const res = await fetch('/api/requests', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({origin,destination:dest,time_needed:time,student_alias:alias})
  });
  if(!res.ok){ toast('Failed to submit request.','var(--rose)'); return; }
  const req = await res.json();
  myRequestId = req.id;
  toast('Request submitted! Looking for helpers… 🌙');
  selectRequest(req.id);
}

// ---- Student: show detail -------------------------------------------------
function showStudentDetail(id) {
  const req = pendingRequests.find(r=>r.id===id);
  if(!req){ hideStudentDetail(); return; }
  document.getElementById('studentDetailEmpty').style.display='none';
  const d = document.getElementById('studentDetail');
  d.style.display='flex';
  document.getElementById('sdTitle').textContent = `Walk: ${req.origin} → ${req.destination}`;
  document.getElementById('sdMeta').innerHTML = `
    <span class="chip chip-gold">⏰ ${esc(req.time_needed)}</span>
    <span class="chip ${req.status==='matched'?'chip-teal':'chip-sub'}">${req.status==='matched'?'✅ Helper matched':'⏳ Awaiting helper'}</span>
    <span class="chip chip-sub">ID ${req.id}</span>`;
  loadMessages(id, 'sdChat', 'student');
}

function hideStudentDetail(){
  document.getElementById('studentDetailEmpty').style.display='flex';
  document.getElementById('studentDetail').style.display='none';
}

// ---- Student: cancel / complete ------------------------------------------
async function cancelCurrentRequest(){
  if(!myRequestId){ toast('No active request.','var(--rose)'); return; }
  await fetch(`/api/requests/${myRequestId}/cancel`,{method:'POST'});
  myRequestId=null; hideStudentDetail(); selectedReqId=null; toast('Request cancelled.');
}

async function completeCurrentRequest(){
  const id = selectedReqId || myRequestId;
  if(!id){ toast('No active request.','var(--rose)'); return; }
  await fetch(`/api/requests/${id}/complete`,{method:'POST'});
  if(id===myRequestId) myRequestId=null;
  hideStudentDetail(); selectedReqId=null;
  toast('Walk completed — stay safe! 🎉','var(--gold)');
}

// ---- Helper: register -----------------------------------------------------
async function registerHelper(){
  if(myHelperId){ toast('Already registered.'); return; }
  const name = document.getElementById('hName').value.trim();
  if(!name){ toast('Please enter a display name.','var(--rose)'); return; }
  const res = await fetch('/api/helpers',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({display_name:name})
  });
  if(!res.ok){ toast('Registration failed.','var(--rose)'); return; }
  const h = await res.json();
  myHelperId   = h.id;
  myHelperName = h.display_name;
  document.getElementById('helperUnreg').style.display='none';
  document.getElementById('helperReg').style.display='block';
  document.getElementById('helperNameBadge').textContent = myHelperName;
  toast(`Welcome, ${myHelperName}! You're ready to help.`,'var(--teal)');
}

// ---- Helper: show detail --------------------------------------------------
function showHelperDetail(id){
  const req = pendingRequests.find(r=>r.id===id);
  if(!req){ hideHelperDetail(); return; }
  document.getElementById('helperDetailEmpty').style.display='none';
  const d = document.getElementById('helperDetail');
  d.style.display='flex';
  document.getElementById('hdTitle').textContent = `Walk: ${req.origin} → ${req.destination}`;
  document.getElementById('hdMeta').innerHTML = `
    <span class="chip chip-gold">⏰ ${esc(req.time_needed)}</span>
    <span class="chip chip-sub">👤 ${esc(req.student_alias)}</span>
    <span class="chip ${req.status==='matched'?'chip-teal':'chip-sub'}">${req.status}</span>`;

  const btns = document.getElementById('hdBtns');
  const isMyAssigned = myHelperAssigned===id;
  if(req.status==='pending'){
    btns.innerHTML = `<button class="btn btn-gold" onclick="volunteerForRequest('${id}')">🤝 Volunteer to Help</button>`;
  } else if(req.status==='matched' && isMyAssigned){
    btns.innerHTML = `<button class="btn btn-teal" onclick="helperComplete('${id}')">✅ Walk Complete</button>`;
  } else {
    btns.innerHTML = '';
  }

  const chatInput = document.getElementById('hdChatInput');
  chatInput.style.display = isMyAssigned ? 'flex' : 'none';
  loadMessages(id, 'hdChat', 'helper');
}

function hideHelperDetail(){
  document.getElementById('helperDetailEmpty').style.display='flex';
  document.getElementById('helperDetail').style.display='none';
}

// ---- Helper: volunteer ----------------------------------------------------
async function volunteerForRequest(id){
  if(!myHelperId){ toast('Please register as a helper first.','var(--rose)'); return; }
  const res = await fetch(`/api/requests/${id}/respond`,{
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({helper_id:myHelperId})
  });
  const data = await res.json();
  if(!res.ok){ toast(data.error||'Could not volunteer.','var(--rose)'); return; }
  myHelperAssigned = id;
  toast('Matched! Head to the meeting point and message the student.','var(--teal)');
  showHelperDetail(id);
}

async function helperComplete(id){
  await fetch(`/api/requests/${id}/complete`,{method:'POST'});
  myHelperAssigned=null; hideHelperDetail(); selectedReqId=null;
  toast('Walk marked complete — great job! 🌟','var(--gold)');
}

// ---- Chat -----------------------------------------------------------------
async function loadMessages(reqId, containerId, myRole){
  const res = await fetch(`/api/requests/${reqId}/messages`);
  const msgs = await res.json();
  const c = document.getElementById(containerId);
  c.innerHTML='';
  msgs.forEach(m=>appendMsg(c,m,myRole));
  c.scrollTop=c.scrollHeight;
}

function appendMsg(container, m, myRole){
  const isMe = m.sender_role===myRole;
  const div  = document.createElement('div');
  div.className = 'msg ' + m.sender_role;
  div.innerHTML = `<div class="msg-meta">
    <div class="msg-alias">${esc(m.sender_alias)}</div>
    <div class="msg-bubble">${esc(m.text)}</div>
  </div>`;
  container.appendChild(div);
}

async function sendMsg(role){
  const inputId = role==='student' ? 'sdMsg' : 'hdMsg';
  const chatId  = role==='student' ? 'sdChat' : 'hdChat';
  const inp = document.getElementById(inputId);
  const text = inp.value.trim();
  if(!text) return;
  const alias = role==='student' ? (studentAlias||'Student') : myHelperName;
  inp.value='';
  await fetch(`/api/requests/${selectedReqId}/messages`,{
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sender_role:role, sender_alias:alias, text})
  });
}

// ---- SSE: real-time updates -----------------------------------------------
function connectSSE(){
  const es = new EventSource('/api/events');
  const statusEl = document.getElementById('liveStatus');

  es.onopen = ()=>{
    statusEl.innerHTML = '<span class="status-dot status-live"></span>Live';
  };
  es.onerror = ()=>{
    statusEl.innerHTML = '<span class="status-dot status-off"></span>Reconnecting…';
    setTimeout(connectSSE, 3000);
    es.close();
  };
  es.onmessage = e=>{
    const payload = JSON.parse(e.data);
    if(payload.event==='request_created'){
      pendingRequests.push(payload.data);
      renderRequestList();
    } else if(payload.event==='request_updated'){
      const idx = pendingRequests.findIndex(r=>r.id===payload.data.id);
      if(idx>=0) pendingRequests[idx]=payload.data;
      else pendingRequests.push(payload.data);
      renderRequestList();
      // refresh detail panels if selected
      if(selectedReqId===payload.data.id){
        if(mode==='student') showStudentDetail(selectedReqId);
        else showHelperDetail(selectedReqId);
      }
    } else if(payload.event==='chat_message'){
      const m = payload.data;
      if(selectedReqId===m.request_id){
        const chatId = mode==='student'?'sdChat':'hdChat';
        const c = document.getElementById(chatId);
        if(c){
          appendMsg(c, m, mode);
          c.scrollTop=c.scrollHeight;
        }
      }
    }
  };
}

// ---- Bootstrap ------------------------------------------------------------
async function loadRequests(){
  const res = await fetch('/api/requests');
  pendingRequests = await res.json();
  renderRequestList();
}

loadRequests();
connectSSE();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def run(host: str = "0.0.0.0", port: int = 8000):
    server = HTTPServer((host, port), SafeWalkHandler)
    # Allow multiple threads so SSE connections don't block regular requests
    server.socket.setsockopt(1, 15, 1)   # SO_REUSEPORT where available

    print(f"""
╔══════════════════════════════════════════╗
║   🌙  SafeWalk — Campus Safety App       ║
╠══════════════════════════════════════════╣
║  Open: http://localhost:{port}             ║
║  Press Ctrl+C to stop                   ║
╚══════════════════════════════════════════╝
""")

    # Use a thread-pool approach: spawn a daemon thread per connection via
    # a simple thread-per-request wrapper so SSE clients don't block others.
    import socketserver

    class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True

    threaded = ThreadedHTTPServer((host, port), SafeWalkHandler)
    try:
        threaded.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down SafeWalk. Stay safe!")


if __name__ == "__main__":
    run()
