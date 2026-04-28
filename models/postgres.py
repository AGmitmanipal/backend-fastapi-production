from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, func
from config.database import Base

class Zone(Base):
    __tablename__ = "parkingzones"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    capacity = Column(Integer, default=0)
    available = Column(Integer, default=0)
    isActive = Column("is_active", Boolean, default=True)
    polygon = Column(JSON, default=list)
    createdAt = Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedAt = Column("updated_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    userId = Column("user_id", String, index=True, nullable=False)
    zoneId = Column("zone_id", Integer, index=True, nullable=False)
    fromTime = Column("from_time", DateTime(timezone=True), nullable=False)
    toTime = Column("to_time", DateTime(timezone=True), nullable=False)
    status = Column(String, index=True, nullable=False) # booked, reserved, expired, cancelled
    parkedAt = Column("parked_at", DateTime(timezone=True), nullable=True)
    createdAt = Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedAt = Column("updated_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    uid = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    role = Column(String, default="user")
    approved = Column(Boolean, default=False)
    vehiclePlate = Column("vehicle_plate", String, nullable=True)
    createdAt = Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
