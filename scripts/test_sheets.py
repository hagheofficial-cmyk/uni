#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.services.sheets_service import test_connection
ok, msg = test_connection()
print("OK" if ok else "FAIL", msg)
