import os
import sys

def validate_configs():
    """Checks for necessary environment variables."""
    required_keys = [
        "ADANOS_API_KEY", 
        "ALPACA_API_KEY", 
        "ALPACA_SECRET_KEY", 
        "DATABASE_URL"
    ]
    
    missing = []
    for key in required_keys:
        if not os.getenv(key):
            missing.append(key)
    
    if missing:
        print(f"Error: Missing the following environment variables:\n - {', '.join(missing)}")
        sys.exit(1)
    
    print("Configuration validated successfully.")

if __name__ == "__main__":
    validate_configs()
