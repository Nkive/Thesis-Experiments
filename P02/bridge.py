# bridge.py
import asyncio

class ParticipantBridge:
    def __init__(self, participant_module):
        self.module = participant_module

    def call_safety_app(self, helpers, student_data):
        """
        Adapts to the async class-based architecture in helper_assignment.py.
        """
        try:
            # 1. Register Helpers
            h_service = self.module.HelperService()
            for h_name in helpers:
                h_service.register_helper(h_name)

            # 2. Create Request
            r_service = self.module.RequestService()
            # We run this synchronously for the test suite
            asyncio.run(r_service.create_request(
                student_data.get("student_id", "Unknown"),
                student_data.get("location", "Library"),
                student_data.get("destination", "Dorms")
            ))

            # 3. Simulate a match (Check if store was updated)
            # This participant uses a shared store and background workers
            if not self.module.store.requests:
                return None
            
            return list(self.module.store.requests.values())[0].status
        except Exception as e:
            raise e

    def call_pricing_engine(self, user_type, ticket_type, quantity, peak=False):
        """
        Adapts to the PricingEngine class in ticket_pricing.py.
        """
        # Map strings to the participant's Enum types
        u_type = self.module.UserType[user_type]
        t_type = self.module.TicketType[ticket_type]
        
        user = self.module.User(user_id="test_user", user_type=u_type)
        tickets = [self.module.TicketRequest(ticket_type=t_type, quantity=quantity)]
        
        # Call the class method directly
        return self.module.PricingEngine.calculate_total(user, tickets)
