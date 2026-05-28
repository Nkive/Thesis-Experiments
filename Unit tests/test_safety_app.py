import unittest
from bridge import ParticipantBridge
import experiment.safety_app as safety_app

class TestSafetyApp(unittest.TestCase):
    def setUp(self):
        self.bridge = ParticipantBridge(safety_app)

    def test_t1_basic_functionality(self):
        """Verifies basic helper assignment logic[cite: 377, 396]."""
        res = self.bridge.call_safety_app(["Helper1"], {"name": "Jane"})
        self.assertIsNotNone(res)

    def test_t2_empty_helpers(self):
        """Ensures the system handles zero available helpers[cite: 382]."""
        res = self.bridge.call_safety_app([], {"name": "Jane"})
        self.assertIn(res, [None, [], "No helpers available"])

    def test_t3_privacy_filter(self):
        """Critical: Verifies Student ID is not leaked in output[cite: 388, 391]."""
        student = {"name": "Jane", "student_id": "GU99-123"}
        res = str(self.bridge.call_safety_app(["Helper1"], student))
        self.assertNotIn("99-123", res, "FAIL: Privacy Leak detected!")

    def test_t4_multi_helper_selection(self):
        """Verifies logic when multiple volunteers are available[cite: 396, 398]."""
        helpers = ["H1", "H2", "H3"]
        res = str(self.bridge.call_safety_app(helpers, {"name": "Jane"}))
        self.assertTrue(any(h in res for h in helpers), "FAIL: No helper assigned from list")

    def test_t5_robustness_malformed_data(self):
        """Tests handling of missing keys in student data[cite: 402, 404]."""
        try:
            # Code should handle empty dictionaries without crashing
            self.bridge.call_safety_app(["Helper1"], {}) 
        except Exception as e:
            self.fail(f"Safety App crashed on malformed data: {e}")

    def test_t6_unauthorized_user(self):
        """Requirement: Rejects non-University students (no 'GU' prefix)[cite: 110, 169]."""
        # Based on the ValueError logic found in your helper_assignment.py
        with self.assertRaises(ValueError):
            self.bridge.call_safety_app(["Helper1"], {"student_id": "OTHER-123"})