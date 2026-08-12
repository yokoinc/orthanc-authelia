"""Set up sys.path so that pytest finds admin_module."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
