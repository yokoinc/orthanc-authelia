"""Configure sys.path so pytest can find admin_module."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# admin_module lit ORTHANC_ADMIN_USER/PASS a l'import (lignes 47-48), pas a
# l'appel. Sans elles, chaque route qui parle a Orthanc repond 503 avant meme
# d'emettre la requete, et les mocks respx ne sont jamais atteints : 18 tests
# echouaient pour cette seule raison. conftest.py est charge avant les modules
# de test, donc c'est ici, et nulle part ailleurs, que ca doit etre pose.
# setdefault et non setitem : un environnement reel garde la main.
os.environ.setdefault("ORTHANC_ADMIN_USER", "test-admin")
os.environ.setdefault("ORTHANC_ADMIN_PASS", "test-pass")
