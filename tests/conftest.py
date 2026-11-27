import os, sys, tempfile
from pathlib import Path
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="gtaclips-test-"))
os.environ.setdefault("APP_PASSWORD", "test-pw")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
