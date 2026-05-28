import unittest
import sys
from test_safety_app import TestSafetyApp
from test_transport_system import TestTicketPricing

def run_clean_evaluation():
    print("\n" + "="*50)
    print("THESIS EVALUATION: PARTICIPANT LOG")
    print("="*50)

    suites = [
        ("VISION 1: SAFETY APP", TestSafetyApp),
        ("VISION 2: TRANSPORT SYSTEM", TestTicketPricing)
    ]

    for title, suite_class in suites:
        print(f"\n--- {title} ---")
        suite = unittest.TestLoader().loadTestsFromTestCase(suite_class)
        result = unittest.TestResult()
        suite.run(result)

        # Track results for the summary
        total = suite.countTestCases()
        failures = {f[0].id().split('.')[-1]: f[1] for f in result.failures}
        errors = {e[0].id().split('.')[-1]: e[1] for e in result.errors}
        
        # Print individual test status
        for test in suite:
            test_id = test.id().split('.')[-1]
            if test_id in failures or test_id in errors:
                print(f"[ FAIL ] {test_id}")
            else:
                print(f"[ PASS ] {test_id}")
        
        passed = total - len(failures) - len(errors)
        print(f"\nTASK SCORE: {passed}/{total} ({(passed/total)*100:.1f}%)")

    print("\n" + "="*50)
    print("FINAL COVERAGE REPORT")
    print("="*50)

if __name__ == "__main__":
    run_clean_evaluation()
    
    # Trigger coverage tracking targeting your actual module filenames
    import subprocess
    subprocess.run([
        "pytest", 
        "--cov=experiment.safety_app", 
        "--cov=experiment.transport_system", 
        "--cov-report=term-missing"
    ])