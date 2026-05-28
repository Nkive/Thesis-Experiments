# bridge.py
class ParticipantBridge:
    def __init__(self, participant_module):
        self.module = participant_module

    def call_safety_app(self, helpers, student_data):
        """
        Adapts to the Safety App logic.
        Note: This participant has two 'assign_helpers' functions; 
        Python will use the last one defined.
        """
        try:
            # The participant's second 'assign_helpers' does not filter 'student_id'.
            # If you want to test the first one, you would need to rename them in their file.
            return self.module.assign_helpers(helpers, student_data)
        except Exception as e:
            raise e

    def call_pricing_engine(self, user_type, ticket_type, quantity, peak_hour=False):
        """
        Adapts to the Pricing logic in ticket_pricing.py.
        Maps the specific parameter order used by the participant.
        """
        # The participant implementation explicitly checks for positive quantity.
        # It also handles student and senior/retired discounts.
        return self.module.calculate_price(user_type, ticket_type, quantity, peak_hour)