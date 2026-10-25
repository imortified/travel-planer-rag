from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    travel_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    age_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TripRequest(Base):
    __tablename__ = "trip_requests"
    __table_args__ = (
        CheckConstraint("budget >= 0"),
        CheckConstraint("start >= 0 AND start < end_time AND end_time <= 1440"),
        CheckConstraint(
            "end_date IS NULL OR (travel_date IS NOT NULL AND "
            "end_date - travel_date BETWEEN 0 AND 13)",
            name="ck_trip_period",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    destination: Mapped[str] = mapped_column(String(100), default="Demo City")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    travel_date: Mapped[date | None]
    end_date: Mapped[date | None]
    semantic_query: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pace_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    budget_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    meal_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lodging_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    flight_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    purpose: Mapped[str] = mapped_column(String(20), default="tourism", server_default="unknown")
    budget: Mapped[int]
    start: Mapped[int]
    end_time: Mapped[int]
    mandatory: Mapped[list] = mapped_column(JSON)
    excluded: Mapped[list] = mapped_column(JSON)
    participants_snapshot: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RouteSet(Base):
    __tablename__ = "route_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_request_id: Mapped[int] = mapped_column(ForeignKey("trip_requests.id"), index=True)
    algorithm: Mapped[str] = mapped_column(String(50))
    search_info: Mapped[dict] = mapped_column(JSON)
    diagnostic: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visa_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RouteOption(Base):
    __tablename__ = "route_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_request_id: Mapped[int] = mapped_column(ForeignKey("trip_requests.id"), index=True)
    route_set_id: Mapped[int | None] = mapped_column(ForeignKey("route_sets.id"), index=True)
    comparison: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visa_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm: Mapped[str] = mapped_column(String(50), default="greedy-v1")
    data_source: Mapped[str] = mapped_column(String(50), default="synthetic-demo-v1")
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogVersion(Base):
    __tablename__ = "catalog_versions"
    __table_args__ = (
        UniqueConstraint("destination", "id"),
        CheckConstraint("poi_count > 0", name="ck_catalog_count"),
        CheckConstraint("fresh_until > fetched_at", name="ck_catalog_freshness"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    destination: Mapped[str] = mapped_column(String(100), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content_sha256: Mapped[str] = mapped_column(String(64))
    poi_count: Mapped[int]
    manifest: Mapped[dict] = mapped_column(JSON)


class CatalogPoi(Base):
    __tablename__ = "catalog_pois"
    __table_args__ = (
        CheckConstraint(
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180",
            name="ck_catalog_coordinates",
        ),
        CheckConstraint("cost_per_person IS NULL OR cost_per_person >= 0", name="ck_catalog_price"),
        CheckConstraint("duration BETWEEN 1 AND 1440", name="ck_catalog_duration"),
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    place_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(32))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    cost_per_person: Mapped[int | None]
    duration: Mapped[int]
    opening_hours: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON)


class CatalogHead(Base):
    __tablename__ = "catalog_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["destination", "version_id"],
            ["catalog_versions.destination", "catalog_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    destination: Mapped[str] = mapped_column(String(100), primary_key=True)
    version_id: Mapped[int | None]


class LodgingCatalogVersion(Base):
    __tablename__ = "lodging_catalog_versions"
    __table_args__ = (
        UniqueConstraint("destination", "content_sha256"),
        UniqueConstraint("destination", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    destination: Mapped[str] = mapped_column(String(100))
    content_sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LodgingCatalogHead(Base):
    __tablename__ = "lodging_catalog_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["destination", "version_id"],
            ["lodging_catalog_versions.destination", "lodging_catalog_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    destination: Mapped[str] = mapped_column(String(100), primary_key=True)
    version_id: Mapped[int | None]


class LodgingProviderQuote(Base):
    __tablename__ = "lodging_provider_quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FlightSearchObservation(Base):
    __tablename__ = "flight_search_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
