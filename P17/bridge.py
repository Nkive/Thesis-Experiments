# bridge.py
import sys
import uuid
from unittest.mock import MagicMock

# --- STEP 1: ISOLATE INFRASTRUCTURE INITIALIZATIONS ---
# Safely stub out any external container requirements during module loading
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()
if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = MagicMock()
if "sqlalchemy" not in sys.modules:
    sys.modules["sqlalchemy"] = MagicMock()

import transport_system
import safety_app
# --- STEP 2: MASTER TRANSLATOR CLASS ---
class ParticipantBridge:
    def __init__(self, participant_module):
        self.module = participant_module

    def call_safety_app(self, helpers, student_data):
        """
        Pure structural adapter for the functional state tracking inside helper_assignment.py.
        Vision 1: Student Safety App.
        """
        # Clear shared storage lists completely between runs for strict data isolation
        self.module.users.clear()
        self.module.volunteers.clear()
        self.module.escort_requests.clear()
        self.module.notifications.clear()

        student_name = student_data.get("name", "Jane")
        student_id = student_data.get("student_id", "MOCK_ID")
        email = student_data.get("email", "student@university.edu")

        # 1. Register Student Profile
        # We append the raw student_id directly to the name tracking key string 
        # so your T3 Privacy scanner can detect if it leaks down public tracking outputs.
        s_name = f"{student_name}_{student_id}" if "student_id" in student_data else student_name
        s_uid = self.module.register_student(s_name, email, "student_card.png")

        # 2. T2 Boundary Handling: Process empty arrays natively
        if not helpers:
            return "No helpers available"

        # 3. Register provided volunteers
        for h_name in helpers:
            self.module.register_volunteer(h_name, f"{h_name.lower()}@university.edu", "volunteer_card.png")

        # 4. Dispatch Escort Request Sequence
        target_uid = s_uid if s_uid else student_id
        req_id = self.module.create_escort_request(
            student_id=target_uid,
            destination=student_data.get("destination", "Dorms"),
            meeting_point=student_data.get("location", "Library")
        )

        # 5. Synchronously process exactly one cycle of the matching loop
        # Replicates their background worker queue threads safely for a synchronous unit test.
        request_obj = self.module.escort_requests.get(req_id)
        volunteer = self.module.select_best_volunteer()

        if volunteer and request_obj:
            request_obj.assigned_volunteer = volunteer.user_id
            request_obj.status = "WAITING_ACCEPTANCE"
            volunteer.active_requests += 1

        # Return a plain text block representation from the generated model
        final_state = self.module.escort_requests.get(req_id)
        return f"Status: {final_state.status}, Assigned ID: {final_state.assigned_volunteer}, Context Data: {final_state.student_id}"

    def call_pricing_engine(self, user_type, ticket_type, quantity, peak=False):
        """
        Adapts your unit tests to purchase_tickets inside ticket_pricing.py.
        Vision 2: Smart Urban Transport System.
        """
        # Generate a unique transaction tracking key for every single test execution
        transaction_id = f"TXN_{uuid.uuid4().hex[:6]}"

        # Standardize generic test keywords to match participant's internal database keys
        ticket_map = {
            "single": "single",
            "period": "daily",
            "daily": "daily",
            "weekly": "weekly"
        }
        mapped_ticket = ticket_map.get(ticket_type.lower(), ticket_type.lower())

        # Execute purchase endpoint using an authenticated, whitelisted payment method ("apple_pay")
        res = self.module.purchase_tickets(
            user_id="test_evaluation_user",
            ticket_type=mapped_ticket,
            quantity=quantity,
            payment_method="apple_pay",
            transaction_id=transaction_id
        )

        # Map functional dictionary return codes to pythonic exceptions for the test suite
        if isinstance(res, dict):
            if res.get("success") is True:
                return float(res["ticket"]["total_price"])
            else:
                error_msg = res.get("message", "")
                if "must be a whole number" in error_msg:
                    raise TypeError(error_msg)
                else:
                    raise ValueError(error_msg)

        return None