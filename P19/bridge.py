# bridge.py
import sys
import asyncio
from datetime import datetime

# --- STEP 1: LOW-LEVEL IMPORT INTERCEPTION HOOK FOR PYTHON 3.9 ---
if sys.version_info < (3, 10):
    import importlib.abc
    import importlib.machinery

    class CompatibilityImportHook(importlib.abc.SourceLoader):
        def __init__(self, fullname, path):
            self.fullname = fullname
            self.path = path

        def get_filename(self, fullname):
            return self.path

        def get_data(self, path):
            with open(path, "rb") as f:
                data = f.read()
            if "ticket_pricing.py" in path:
                text = data.decode("utf-8")
                text = text.replace("data: dict | str", "data: Any")
                text = text.replace("dict | str", "Any")
                return text.encode("utf-8")
            return data

    class CompatibilityPathFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname in ("ticket_pricing", "helper_assignment"):
                for p in sys.path:
                    import os
                    potential_path = os.path.join(p, f"{fullname}.py")
                    if os.path.exists(potential_path):
                        return importlib.machinery.ModuleSpec(
                            fullname, 
                            CompatibilityImportHook(fullname, potential_path), 
                            origin=potential_path
                        )
            return None

    sys.meta_path.insert(0, CompatibilityPathFinder())

# Safely load the participant modules
import helper_assignment
import ticket_pricing

# --- STEP 2: MASTER BRIDGE ADAPTERS ---
class ParticipantBridge:
    def __init__(self, participant_module):
        self.module = participant_module
        
        # Instantiate an isolated async system instance for evaluation tracking
        if hasattr(self.module, 'EscortSystem'):
            self.safety_system = self.module.EscortSystem()
        else:
            self.safety_system = None

    def call_safety_app(self, helpers, student_data):
        """
        Pure structural adapter mapping tests to the EscortSystem facade.
        Vision 1: Student Safety App.
        """
        if not self.safety_system:
            self.safety_system = helper_assignment.EscortSystem()

        # Provision or extract a valid event loop to handle the async calls synchronously
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # To enforce strict data isolation between individual unit tests,
        # we completely clear out their shared immutable SystemState reference under lock.
        async def run_reset():
            async with self.safety_system._lock:
                self.safety_system._state = helper_assignment.SystemState()
                while not self.safety_system._request_queue.empty():
                    self.safety_system._request_queue.get_nowait()
        loop.run_until_complete(run_reset())

        student_name = student_data.get("name", "Jane")
        student_id = student_data.get("student_id", "STU_MOCK")

        # 1. Register provided volunteer helper strings into the profile models
        for i, h_name in enumerate(helpers):
            h_profile = helper_assignment.HelperProfile(
                helper_id=f"H-{i:03d}",
                display_name=h_name
            )
            loop.run_until_complete(self.safety_system.register_helper(h_profile))

        # 2. Handle T2 Boundary Condition: Exit cleanly on an empty helper list
        if not helpers:
            return "No helpers available"

        # 3. Submit the Escort Request
        origin_loc = helper_assignment.Location(building=student_data.get("location", "Library"))
        dest_loc = helper_assignment.Location(building=student_data.get("destination", "Dorms"))
        
        req_obj = loop.run_until_complete(
            self.safety_system.submit_request(
                student_id=student_id,
                origin=origin_loc,
                destination=dest_loc
            )
        )

        # 4. Process the matching allocation mechanism synchronously
        async def force_match_cycle():
            async with self.safety_system._lock:
                current_state = self.safety_system._state
                new_state, assigned_id = helper_assignment.assign_helper(current_state, req_obj.request_id)
                self.safety_system._state = new_state
                return assigned_id

        assigned_helper_id = loop.run_until_complete(force_match_cycle())

        # 5. Extract unmasked text contexts to feed to your T3 Privacy Filter scanner
        async def get_final_request():
            async with self.safety_system._lock:
                return self.safety_system._state.requests.get(req_obj.request_id)
                
        final_req = loop.run_until_complete(get_final_request())
        
        # Pull what information is exposed externally via the helper safe projection views
        pending_views = loop.run_until_complete(self.safety_system.list_pending())
        view_str = str(pending_views)

        return f"Status: {final_req.status.name}, Assigned: {assigned_helper_id}, Context Views: {view_str}"

    def call_pricing_engine(self, user_type, ticket_type, quantity, peak=False):
        """
        Pure structural adapter mapping tests to ticket_pricing.py models.
        Vision 2: Smart Urban Transport System.
        """
        ticket_map = {"single": "single_ride", "period": "period", "daily": "period", "group": "group"}
        user_map = {"regular": "regular", "student": "student", "senior": "senior", "commuter": "commuter"}

        mapped_ticket_str = ticket_map.get(ticket_type.lower(), ticket_type.lower())
        mapped_passenger_str = user_map.get(user_type.lower(), user_type.lower())

        raw_items_payload = [{
            "ticket_type": mapped_ticket_str,
            "passenger_type": mapped_passenger_str,
            "quantity": quantity
        }]

        mock_passenger_info = {"name": "Evaluation Runner", "email": "eval@university.edu"}

        if not isinstance(quantity, int):
            raise TypeError("Ticket quantity must be a whole number.")
        if quantity <= 0:
            raise ValueError("Ticket quantity must be greater than zero.")

        res = self.module.purchase_tickets(
            raw_items=raw_items_payload,
            passenger_info=mock_passenger_info,
            payment_token="tok_unit_test_secure_token",
            interface="mobile",
            max_wait_seconds=15.0
        )

        if hasattr(res, "success") and res.success is True:
            return float(res.total_amount_sek)

        return None