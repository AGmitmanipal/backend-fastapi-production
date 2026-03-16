import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Setup PostgreSQL Database URL
# Provide a valid default or require it to exist in the .env file.
# Example format: postgresql://username:password@host:port/database_name
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/my_database"
)

try:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print("🐘 PostgreSQL Database Engine Initialized")
except Exception as e:
    print(f"❌ Failed to initialize PostgreSQL engine: {e}")

Base = declarative_base()

# FastAPI Dependency for injecting the database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
