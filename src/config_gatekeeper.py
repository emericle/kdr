import os
import sys

def validate_configs(exit_on_error: bool = True) -> bool:
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
        msg = f"Error: Missing the following environment variables:\n - {', '.join(missing)}"
        if exit_on_error:
            print(msg)
            sys.exit(1)
        return False
    
    print("Configuration validated successfully.")
    return True

if __name__ == "__main__":
    validate_configs()
