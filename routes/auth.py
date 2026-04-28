from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from middleware.auth import requireAuth
from config.database import get_db
from models.postgres import User

router = APIRouter()

class UpdateProfileRequest(BaseModel):
    vehiclePlate: Optional[str] = None

# GET /api/auth/me - Returns current user status
# Used by frontend to check approval status
@router.get("/me")
def get_me(user: User = Depends(requireAuth)):
    return {
        "_id": user.uid,
        "email": user.email,
        "vehiclePlate": user.vehiclePlate,
        "createdAt": user.createdAt
    }

# POST /api/auth/update-profile
@router.post("/update-profile")
def update_profile(data: UpdateProfileRequest, user: User = Depends(requireAuth), db: Session = Depends(get_db)):
    try:
        # Using Python truthiness matches JavaScript `if (vehiclePlate)` 
        # which ignores null, undefined, and empty strings.
        if data.vehiclePlate:
            user.vehiclePlate = data.vehiclePlate
            db.add(user)
            db.commit()
            db.refresh(user)

        return {
            "_id": user.uid,
            "email": user.email,
            "vehiclePlate": user.vehiclePlate,
            "createdAt": user.createdAt
        }
    except Exception as err:
        db.rollback()
        print(f"Error updating profile: {err}")
        raise HTTPException(status_code=500, detail={"message": "Server error updating profile"})
