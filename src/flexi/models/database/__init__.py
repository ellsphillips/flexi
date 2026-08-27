"""Database records, lifecycle helpers, and migration operations.

This is the stable persistence facade. The leaf modules remain available for a
narrower import, while wildcard imports expose only the names deliberately
listed here rather than SQLAlchemy, Alembic, or standard-library dependencies.
"""

from __future__ import annotations

from flexi.models.database import backup, db, engine, invariants, migrate, moment
from flexi.models.database.backup import (
    PROTECTED_PREFIX,
    ROUTINE_PREFIX,
    snapshot,
    verify,
)
from flexi.models.database.db import (
    DEFAULT_CONTRACTED_MINUTES,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    SETTINGS_SINGLETON_KEY,
    AbsenceDay,
    BalanceAdjustment,
    BankHolidayCache,
    Base,
    ClockEvent,
    LeaveEntitlement,
    Settings,
    WorkSession,
)
from flexi.models.database.engine import (
    create_db_engine,
    database_scope,
    enforce_foreign_keys,
    get_session,
)
from flexi.models.database.invariants import (
    CLOCK_EVENT_UPDATE_ERROR,
    CLOCK_EVENT_UPDATE_TRIGGER,
    clock_event_update_trigger_sql,
    create_clock_event_update_trigger,
    drop_clock_event_update_trigger,
    drop_clock_event_update_trigger_sql,
    register_clock_event_immutability,
)
from flexi.models.database.migrate import (
    HEAD,
    MAX_BACKUPS,
    DatabaseRevision,
    MigrationConfig,
    RevisionState,
    alembic_config,
    backup_database,
    current_revision,
    prune_backups,
    run_migrations,
)
from flexi.models.database.moment import moment_of, punched

__all__ = (
    "CLOCK_EVENT_UPDATE_ERROR",
    "CLOCK_EVENT_UPDATE_TRIGGER",
    "DEFAULT_CONTRACTED_MINUTES",
    "DEFAULT_WINDOW_END",
    "DEFAULT_WINDOW_START",
    "HEAD",
    "MAX_BACKUPS",
    "PROTECTED_PREFIX",
    "ROUTINE_PREFIX",
    "SETTINGS_SINGLETON_KEY",
    "AbsenceDay",
    "BalanceAdjustment",
    "BankHolidayCache",
    "Base",
    "ClockEvent",
    "DatabaseRevision",
    "LeaveEntitlement",
    "MigrationConfig",
    "RevisionState",
    "Settings",
    "WorkSession",
    "alembic_config",
    "backup",
    "backup_database",
    "clock_event_update_trigger_sql",
    "create_clock_event_update_trigger",
    "create_db_engine",
    "current_revision",
    "database_scope",
    "db",
    "drop_clock_event_update_trigger",
    "drop_clock_event_update_trigger_sql",
    "enforce_foreign_keys",
    "engine",
    "get_session",
    "invariants",
    "migrate",
    "moment",
    "moment_of",
    "prune_backups",
    "punched",
    "register_clock_event_immutability",
    "run_migrations",
    "snapshot",
    "verify",
)
