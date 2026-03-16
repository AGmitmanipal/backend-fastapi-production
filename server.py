import os
import json
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials

load_dotenv()

# SQLAlchemy config and Models
from config.database import Base, engine, get_db
from models.postgres import Zone, Reservation, User

# Route imports
from routes.book import router as bookingRouter
from routes.reserve import reserveRouter, startReservationCron
from routes.auth import router as authRouter
from routes.admin import router as adminRouter

# Auth Imports
from middleware.auth import requireAuth, requireApprovedUser

# 1. Initialize App
app = FastAPI(title="Parking API")

# 2. Middlewares (CORS & Logging)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    print(f"📨 [{now}] {request.method} {request.url.path}")
    response = await call_next(request)
    return response

# Initialize Server
@app.on_event("startup")
async def init_server():
    try:
        # Initialize Firebase Admin
        try:
            firebase_admin.get_app()
        except ValueError:
            import json
            service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "./service-account.json")
            service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

            if service_account_json:
                try:
                    # Parse the env string as JSON
                    cred_dict = json.loads(service_account_json)
                    cred = credentials.Certificate(cred_dict)
                    firebase_admin.initialize_app(cred)
                    print("🔥 Firebase Admin initialized from GOOGLE_SERVICE_ACCOUNT_JSON")
                except json.JSONDecodeError:
                    print("⚠️ Failed to decode GOOGLE_SERVICE_ACCOUNT_JSON")
            elif os.path.exists(service_account_path):
                cred = credentials.Certificate(service_account_path)
                firebase_admin.initialize_app(cred)
                print("🔥 Firebase Admin initialized from file")
            else:
                # Last resort using individual keys
                cred_dict = {
                    "type": "service_account",
                    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
                    "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").replace('\\n', '\n'),
                    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
                print("🔥 Firebase Admin initialized from individual env vars")

        # Initialize PostgreSQL tables
        Base.metadata.create_all(bind=engine)
        print("✅ PostgreSQL initialized")
        
        # Seed Data
        with Session(engine) as session:
            if session.query(Zone).count() == 0:
                print("🌱 Seeding parking zones...")
                
                # Retrieve from environment, fallback to defaults if not provided
                zone1_poly = json.loads(os.getenv("AB1"))
                zone2_poly = json.loads(os.getenv("AB3"))
                zone3_poly = json.loads(os.getenv("AB5"))
                seed_zones = [
                    Zone(
                        name="AB1 Manipal",
                        capacity=10,
                        available=10,
                        isActive=True,
                        polygon=zone1_poly
                    ),
                    Zone(
                        name="AB3 Manipal",
                        capacity=18,
                        available=18,
                        isActive=True,
                        polygon=zone2_poly
                    ),
                    Zone(
                        name="AB5 Manipal",
                        capacity=8,
                        available=8,
                        isActive=True,
                        polygon=zone3_poly
                    )
                ]
                session.add_all(seed_zones)
                session.commit()
                print("✅ Seeded parking zones")

        # Start cron jobs
        startReservationCron()
        print("📅 Reservation expiry cron started")
    except Exception as err:
        print(f"❌ Cleanup/Init error: {err}")

# Health Check (Public)
@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        # Check DB connection
        db.execute("SELECT 1")
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        "database": db_status
    }

# --- ROUTES ---

# 1. Auth & Admin (Include routers)
app.include_router(authRouter, prefix="/api/auth", tags=["Auth"])
app.include_router(adminRouter, prefix="/api/admin", tags=["Admin"])

# GET ZONES (Protected)
@app.get("/", dependencies=[Depends(requireAuth), Depends(requireApprovedUser)])
async def get_zones(db: Session = Depends(get_db)):
    try:
        # 1. Fetch Zones
        zones = db.query(Zone).filter(Zone.isActive == True).order_by(Zone.name.asc()).all()

        # 2. Fetch Active Reservation Counts
        reservations = db.query(Reservation).filter(Reservation.status.in_(["booked", "reserved"])).all()

        stats_map = {}
        for r in reservations:
            zone_id = r.zoneId
            status = r.status

            if zone_id not in stats_map:
                stats_map[zone_id] = {"reserved": 0, "booked": 0}
            
            if status == "reserved":
                stats_map[zone_id]["reserved"] += 1
            if status == "booked":
                stats_map[zone_id]["booked"] += 1

        response = []
        for zone in zones:
            zone_id = zone.id
            stats = stats_map.get(zone_id, {"reserved": 0, "booked": 0})

            # Count by status
            reserved_count = stats["reserved"]
            booked_count = stats["booked"]

            # Calculate availability
            capacity = zone.capacity or 0
            available = max(0, min(capacity, capacity - reserved_count - booked_count))

            response.append({
                "_id": zone_id,
                "name": zone.name or "Unnamed Zone",
                "polygon": zone.polygon or [],

                # Return explicit counts
                "capacity": capacity,
                "available": available,

                # Computed stats
                "reserved": reserved_count,
                "prebooked": booked_count,
            })

        return response
    except Exception as err:
        print(f"❌ GET / ERROR: {err}")
        raise HTTPException(status_code=500, detail={"message": "Server error", "error": str(err)})

# Mount remaining routers
app.include_router(bookingRouter, prefix="/api/book", tags=["Booking"], dependencies=[Depends(requireAuth), Depends(requireApprovedUser)])
app.include_router(reserveRouter, tags=["Reservation"], dependencies=[Depends(requireAuth), Depends(requireApprovedUser)])

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Backend server listening on http://localhost:{port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port)