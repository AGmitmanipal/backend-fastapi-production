import os
import json
import base64
import requests
import traceback
import jwt  # PyJWT
from fastapi import Request, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from config.database import get_db
from models.postgres import User

security = HTTPBearer(auto_error=False)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

# In-memory cache for JWKS public keys (keyed by 'kid')
_jwks_cache: dict = {}


def _get_public_key(kid: str):
    """
    Fetch and cache Supabase's JWKS public keys.
    Used for asymmetric algorithms like ES256.
    """
    global _jwks_cache

    if kid in _jwks_cache:
        return _jwks_cache[kid]

    jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    try:
        response = requests.get(jwks_url, timeout=10)
        response.raise_for_status()
        jwks = response.json()
        for key_data in jwks.get("keys", []):
            key_id = key_data.get("kid")
            alg = key_data.get("alg", "ES256")
            if alg in ("ES256", "ES384", "ES512"):
                public_key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(key_data))
            elif alg in ("RS256", "RS384", "RS512"):
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
            else:
                continue
            if key_id:
                _jwks_cache[key_id] = public_key

        return _jwks_cache.get(kid)
    except Exception as e:
        print(f"⚠️ Failed to fetch JWKS from {jwks_url}: {e}")
        return None


def _decode_token(token: str) -> dict:
    """
    Decode and verify a Supabase JWT.
    Supports ES256 (asymmetric, via JWKS) and HS256 (symmetric, via secret).
    """
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    if alg in ("ES256", "ES384", "ES512", "RS256", "RS384", "RS512"):
        if not kid:
            raise jwt.InvalidTokenError("Token is missing 'kid' header required for asymmetric verification.")

        public_key = _get_public_key(kid)
        if not public_key:
            # Clear cache and retry once (key rotation)
            _jwks_cache.clear()
            public_key = _get_public_key(kid)

        if not public_key:
            raise jwt.InvalidTokenError(f"Public key not found for kid '{kid}'. JWKS fetch may have failed.")

        return jwt.decode(token, public_key, algorithms=[alg], options={"verify_aud": False})

    else:
        # HS256: symmetric verification via shared secret
        if not SUPABASE_JWT_SECRET:
            raise jwt.InvalidTokenError("SUPABASE_JWT_SECRET is not configured.")

        try:
            secret_bytes = base64.b64decode(SUPABASE_JWT_SECRET)
        except Exception:
            secret_bytes = SUPABASE_JWT_SECRET.encode("utf-8")

        return jwt.decode(token, secret_bytes, algorithms=["HS256"], options={"verify_aud": False})


def requireAuth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(get_db)
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail={"message": "Unauthorized: No token provided."})

    token = credentials.credentials

    try:
        print(f"🔑 Verifying token...")
        decoded_token = _decode_token(token)
        print(f"🔑 Token verified for sub: {decoded_token.get('sub')}")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail={"message": "Unauthorized: Token has expired."})
    except jwt.InvalidTokenError as e:
        print(f"🔑 Token Verification Failed: {e}")
        raise HTTPException(status_code=401, detail={"message": "Unauthorized: Invalid token.", "error": str(e)})
    except Exception as e:
        print(f"🔑 Auth Middleware Error during decoding: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail={"message": "Internal Server Authentication Error.", "error": str(e)})

    uid = decoded_token.get("sub")
    email = decoded_token.get("email")
    user_metadata = decoded_token.get("user_metadata", {})
    vehicle_plate = user_metadata.get("vehicle_plate") if isinstance(user_metadata, dict) else None

    print(f"🔑 Fetching/Creating user for uid: {uid}")
    try:
        user = db.query(User).filter(User.uid == uid).first()
        if not user:
            print(f"🔑 User not found, creating new user for email: {email}")
            try:
                user = User(uid=uid, email=email, vehiclePlate=vehicle_plate)
                db.add(user)
                db.commit()
                db.refresh(user)
                print(f"🔑 New user created with id: {user.id}")
            except IntegrityError:
                # Email already exists (race condition or UID mismatch) — fetch & heal the existing record
                db.rollback()
                print(f"🔑 Email '{email}' already exists (IntegrityError). Fetching existing user and syncing UID.")
                user = db.query(User).filter(User.email == email).first()
                if user:
                    if user.uid != uid:
                        print(f"🔑 Updating UID from {user.uid} → {uid}")
                        user.uid = uid
                    if vehicle_plate and not user.vehiclePlate:
                        user.vehiclePlate = vehicle_plate
                    db.commit()
                    db.refresh(user)
                    print(f"🔑 User record healed for id: {user.id}")
                else:
                    raise HTTPException(status_code=500, detail={"message": "Failed to sync user record: email conflict but user not found."})

        if user and vehicle_plate and not user.vehiclePlate:
            # Backfill vehicle plate if it was added later and not yet saved
            print(f"🔑 Backfilling vehicle plate: {vehicle_plate}")
            user.vehiclePlate = vehicle_plate
            db.commit()
            print(f"🔑 Vehicle plate updated")
    except HTTPException:
        raise
    except Exception as e:
        print(f"🔑 Database error in auth middleware: {e}")
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail={"message": "Failed to sync user record.", "error": str(e)})

    request.state.user = user
    return user


def requireApprovedUser(request: Request):
    pass
