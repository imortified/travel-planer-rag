from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    interests: Mapped[dict] = mapped_column(JSON, default=dict)


class TripRequest(Base):
    __tablename__ = "trip_requests"
    __table_args__ = (
        CheckConstraint("budget >= 0"),
        CheckConstraint("start >= 0 AND start < end_time AND end_time <= 1440"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    destination: Mapped[str] = mapped_column(String(100), default="Demo City")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    travel_date: Mapped[date | None]
    budget: Mapped[int]
    start: Mapped[int]
    end_time: Mapped[int]
    mandatory: Mapped[list] = mapped_column(JSON)
    excluded: Mapped[list] = mapped_column(JSON)
    participants_snapshot: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RouteOption(Base):
    __tablename__ = "route_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_request_id: Mapped[int] = mapped_column(ForeignKey("trip_requests.id"), index=True)
    algorithm: Mapped[str] = mapped_column(String(50), default="greedy-v1")
    data_source: Mapped[str] = mapped_column(String(50), default="synthetic-demo-v1")
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
