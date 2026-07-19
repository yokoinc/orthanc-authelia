"""Configure sys.path pour que pytest trouve admin_module."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
