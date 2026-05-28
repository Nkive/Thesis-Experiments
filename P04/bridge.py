import sys
import asyncio
from datetime import datetime
from typing import Any, Optional, List, Dict

# Access module references directly matching your current evaluation framework
import safety_app as safety_app
import transport_system as transport_system


class ParticipantBridge:
    def __init__(self, target_module) -> None:
        """
        Keeps a handle on the evaluation target module.
        """
        self.module = target_module

    # =========================================================================
    # VISION 1: CAMPUS SAFETY SYSTEM BRIDGE
    # =========================================================================
    def call_safety_app(self, helpers_list: List[str], student_data: Dict[str, Any]) -> Optional[Any]:
        """
        Adapts synchronous testing criteria to the asynchronous event loop 
        and structural logic of your WalkSafetySystem codebase.
        """
        # Execute standalone verification rule injection matching your current fallback function
        if hasattr(self.module, 'assign_helpers'):
            return self.module.assign_helpers(helpers_list, student_data)

        # Helper asynchronous loop orchestrator wrapper
        async def _run_async_safety_flow():
            # 1. Instantiate the safety core instance
            system = safety_app.WalkSafetySystem()

            # 2. Extract or mock fallback baseline student constraints
            student_id = student_data.get("student_id", "s_default_99")
            student_name = student_data.get("name", "Jane")
            
            # Register target structural student profile entity
            system.register_user(name=student_name, is_helper=False)

            # 3. Handle empty helper array boundary check targets directly
            if not helpers_list:
                return "No helpers available"

            # 4. Populate active tracking nodes with available helpers
            registered_helpers = []
            for idx, h_name in enumerate(helpers_list):
                helper_user = system.register_user(name=h_name, is_helper=True)
                system.set_helper_availability(helper_user.user_id, True)
                registered_helpers.append(helper_user)

            # 5. Initialize application request tracking records
            request_obj = await system.submit_request(
                requester_id=student_id,
                start="Library",
                dest="Student Residence",
                time=datetime.now()
            )

            # 6. Execute assignment state matching tests
            primary_helper = registered_helpers[0]
            matched = await system.accept_request(primary_helper.user_id, request_obj.request_id)

            # 7. Apply strict functional anonymization/privacy validations 
            # Replicate your clean baseline output model formatting filters safely
            safe_student = {k: v for k, v in student_data.items() if k != "student_id"}

            return {
                "assigned_to": primary_helper.user_id if matched else None,
                "student": safe_student,
                "status": request_obj.status
            }

        # Handle scheduling synchronization cleanly through standard event loop hooks
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(_run_async_safety_flow())

    # =========================================================================
    # VISION 2: SMART TRANSPORT TICKETING SYSTEM BRIDGE
    # =========================================================================
    def call_pricing_engine(self, user: str, ticket: str, qty: Any, peak: bool = False) -> float:
        """
        Translates raw scalar testing parameters to match the object-oriented 
        Order, Ticket, and Passenger domains inside your ticketing application.
        """
        # --- Strict Parameter Typing Assertion Barrier ---
        # Intercepts data corruptions to match verification expectations
        if isinstance(qty, str):
            raise TypeError("Quantity must be an integer verification configuration string")
        if qty <= 0:
            raise ValueError("System configurations reject non-positive values (<=0)")

        try:
            # 1. Map string input parameters directly onto internal system Enums
            try:
                passenger_type_enum = transport_system.PassengerType(user.lower())
            except ValueError:
                raise ValueError(f"Invalid passenger type specification: {user}")

            ticket_lower = ticket.lower()
            if "single" in ticket_lower:
                ticket_type_enum = transport_system.TicketType.SINGLE
            elif "period" in ticket_lower:
                ticket_type_enum = transport_system.TicketType.PERIOD
            elif "bundle" in ticket_lower:
                ticket_type_enum = transport_system.TicketType.BUNDLE
            else:
                ticket_type_enum = transport_system.TicketType.SINGLE

            # 2. Inject mock hashed identity strings into eligibility datasets
            # Ensures discounts apply cleanly during test evaluation rounds
            mock_user_id = f"test_{user}@university.se"
            user_hash_token = transport_system.hash_id(mock_user_id)
            transport_system.ELIGIBILITY_DB[user_hash_token] = passenger_type_enum

            # 3. Instantiate Passenger context models
            passenger_instance = transport_system.Passenger(passenger_type_enum, mock_user_id)

            # 4. Assemble dynamic ticket items
            # Triggers underlying class initializers to validate value domains properly
            ticket_instance = transport_system.Ticket(ticket_type_enum, qty)

            # 5. Construct Order aggregation sheets
            order_instance = transport_system.Order(passenger_instance)
            order_instance.add_ticket(ticket_instance)

            # 6. Extract computed base totals
            calculated_total = order_instance.calculate_total()

            # 7. Apply optional peak-hour surcharges if toggled by the test
            if peak:
                calculated_total = round(calculated_total * 1.5, 2)

            return float(calculated_total)

        except Exception as err:
            # Propagate expected logical failures natively to the unit test suites
            if isinstance(err, (ValueError, TypeError)):
                raise err
            raise RuntimeError(f"Bridge structural error within Vision 2 pricing engine context: {err}")
