import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lantai.storage.db import init_db

init_db()
print("db initialized")
