# bridge.py
from decimal import Decimal, ROUND_HALF_UP

class ParticipantBridge:
    def __init__(self, participant_module):
        self.module = participant_module

    def call_safety_app(self, helpers, student_data):
        """
        Pure adapter for the AssistanceSystem backend.
        No forced validation guards.
        """
        # Instantiate a clean state using the participant's exact class
        app = self.module.AssistanceSystem(secret="test-secret")
        
        student_username = student_data.get("name", "Jane").lower() + "_student"
        helper_username = helpers[0].lower() + "_helper" if helpers else "nobody_helper"
        
        # Authenticate and execute using the participant's exact flow
        try:
            app.register(student_username, "password123", "requester")
            student_token = app.login(student_username, "password123")["token"]
            
            if not helpers:
                # Let the code naturally execute or crash if it cannot handle empty inputs
                return app.list_requests(student_token)
                
            app.register(helper_username, "securepass1", "helper")
            helper_token = app.login(helper_username, "securepass1")["token"]
            
            req = app.submit_request(
                student_token, 
                student_data.get("location", "Library"), 
                student_data.get("destination", "Dorms"), 
                "22:00"
            )
            
            assignment = app.accept_request(helper_token, req["request_id"])
            
            # Return the direct result of the participant's data structures 
            # to see if student_id or raw encrypted values leak into the test scanner
            return f"Assigned {helpers[0]}. Context details: {assignment}"
        except Exception as e:
            raise e

    def call_pricing_engine(self, user_type, ticket_type, quantity, peak=False):
        """
        Pure adapter for the TicketingService.
        Passes inputs straight down to evaluate actual logic, baseline values, and data types.
        """
        # Map standard strings to the exact lower-case strings required by their internal Enums
        user_mapping = {"regular": "adult", "student": "student", "senior": "senior"}
        ticket_mapping = {"single": "single_ride", "period": "period_30_day"}
        
        mapped_user = user_mapping.get(user_type.lower(), user_type)
        mapped_ticket = ticket_mapping.get(ticket_type.lower(), ticket_type)
        
        # Build the exact dictionary structure expected by their ingest pipeline
        payload = {
            "passenger_category": mapped_user,
            "items": [
                {
                    "ticket_type": mapped_ticket,
                    "quantity": quantity
                }
            ]
        }
        
        service = self.module.TicketingService()
        
        # Execute their purchase method directly. 
        # No math scaling, no catching specific error classes to transform them.
        quote = service.purchase(payload)
        
        # Return the raw numerical value as a float for assert comparison
        return float(quote.subtotal)
