import os
from datetime import datetime, timezone
from fastapi import Request, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth
from sqlalchemy.orm import Session
from config.database import get_db
from models.postgres import User

security = HTTPBearer(auto_error=False)

def requireAuth(
    request: Request, 
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(get_db)
):
    try:
        # Verify Firebase app is active
        try:
            from firebase_admin import get_app
            get_app()
        except ValueError:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Server auth is not configured (Firebase Admin not initialized).",
                    "code": "AUTH_NOT_CONFIGURED"
                }
            )

        # Token Extraction
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=401,
                detail={"message": "Unauthorized: No token provided"}
            )

        token = credentials.credentials
        
        # Verify token validity
        try:
            decoded_token = firebase_auth.verify_id_token(token)
        except Exception as verifyError:
            print(f"🔥 Token Verification Failed: {verifyError}")
            raise HTTPException(
                status_code=401,
                detail={"message": "Unauthorized: Invalid token", "error": str(verifyError)}
            )

        uid = decoded_token.get("uid")
        email = decoded_token.get("email")

        # Find or Create User in PostgreSQL
        user = db.query(User).filter(User.uid == uid).first()

        if not user:
            # Optionally just auto-create them if loging in, like many systems do:
            user = User(uid=uid, email=email)
            db.add(user)
            try:
                db.commit()
                db.refresh(user)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to create user record")
            
        request.state.user = user
        return user

    except HTTPException:
        raise
    except Exception as error:
        print(f"🔥 Auth Middleware Error: {error}")
        raise HTTPException(
            status_code=500,
            detail={"message": "Internal Server Authentication Error", "error": str(error)}
        )

def requireApprovedUser(request: Request):
    pass