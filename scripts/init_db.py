import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from remembrance.storage.db import init_db
init_db()
print("db initialized")
