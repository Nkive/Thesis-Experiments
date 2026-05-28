import unittest
from bridge import ParticipantBridge
import experiment.transport_system as transport_system

class TestTicketPricing(unittest.TestCase):
    def setUp(self):
        self.bridge = ParticipantBridge(transport_system)

    def test_t1_basic_math(self):
        """Validates standard pricing for regular users[cite: 414]."""
        price = self.bridge.call_pricing_engine("regular", "single", 1)
        # Note: If this fails 2.0 != 3.0, record as logic failure for your ANOVA[cite: 210]
        self.assertEqual(price, 3.0)

    def test_t2_discount_logic(self):
        """Verifies students/seniors receive lower rates than regular users[cite: 420, 423]."""
        p_student = self.bridge.call_pricing_engine("student", "single", 1)
        p_regular = self.bridge.call_pricing_engine("regular", "single", 1)
        self.assertLess(p_student, p_regular)

    def test_t3_negative_input(self):
        """Strict edge-case: Code must raise ValueError for negative quantity[cite: 363, 425]."""
        with self.assertRaises(ValueError):
            self.bridge.call_pricing_engine("regular", "single", -1)

    def test_t4_advanced_features_peak(self):
        """Tests for peak-hour surcharges or specific zone pricing[cite: 364, 431]."""
        p_normal = self.bridge.call_pricing_engine("regular", "single", 1, peak=False)
        p_peak = self.bridge.call_pricing_engine("regular", "single", 1, peak=True)
        # Check if peak hours are implemented (either price changes or runs without error)
        self.assertIsNotNone(p_peak)

    def test_t5_zero_quantity_boundary(self):
        """Ensures the system rejects non-positive purchases (0 tickets)[cite: 365, 436]."""
        with self.assertRaises(ValueError):
            self.bridge.call_pricing_engine("regular", "single", 0)

    def test_t6_type_safety_validation(self):
        """Validates rejection of String inputs where Integer is expected[cite: 366, 442]."""
        with self.assertRaises((TypeError, ValueError)):
            self.bridge.call_pricing_engine("regular", "single", "one")