from __future__ import annotations

import enum
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, Time, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SeasonPattern(str, enum.Enum):
    SINGLE_YEAR = "single_year"
    CROSS_YEAR = "cross_year"


class EventOrder(str, enum.Enum):
    OFFICIAL = "official"
    WEEKEND = "weekend"
    ROUND = "round"


class EventOrigin(str, enum.Enum):
    UPSTREAM = "upstream"
    MANUAL = "manual"


class SegmentStatus(str, enum.Enum):
    STAGED = "staged"
    REVIEW = "review"
    PUBLISHED = "published"
    IGNORED = "ignored"
    ERROR = "error"


class ReviewTaskStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Competition(Base):
    __tablename__ = "competition"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tsdb_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alternate_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    sport: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    formed_year: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    season_pattern: Mapped[SeasonPattern] = mapped_column(String(32), default=SeasonPattern.SINGLE_YEAR.value)
    season_split_month: Mapped[int | None] = mapped_column(Integer)
    season_split_day: Mapped[int | None] = mapped_column(Integer)
    event_order: Mapped[EventOrder] = mapped_column(String(32), default=EventOrder.OFFICIAL.value)
    poster_url: Mapped[str | None] = mapped_column(String(1024))
    banner_url: Mapped[str | None] = mapped_column(String(1024))
    fanart_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    seasons: Mapped[list["CompetitionSeason"]] = relationship(back_populates="competition", cascade="all, delete-orphan")

    def all_names(self) -> list[str]:
        return [self.name, *self.alternate_names]


class CompetitionSeason(Base):
    __tablename__ = "competition_season"
    __table_args__ = (UniqueConstraint("competition_id", "season_number", name="uq_competition_season"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    competition_id: Mapped[str] = mapped_column(ForeignKey("competition.id"), nullable=False, index=True)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_complete: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    competition: Mapped[Competition] = relationship(back_populates="seasons")
    events: Mapped[list["Event"]] = relationship(back_populates="competition_season", cascade="all, delete-orphan")
    segments: Mapped[list["Segment"]] = relationship(back_populates="competition_season")


class Event(Base):
    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tsdb_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    competition_season_id: Mapped[str] = mapped_column(ForeignKey("competition_season.id"), index=True)
    origin: Mapped[EventOrigin] = mapped_column(String(32), default=EventOrigin.UPSTREAM.value)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[date | None] = mapped_column(Date)
    time: Mapped[time | None] = mapped_column(Time)
    round: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(255))
    home_team: Mapped[str | None] = mapped_column(String(255))
    away_team: Mapped[str | None] = mapped_column(String(255))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    thumb_url: Mapped[str | None] = mapped_column(String(1024))
    event_sequence: Mapped[int | None] = mapped_column(Integer, index=True)
    weekend_group: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    competition_season: Mapped[CompetitionSeason] = relationship(back_populates="events")
    segments: Mapped[list["Segment"]] = relationship(back_populates="event")


class Segment(Base):
    __tablename__ = "segment"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("event.id"))
    competition_season_id: Mapped[str] = mapped_column(ForeignKey("competition_season.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    episode_number: Mapped[int | None] = mapped_column(Integer)
    segment_code: Mapped[int | None] = mapped_column(Integer)
    air_date: Mapped[date | None] = mapped_column(Date)
    air_time: Mapped[time | None] = mapped_column(Time)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    thumb_url: Mapped[str | None] = mapped_column(String(1024))
    source_path: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    managed_path: Mapped[str | None] = mapped_column(String(2048))
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_method: Mapped[str | None] = mapped_column(String(64))
    metadata_source: Mapped[str | None] = mapped_column(String(64))
    metadata_record: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metadata_images: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    metadata_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SegmentStatus] = mapped_column(String(32), default=SegmentStatus.STAGED.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    event: Mapped[Event | None] = relationship(back_populates="segments")
    competition_season: Mapped[CompetitionSeason] = relationship(back_populates="segments")
    review_tasks: Mapped[list["ReviewTask"]] = relationship(back_populates="segment")


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    cached_path: Mapped[str | None] = mapped_column(String(2048))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Override(Base):
    __tablename__ = "override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("segment.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class ReviewTask(Base):
    __tablename__ = "review_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("segment.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[ReviewTaskStatus] = mapped_column(String(32), default=ReviewTaskStatus.OPEN.value, index=True)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    segment: Mapped[Segment] = relationship(back_populates="review_tasks")


class ApiCache(Base):
    __tablename__ = "api_cache"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())


class LogRecord(Base):
    __tablename__ = "log_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), index=True)
    level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    logger_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
