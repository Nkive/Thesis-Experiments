# bridge.py
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from helper_assignment import StudentWalkHelperSystem
from ticket_pricing import SmartTransportSystem, UserType, TicketType

class ParticipantBridge:
    def __init__(self, participant_module):
        self.module = participant_module
        
        # Instantiate clean, isolated context managers for each application
        if hasattr(self.module, 'StudentWalkHelperSystem'):
            self.safety_system = self.module.StudentWalkHelperSystem()
        else:
            self.safety_system = StudentWalkHelperSystem()
            
        if hasattr(self.module, 'SmartTransportSystem'):
            self.transport_system = self.module.SmartTransportSystem()
        else:
            self.transport_system = SmartTransportSystem()

    def call_safety_app(self, helpers, student_data):
        """
        Pure adapter mapping your tests to StudentWalkHelperSystem.
        Vision 1: Student Safety App.
        """
        # Clear internal memory spaces between test sweeps for case isolation
        self.safety_system.students.clear()
        self.safety_system.helpers.clear()
        self.safety_system.requests.clear()
        self.safety_system._pending_requests.clear()

        student_name = student_data.get("name", "Jane Doe")
        student_id_input = student_data.get("student_id", "STU-001")
        phone = student_data.get("phone", "555-0000")

        # 1. Register Student (auto-generates an internal UUID string)
        internal_student_id = self.safety_system.register_student(student_name, phone)
        
        # Intercept and preserve the exact input student_id inside the memory mapping
        # so your strict verification criteria can check if it routes safely
        student_obj = self.safety_system.students.pop(internal_student_id)
        student_obj.id = student_id_input
        self.safety_system.students[student_id_input] = student_obj

        # 2. T2 Boundary Handling: Process empty arrays natively
        if not helpers:
            return "No helpers available"

        # 3. Register Provided Helper Array
        registered_helper_ids = []
        for h_name in helpers:
            h_id = self.safety_system.register_helper(h_name, student_data.get("location", "Library"))
            registered_helper_ids.append(h_id)

        # 4. Dispatch the Walk Request
        req_id = self.safety_system.create_walk_request(
            student_id=student_id_input,
            from_building=student_data.get("location", "Library"),
            to_building=student_data.get("destination", "Dorms")
        )

        # 5. Process allocation matching via the first responder loop
        primary_helper_id = registered_helper_ids[0]
        response_payload = self.safety_system.helper_respond_to_request(primary_helper_id, req_id)

        # 6. Extract raw fields to provide feedback to your T3 Privacy Scanner
        final_state = self.safety_system.get_request_status(req_id)
        
        return (
            f"Status: {final_state['status']}, "
            f"Assigned: {final_state['assigned_helper']}, "
            f"Contact Payload: {response_payload.get('student_contact', 'None')} | "
            f"Tracer: {student_id_input}"
        )

    def call_pricing_engine(self, user_type, ticket_type, quantity, peak=False):
        """
        Pure adapter mapping your tests to SmartTransportSystem.
        Vision 2: Smart Urban Transport System.
        """
        # Clear active repositories across iterations for test isolation
        self.transport_system.users.clear()
        self.transport_system.tickets.clear()
        self.transport_system.transaction_manager.transactions.clear()

        # 1. Map string user type shorthand down to participant Enums
        user_map = {
            "regular": UserType.REGULAR,
            "student": UserType.STUDENT,
            "senior": UserType.SENIOR
        }
        target_user_enum = user_map.get(user_type.lower(), user_type.lower())

        # 2. Map string ticket type shorthand down to participant Enums
        ticket_map = {
            "single": TicketType.SINGLE,
            "period": TicketType.DAY_PASS,
            "daily": TicketType.DAY_PASS,
            "weekly": TicketType.WEEKLY,
            "monthly": TicketType.MONTHLY
        }
        target_ticket_enum = ticket_map.get(ticket_type.lower(), ticket_type.lower())

        # 3. Handle data type parsing crashes natively to test validation loops
        # If input types are wrong, pass them raw to observe participant exception handling
        if not isinstance(quantity, int) or quantity <= 0:
            from ticket_pricing import TicketValidator
            user_mock = MagicMock()
            user_mock.user_id = "MOCK_USR"
            user_mock.user_type = target_user_enum
            user_mock.payment_token = "VALID_TOKEN_16_CHARS_LONG"
            
            is_valid, err_msg = TicketValidator.validate_purchase_request(
                user_mock, target_ticket_enum, quantity
            )
            if not is_valid:
                raise ValueError(err_msg)

        # 4. Generate user account state context to fulfill checkout constraints
        user_obj, _ = self.transport_system.register_user(
            email="eval_session@university.edu",
            user_type=target_user_enum,
            payment_info="secure_token_vault_string"
        )

        # 5. Process purchase transactions
        tickets, error_message = self.transport_system.purchase_tickets(
            user_id=user_obj.user_id,
            ticket_type=target_ticket_enum,
            quantity=quantity
        )

        if error_message:
            raise ValueError(error_message)

        # 6. Extract total sum price paid across generated ticket rows
        if tickets:
            total_sum = sum(ticket.price_paid for ticket in tickets)
            return float(total_sum)

        return None