"""Configure sys.path so pytest can find admin_module."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# admin_module reads ORTHANC_ADMIN_USER/PASS at import time (lines 47-48), not
# at call time. Without them every route that talks to Orthanc answers 503
# before even sending the request, and the respx mocks are never reached: 18
# tests were failing for that reason alone. conftest.py is loaded before the
# test modules, so this is where, and nowhere else, they must be set.
# setdefault rather than item assignment: a real environment keeps precedence.
os.environ.setdefault("ORTHANC_ADMIN_USER", "test-admin")
os.environ.setdefault("ORTHANC_ADMIN_PASS", "test-pass")
