import os
import base64
import jwt  # PyJWT
from fastapi import Request, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from config.database import get_db
from models.postgres import User

security = HTTPBearer(auto_error=False)

# Supabase JWT secret — from Project Settings > API > JWT Secret
# The dashboard shows it base64-encoded; decode it to raw bytes for PyJWT
_raw_secret = os.getenv("SUPABASE_JWT_SECRET")
try:
    SUPABASE_JWT_SECRET = base64.b64decode(_raw_secret + "==") if _raw_secret else None
except Exception:
    SUPABASE_JWT_SECRET = _raw_secret.encode() if _raw_secret else None


def requireAuth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(get_db)
):
    try:
        if not SUPABASE_JWT_SECRET:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Server auth is not configured (SUPABASE_JWT_SECRET missing).",
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

        # Verify Supabase JWT
        try:
            decoded_token = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_audience": False},
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail={"message": "Unauthorized: Token has expired"})
        except jwt.InvalidTokenError as verifyError:
            print(f"🔑 Token Verification Failed: {verifyError}")
            raise HTTPException(
                status_code=401,
                detail={"message": "Unauthorized: Invalid token", "error": str(verifyError)}
            )

        uid = decoded_token.get("sub")        # Supabase user UUID lives in 'sub'
        email = decoded_token.get("email")

        # Find or Create User in PostgreSQL
        user = db.query(User).filter(User.uid == uid).first()

        if not user:
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
        print(f"🔑 Auth Middleware Error: {error}")
        raise HTTPException(
            status_code=500,
            detail={"message": "Internal Server Authentication Error", "error": str(error)}
        )


def requireApprovedUser(request: Request):
    pass
