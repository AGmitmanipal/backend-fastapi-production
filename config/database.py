import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Setup PostgreSQL Database URL
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Fix for SQLAlchemy 1.4/2.0+ which requires 'postgresql://' instead of 'postgres://'
if SQLALCHEMY_DATABASE_URL and SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Initialize engine and session global variables
engine = None
SessionLocal = None

if SQLALCHEMY_DATABASE_URL:
    try:
        engine = create_engine(SQLALCHEMY_DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        print("🐘 PostgreSQL Database Engine Initialized")
    except Exception as e:
        print(f"❌ Failed to initialize PostgreSQL engine: {e}")
else:
    print("⚠️ DATABASE_URL not found in environment variables")

Base = declarative_base()

# FastAPI Dependency for injecting the database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
