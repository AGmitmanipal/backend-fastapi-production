import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List

from config.database import get_db, SessionLocal
from models.postgres import Reservation, Zone

reserveRouter = APIRouter()

cron_started = False

async def expire_reservations_task():
    while True:
        try:
            db = SessionLocal()
            now = datetime.now(timezone.utc)
            
            # Find expired reservations
            expired = db.query(Reservation).filter(
                Reservation.status.in_(["reserved", "booked"]),
                Reservation.toTime < now
            ).all()
            
            if expired:
                for res in expired:
                    res.status = "expired"
                db.commit()
                print(f"♻️ Expired {len(expired)} reservations/bookings...")
            db.close()
        except Exception as err:
            print(f"❌ Cron error: {err}")
            
        await asyncio.sleep(60)

def startReservationCron():
    global cron_started
    if cron_started:
        return
    cron_started = True
    asyncio.create_task(expire_reservations_task())


# ================= GET USER BOOKINGS =================
@reserveRouter.get("/reserve/book")
def get_user_bookings(
    userId: str = Query(None), 
    email: str = Query(None), 
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    target_id = userId or email
    if not target_id:
        raise HTTPException(status_code=400, detail="userId required")
        
    try:
        query = db.query(
            Reservation.id,
            Reservation.userId,
            Reservation.zoneId,
            Reservation.fromTime,
            Reservation.toTime,
            Reservation.status,
            Reservation.parkedAt,
            Reservation.createdAt,
            Zone.name.label("zoneName")
        ).outerjoin(Zone, Reservation.zoneId == Zone.id).filter(Reservation.userId == target_id)
        
        if status:
            status_list = [s.strip() for s in status.split(",")]
            query = query.filter(Reservation.status.in_(status_list))
            
        bookings = query.order_by(Reservation.toTime.desc()).all()
        
        results = [
            {
                "_id": b.id,
                "userId": b.userId,
                "zoneId": b.zoneId,
                "fromTime": b.fromTime,
                "toTime": b.toTime,
                "status": b.status,
                "parkedAt": b.parkedAt,
                "createdAt": b.createdAt,
                "zoneName": b.zoneName if b.zoneName else "Unknown Zone"
            }
            for b in bookings
        ]
            
        return results
    except Exception as err:
        print(err)
        raise HTTPException(status_code=500, detail="Failed to load bookings")


class ReservePayload(BaseModel):
    userId: str
    zoneId: int
    fromTime: datetime
    toTime: datetime


# ================= CREATE BOOKING =================
@reserveRouter.post("/prebook")
def create_prebook(req: ReservePayload, db: Session = Depends(get_db)):
    start = req.fromTime
    end = req.toTime
    now = datetime.now(timezone.utc)
    
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    
    if start >= end:
        raise HTTPException(status_code=400, detail="Invalid time range")
        
    if start <= now:
        raise HTTPException(status_code=400, detail="Bookings must be for future time. Use /reserve for immediate reservations.")
        
    try:
        zone = db.query(Zone).filter(Zone.id == req.zoneId).with_for_update().first()
        if not zone:
            raise HTTPException(status_code=404, detail="Zone not found")
            
        # 1. One active action per zone
        existing = db.query(Reservation).filter(
            Reservation.userId == req.userId,
            Reservation.zoneId == req.zoneId,
            Reservation.status.in_(["booked", "reserved"])
        ).first()
        
        if existing:
            raise HTTPException(status_code=409, detail="You already have an active booking or parking in this zone.")
            
        # 2. Capacity Check (Overlap logic from PG: toTime > start AND fromTime < end)
        overlap_stats = db.query(Reservation.status, func.count(Reservation.id)).filter(
            Reservation.zoneId == req.zoneId,
            Reservation.status.in_(["reserved", "booked"]),
            Reservation.toTime > start,
            Reservation.fromTime < end
        ).group_by(Reservation.status).all()
        
        total_reserved = 0
        total_booked = 0
        for st, count in overlap_stats:
            if st == "reserved": total_reserved = count
            elif st == "booked": total_booked = count
            
        capacity = zone.capacity or 0
        
        if (total_reserved + total_booked) >= capacity:
             raise HTTPException(status_code=409, detail="Zone is fully booked for this time range.")
             
        overall_available = max(0, capacity - total_reserved - total_booked)
        if overall_available <= 0:
             raise HTTPException(status_code=409, detail="Zone is fully booked. No available spots.")
             
        new_res = Reservation(
            userId=req.userId,
            zoneId=req.zoneId,
            fromTime=start,
            toTime=end,
            status="booked"
        )
        db.add(new_res)
        db.commit()
        db.refresh(new_res)
        
        return {
            "message": "Booking confirmed. Your reservation will activate at the scheduled time.",
            "reservationId": new_res.id,
            "status": "booked"
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as err:
        db.rollback()
        print(f"❌ Pre-booking Error: {err}")
        raise HTTPException(status_code=500, detail="Server Error")


# ================= MAKE RESERVATION =================
@reserveRouter.post("/reserve")
def make_reservation(req: ReservePayload, db: Session = Depends(get_db)):
    start = req.fromTime
    end = req.toTime
    now = datetime.now(timezone.utc)
    
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
        
    if start >= end:
        raise HTTPException(status_code=400, detail="Invalid time range")
        
    if start > now:
        raise HTTPException(status_code=400, detail="Reservations must start now (or earlier). For future time windows, use /prebook.")
        
    try:
        zone = db.query(Zone).filter(Zone.id == req.zoneId).with_for_update().first()
        if not zone:
            raise HTTPException(status_code=404, detail="Zone not found")
            
        existing = db.query(Reservation).filter(
            Reservation.userId == req.userId,
            Reservation.zoneId == req.zoneId,
            Reservation.status.in_(["booked", "reserved"])
        ).first()
        
        if existing:
            if existing.status == "booked":
                exStart = existing.fromTime
                if exStart.tzinfo is None: exStart = exStart.replace(tzinfo=timezone.utc)
                exEnd = existing.toTime
                if exEnd.tzinfo is None: exEnd = exEnd.replace(tzinfo=timezone.utc)
                
                overlapsOwn = start < exEnd and end > exStart
                isActiveReservationWindow = start <= now and now <= end
                
                if not isActiveReservationWindow:
                    raise HTTPException(status_code=409, detail="Reservations are only allowed for present time (check-in now).")
                if not overlapsOwn:
                    raise HTTPException(status_code=409, detail="Your reservation time must overlap your pre-booking time window.")
                
                overlaps_count = db.query(func.count(Reservation.id)).filter(
                    Reservation.zoneId == req.zoneId,
                    Reservation.status.in_(["reserved", "booked"]),
                    Reservation.toTime > start,
                    Reservation.fromTime < end,
                    Reservation.id != existing.id
                ).scalar()
                
                capacity = zone.capacity or 0
                if overlaps_count >= capacity:
                    raise HTTPException(status_code=409, detail="Zone is fully booked for this time range.")
                    
                existing.status = "reserved"
                existing.parkedAt = now
                existing.fromTime = start
                existing.toTime = end
                
                db.commit()
                return {"message": "Booking converted to active reservation.", "reservationId": existing.id, "status": "reserved"}
                
            elif existing.status == "reserved":
                raise HTTPException(status_code=409, detail="You already have an active parking in this zone.")
                
        # New Reservation Capacity Check
        overlaps_count = db.query(func.count(Reservation.id)).filter(
            Reservation.zoneId == req.zoneId,
            Reservation.status.in_(["reserved", "booked"]),
            Reservation.toTime > start,
            Reservation.fromTime < end
        ).scalar()
        
        capacity = zone.capacity or 0
        if overlaps_count >= capacity:
            raise HTTPException(status_code=409, detail="Zone is fully booked for this time range.")
            
        new_res = Reservation(
            userId=req.userId,
            zoneId=req.zoneId,
            fromTime=start,
            toTime=end,
            status="reserved",
            parkedAt=now
        )
        db.add(new_res)
        db.commit()
        db.refresh(new_res)
        
        return {
            "message": "Parking confirmed!",
            "reservationId": new_res.id,
            "status": "reserved"
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as err:
        db.rollback()
        print(f"❌ Reservation Error: {err}")
        raise HTTPException(status_code=500, detail="Server Error")

# ================= CANCEL RESERVATION =================
@reserveRouter.delete("/reserve/{id}")
def cancel_reservation(id: int, db: Session = Depends(get_db)):
    try:
        doc = db.query(Reservation).filter(Reservation.id == id).with_for_update().first()
        if not doc:
            raise HTTPException(status_code=404, detail="Parking not found")
            
        if doc.status not in ["booked", "reserved"]:
            raise HTTPException(status_code=400, detail=f"Cannot cancel parking with status: {doc.status}")
            
        doc.status = "cancelled"
        db.commit()
        
        return {"message": "Cancelled successfully", "reservationId": id}
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as err:
        db.rollback()
        print(f"❌ Cancel Error: {err}")
        raise HTTPException(status_code=500, detail="Cancel failed")