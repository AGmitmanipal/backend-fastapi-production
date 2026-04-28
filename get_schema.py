import os
import sys
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("No DATABASE_URL found.")
    sys.exit(1)

try:
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)
    
    for table_name in inspector.get_table_names():
        print(f"Table: {table_name}")
        for column in inspector.get_columns(table_name):
            print(f"  - {column['name']} : {column['type']}")
        print()
except Exception as e:
    print(f"Error: {e}")
