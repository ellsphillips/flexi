"""Database records, lifecycle helpers, and migration operations.

This is the stable persistence facade. The leaf modules remain available for a
narrower import, while wildcard imports expose only the names deliberately
listed here rather than SQLAlchemy, Alembic, or standard-library dependencies.
"""

from __future__ import annotations

from flexi.models.database import backup, db, engine, migrate, moment
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
    enforce_foreign_keys,
    get_session,
)
from flexi.models.database.migrate import (
    HEAD,
    MAX_BACKUPS,
    MigrationConfig,
    alembic_config,
    backup_database,
    current_revision,
    prune_backups,
    run_migrations,
)
from flexi.models.database.moment import moment_of, punched

__all__ = (
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
    "LeaveEntitlement",
    "MigrationConfig",
    "Settings",
    "WorkSession",
    "alembic_config",
    "backup",
    "backup_database",
    "create_db_engine",
    "current_revision",
    "db",
    "enforce_foreign_keys",
    "engine",
    "get_session",
    "migrate",
    "moment",
    "moment_of",
    "prune_backups",
    "punched",
    "run_migrations",
    "snapshot",
    "verify",
)
