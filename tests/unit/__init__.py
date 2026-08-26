import sys
from pathlib import Path

# Ensure the src directory is in the python path
root_dir = Path(sys.argv[0]).parent.parent
if root_dir.exists():
    sys.path.append(str(root_dir))
else:
    sys.path.append(str(Path.cwd()))

# Also add src specifically
src_dir = Path(sys.argv[0]).parent.parent / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

import os
os.environ["PYTHONPATH"] = str(Path.cwd())

# Final check for the core modules we need to test
try:
    from src.config_gatekeeper import validate_configs
    print("Successfully imported config_gatekeeper")
except ImportError as e:
    print(f"Import failed: {e}")
