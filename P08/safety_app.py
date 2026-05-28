from datetime import datetime

def handle_safety_request(location, destination, departure_time):
    # --- 1. VALIDATE INPUT ---

    if not location or not isinstance(location, str):
        print("Invalid location")
        return None

    if not destination or not isinstance(destination, str):
        print("Invalid destination")
        return None

    if not isinstance(departure_time, datetime):
        print("Invalid time")
        return None

    if departure_time < datetime.now():
        print("Time is in the past")
        return None

    # --- 2. CREATE REQUEST ---

    request = {
        "location": location,
        "destination": destination,
        "time": departure_time
    }

    # --- 3. PUBLISH (SIMPLIFIED) ---

    print("New request sent to volunteers:")
    print(request)

    return request