import os
import json
from datetime import datetime, timezone
import traceback
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

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
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        print(f"🔥 UNCAUGHT ERROR in {request.url.path}: {e}")
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "message": "Internal Server Error",
                "error": str(e),
                "path": request.url.path
            }
        )

# Initialize Server
@app.on_event("startup")
async def init_server():
    try:
        # Supabase Auth check
        if not os.getenv("SUPABASE_JWT_SECRET"):
            print("⚠️  SUPABASE_JWT_SECRET not set — auth will fail for all protected routes!")
        else:
            print("🟢 Supabase JWT Secret loaded — auth ready")

        # Initialize PostgreSQL tables
        if engine:
            Base.metadata.create_all(bind=engine)
            print("✅ PostgreSQL initialized")
        else:
            print("❌ PostgreSQL initialization skipped: No database engine found")
        
        # Seed Data
        if engine:
            with Session(engine) as session:
                if session.query(Zone).count() == 0:
                    print("🌱 Seeding parking zones...")
                    
                    # Retrieve from environment, fallback to defaults if not provided
                    try:
                        zone1_raw = os.getenv("AB1")
                        zone2_raw = os.getenv("AB3")
                        zone3_raw = os.getenv("AB5")
                        
                        zone1_poly = json.loads(zone1_raw) if zone1_raw else []
                        zone2_poly = json.loads(zone2_raw) if zone2_raw else []
                        zone3_poly = json.loads(zone3_raw) if zone3_raw else []

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
                    except Exception as seed_err:
                        print(f"⚠️ Seeding failed: {seed_err}")
        else:
            print("⚠️ Skipping seeding: No database connection")

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
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db_status = "ok"
        
        # Check if tables exist
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        required_tables = ["users", "parkingzones", "reservations"]
        missing_tables = [t for t in required_tables if t not in tables]
        
        if missing_tables:
            db_status = f"warning: missing tables {missing_tables}"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        "database": db_status,
        "tables": tables if 'tables' in locals() else []
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

# Health check (public) — remove after debugging
@app.get("/debug-auth")
def debug_auth():
    try:
        from middleware.auth import SUPABASE_JWT_SECRET
        return {
            "jwt_secret_env_present": bool(os.getenv("SUPABASE_JWT_SECRET")),
            "jwt_secret_processed": bool(SUPABASE_JWT_SECRET),
            "jwt_secret_length": len(SUPABASE_JWT_SECRET) if SUPABASE_JWT_SECRET else 0,
            "jwt_secret_type": type(SUPABASE_JWT_SECRET).__name__,
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Backend server listening on http://localhost:{port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port)