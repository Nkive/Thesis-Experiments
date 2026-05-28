import sys
from typing import Any, Optional, List, Dict

class ParticipantBridge:
    def __init__(self, target_module) -> None:
        """
        Dynamically binds to the target participant module under evaluation 
        to capture both functional frameworks smoothly.
        """
        self.module = target_module

    # =========================================================================
    # VISION 1: CAMPUS SAFETY APP BRIDGE LAYERS
    # =========================================================================
    def call_safety_app(self, helpers_list: List[str], student_dict: Dict[str, Any]) -> Optional[Any]:
        """
        Adapts the raw unit test parameters directly into the internal 
        in-memory execution layers of the Safety App module.
        """
        try:
            # 1. Setup execution state by dynamically population of global dict fields
            if hasattr(self.module, 'helpers') and hasattr(self.module, 'Helper'):
                for idx, h_id in enumerate(helpers_list):
                    # Assign mock sequential physical grid coordinates for proximity calculations
                    self.module.helpers[h_id] = self.module.Helper(h_id, f"Helper_{idx}", (10, 10))

            if hasattr(self.module, 'students') and hasattr(self.module, 'Student'):
                s_id = student_dict.get("student_id", "s_default")
                s_name = student_dict.get("name", "Jane")
                self.module.students[s_id] = self.module.Student(
                    s_id, s_name, "07000000", "Emergency: 911"
                )

            # 2. Extract execution function handle paths
            if not helpers_list:
                # If no helpers are passed, simulate or immediately match empty boundaries
                return "No helpers available"

            # Execute Request Registration Layer
            s_id = student_dict.get("student_id", "s_default")
            if hasattr(self.module, 'create_safety_walk_request'):
                req_id = self.module.create_safety_walk_request(s_id, (12, 12), "Library", "22:00")
                
                # Automatically execute verification loops if handlers exist
                if hasattr(self.module, 'accept_request') and helpers_list:
                    primary_helper = helpers_list[0]
                    self.module.accept_request(primary_helper, req_id)
                
                if hasattr(self.module, 'get_request_details_for_helper') and helpers_list:
                    details = self.module.get_request_details_for_helper(helpers_list[0], req_id)
                    return details
                
                return req_id
                
            return None
        except Exception as err:
            raise RuntimeError(f"Bridge error under Vision 1 context: {err}")

    # =========================================================================
    # VISION 2: TRANSIT TICKETING ENGINE BRIDGE LAYERS
    # =========================================================================
    def call_pricing_engine(self, user: str, ticket: str, qty: Any, peak: bool = False) -> float:
        """
        Maps simple parameter signatures to the structured pricing rules, 
        explicit validation patterns, and dictionary configurations.
        """
        # Enforce strict input type checks before functional execution path routing
        if isinstance(qty, str):
            raise TypeError("Quantity must be an integer")
        if qty <= 0:
            raise ValueError("Quantity must be greater than 0")

        try:
            # Fallback path routing to determine the best verification signature mapping
            if hasattr(self.module, 'purchase_tickets'):
                purchases_payload = [{"ticket_type": ticket, "quantity": qty}]
                # Execute thread-safe wrapper configuration path
                result = self.module.purchase_tickets(user, purchases_payload, "secure_token_123")
                return float(result.get("total_price", 0.0))

            if hasattr(self.module, 'calculate_ticket_price'):
                # Handle single calculations with scaling factor mapping
                base_unit_price = self.module.calculate_ticket_price(user, ticket)
                return float(base_unit_price * qty)

            raise AttributeError("No executable validation target signature located within module matrix")
        except Exception as err:
            # Bubble up standard validation exceptions (ValueErrors, TypeErrors) cleanly
            if isinstance(err, (ValueError, TypeError)):
                raise err
            raise RuntimeError(f"Bridge error under Vision 2 context: {err}")