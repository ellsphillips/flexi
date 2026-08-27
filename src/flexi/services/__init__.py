"""Typed, lazy access to Flexi's service layer.

Importing this package resolves no service implementation. That distinction is
important for :mod:`flexi.services.setup`, whose lightweight SQLite probe sits
on every CLI command's startup path and deliberately imports neither SQLAlchemy
nor the full service graph. A service module or symbol is imported only when a
caller asks for that attribute, then cached on this module.
"""

from __future__ import annotations

# These imports describe attributes that PEP 562 resolves lazily at runtime.
# ruff: noqa: TC004
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from flexi.services import (
        absence,
        adjustments,
        bank_holidays,
        clock,
        ledger,
        outcome,
        registry,
        samples,
        settings,
        setup,
        startup,
        transactions,
        wallet,
        work_sessions,
    )
    from flexi.services.absence import (
        PLAN_CHANGED,
        AbsencePlan,
        AbsenceResult,
        AbsenceService,
        DayFacts,
        PlannedDay,
        RangeResult,
        RemovalPlan,
        Span,
        Tally,
        clash_reason,
        covers_the_whole_day,
        deficit,
        overdraw,
        span_of,
        still_bookable,
        verdict_for,
    )
    from flexi.services.adjustments import (
        OPENING_BALANCE,
        AdjustmentResult,
        AdjustmentService,
    )
    from flexi.services.bank_holidays import (
        CACHE_MAX_AGE,
        GOVUK_URL,
        REQUEST_TIMEOUT,
        BankHolidayService,
        ParsedBankHoliday,
        parse_bank_holidays,
    )
    from flexi.services.clock import ClockResult, ClockService
    from flexi.services.ledger import LedgerService, day_kind, end_of_day, segment_of
    from flexi.services.outcome import Outcome
    from flexi.services.registry import (
        Services,
        available_toil_days,
        build_services,
        invalidate_services,
        minimum_session,
        settlement_date,
        zero_balance,
    )
    from flexi.services.samples import (
        ANCHOR,
        ARRIVALS,
        AUGUST,
        EXTRAS,
        FRIDAY,
        LEAVE_YEAR,
        LUNCHES,
        MAY,
        NOW,
        TIMEZONE,
        add_minutes,
        holidays_in,
        nth_monday,
        seed_demo,
    )
    from flexi.services.settings import (
        CLOCK_PATTERN,
        DEFAULT_AUTO_CLOSE,
        DEFAULT_ENTITLEMENT_DAYS,
        DEFAULT_LEAVE_YEAR_START,
        DEFAULT_WORKING_DAYS,
        HOURS_IN_DAY,
        LONGEST_MONTH,
        MINUTES_IN_HOUR,
        NOON,
        LeaveYearStart,
        ResolvedSettings,
        SettingsService,
        SettingsUpdate,
        WorkingDays,
        duration_minutes,
        format_clock_time,
        format_leave_year_start,
        format_window,
        format_working_days,
        named_weekday,
        parse_clock_time,
        parse_month_day,
        parse_settings,
        parse_working_days,
        read_or,
        readable_window,
        resolve_settings,
        validate_window,
    )
    from flexi.services.setup import (
        REQUIRED_SETTINGS,
        clear_initialisation_cache,
        forget,
        is_initialised,
        stamped_and_configured,
    )
    from flexi.services.startup import close_stale_sessions
    from flexi.services.transactions import atomic, write_transaction
    from flexi.services.wallet import WalletService
    from flexi.services.work_sessions import stage_clock_out

_SUBMODULES: Final = (
    "absence",
    "adjustments",
    "bank_holidays",
    "clock",
    "ledger",
    "outcome",
    "registry",
    "samples",
    "settings",
    "setup",
    "startup",
    "transactions",
    "wallet",
    "work_sessions",
)

# Every public name has exactly one source. A future collision must be resolved
# with a semantic name at its defining module rather than by import order here.
_EXPORTS: Final = MappingProxyType(
    {
        "AbsencePlan": ("absence", "AbsencePlan"),
        "AbsenceResult": ("absence", "AbsenceResult"),
        "AbsenceService": ("absence", "AbsenceService"),
        "DayFacts": ("absence", "DayFacts"),
        "PlannedDay": ("absence", "PlannedDay"),
        "RangeResult": ("absence", "RangeResult"),
        "RemovalPlan": ("absence", "RemovalPlan"),
        "Span": ("absence", "Span"),
        "Tally": ("absence", "Tally"),
        "clash_reason": ("absence", "clash_reason"),
        "covers_the_whole_day": ("absence", "covers_the_whole_day"),
        "deficit": ("absence", "deficit"),
        "overdraw": ("absence", "overdraw"),
        "span_of": ("absence", "span_of"),
        "still_bookable": ("absence", "still_bookable"),
        "verdict_for": ("absence", "verdict_for"),
        "PLAN_CHANGED": ("absence", "PLAN_CHANGED"),
        "OPENING_BALANCE": ("adjustments", "OPENING_BALANCE"),
        "AdjustmentResult": ("adjustments", "AdjustmentResult"),
        "AdjustmentService": ("adjustments", "AdjustmentService"),
        "CACHE_MAX_AGE": ("bank_holidays", "CACHE_MAX_AGE"),
        "GOVUK_URL": ("bank_holidays", "GOVUK_URL"),
        "REQUEST_TIMEOUT": ("bank_holidays", "REQUEST_TIMEOUT"),
        "BankHolidayService": ("bank_holidays", "BankHolidayService"),
        "ParsedBankHoliday": ("bank_holidays", "ParsedBankHoliday"),
        "parse_bank_holidays": ("bank_holidays", "parse_bank_holidays"),
        "ClockResult": ("clock", "ClockResult"),
        "ClockService": ("clock", "ClockService"),
        "LedgerService": ("ledger", "LedgerService"),
        "day_kind": ("ledger", "day_kind"),
        "end_of_day": ("ledger", "end_of_day"),
        "segment_of": ("ledger", "segment_of"),
        "Outcome": ("outcome", "Outcome"),
        "Services": ("registry", "Services"),
        "available_toil_days": ("registry", "available_toil_days"),
        "build_services": ("registry", "build_services"),
        "invalidate_services": ("registry", "invalidate_services"),
        "minimum_session": ("registry", "minimum_session"),
        "settlement_date": ("registry", "settlement_date"),
        "zero_balance": ("registry", "zero_balance"),
        "ANCHOR": ("samples", "ANCHOR"),
        "ARRIVALS": ("samples", "ARRIVALS"),
        "AUGUST": ("samples", "AUGUST"),
        "EXTRAS": ("samples", "EXTRAS"),
        "FRIDAY": ("samples", "FRIDAY"),
        "LEAVE_YEAR": ("samples", "LEAVE_YEAR"),
        "LUNCHES": ("samples", "LUNCHES"),
        "MAY": ("samples", "MAY"),
        "NOW": ("samples", "NOW"),
        "TIMEZONE": ("samples", "TIMEZONE"),
        "add_minutes": ("samples", "add_minutes"),
        "holidays_in": ("samples", "holidays_in"),
        "nth_monday": ("samples", "nth_monday"),
        "seed_demo": ("samples", "seed_demo"),
        "CLOCK_PATTERN": ("settings", "CLOCK_PATTERN"),
        "DEFAULT_AUTO_CLOSE": ("settings", "DEFAULT_AUTO_CLOSE"),
        "DEFAULT_ENTITLEMENT_DAYS": ("settings", "DEFAULT_ENTITLEMENT_DAYS"),
        "DEFAULT_LEAVE_YEAR_START": ("settings", "DEFAULT_LEAVE_YEAR_START"),
        "DEFAULT_WORKING_DAYS": ("settings", "DEFAULT_WORKING_DAYS"),
        "HOURS_IN_DAY": ("settings", "HOURS_IN_DAY"),
        "LONGEST_MONTH": ("settings", "LONGEST_MONTH"),
        "MINUTES_IN_HOUR": ("settings", "MINUTES_IN_HOUR"),
        "NOON": ("settings", "NOON"),
        "LeaveYearStart": ("settings", "LeaveYearStart"),
        "ResolvedSettings": ("settings", "ResolvedSettings"),
        "SettingsService": ("settings", "SettingsService"),
        "SettingsUpdate": ("settings", "SettingsUpdate"),
        "WorkingDays": ("settings", "WorkingDays"),
        "duration_minutes": ("settings", "duration_minutes"),
        "format_clock_time": ("settings", "format_clock_time"),
        "format_leave_year_start": ("settings", "format_leave_year_start"),
        "format_window": ("settings", "format_window"),
        "format_working_days": ("settings", "format_working_days"),
        "named_weekday": ("settings", "named_weekday"),
        "parse_clock_time": ("settings", "parse_clock_time"),
        "parse_month_day": ("settings", "parse_month_day"),
        "parse_settings": ("settings", "parse_settings"),
        "parse_working_days": ("settings", "parse_working_days"),
        "read_or": ("settings", "read_or"),
        "readable_window": ("settings", "readable_window"),
        "resolve_settings": ("settings", "resolve_settings"),
        "validate_window": ("settings", "validate_window"),
        "REQUIRED_SETTINGS": ("setup", "REQUIRED_SETTINGS"),
        "clear_initialisation_cache": ("setup", "clear_initialisation_cache"),
        "forget": ("setup", "forget"),
        "is_initialised": ("setup", "is_initialised"),
        "stamped_and_configured": ("setup", "stamped_and_configured"),
        "close_stale_sessions": ("startup", "close_stale_sessions"),
        "atomic": ("transactions", "atomic"),
        "write_transaction": ("transactions", "write_transaction"),
        "WalletService": ("wallet", "WalletService"),
        "stage_clock_out": ("work_sessions", "stage_clock_out"),
    }
)

# Preserve source-module grouping instead of sorting unlike concepts together.
__all__ = (  # noqa: RUF022
    "absence",
    "adjustments",
    "bank_holidays",
    "clock",
    "ledger",
    "outcome",
    "registry",
    "samples",
    "settings",
    "setup",
    "startup",
    "transactions",
    "wallet",
    "work_sessions",
    "AbsencePlan",
    "AbsenceResult",
    "AbsenceService",
    "DayFacts",
    "PlannedDay",
    "RangeResult",
    "RemovalPlan",
    "Span",
    "Tally",
    "clash_reason",
    "covers_the_whole_day",
    "deficit",
    "overdraw",
    "span_of",
    "still_bookable",
    "verdict_for",
    "PLAN_CHANGED",
    "OPENING_BALANCE",
    "AdjustmentResult",
    "AdjustmentService",
    "CACHE_MAX_AGE",
    "GOVUK_URL",
    "REQUEST_TIMEOUT",
    "BankHolidayService",
    "ParsedBankHoliday",
    "parse_bank_holidays",
    "ClockResult",
    "ClockService",
    "LedgerService",
    "day_kind",
    "end_of_day",
    "segment_of",
    "Outcome",
    "Services",
    "available_toil_days",
    "build_services",
    "invalidate_services",
    "minimum_session",
    "settlement_date",
    "zero_balance",
    "ANCHOR",
    "ARRIVALS",
    "AUGUST",
    "EXTRAS",
    "FRIDAY",
    "LEAVE_YEAR",
    "LUNCHES",
    "MAY",
    "NOW",
    "TIMEZONE",
    "add_minutes",
    "holidays_in",
    "nth_monday",
    "seed_demo",
    "CLOCK_PATTERN",
    "DEFAULT_AUTO_CLOSE",
    "DEFAULT_ENTITLEMENT_DAYS",
    "DEFAULT_LEAVE_YEAR_START",
    "DEFAULT_WORKING_DAYS",
    "HOURS_IN_DAY",
    "LONGEST_MONTH",
    "MINUTES_IN_HOUR",
    "NOON",
    "LeaveYearStart",
    "ResolvedSettings",
    "SettingsService",
    "SettingsUpdate",
    "WorkingDays",
    "duration_minutes",
    "format_clock_time",
    "format_leave_year_start",
    "format_window",
    "format_working_days",
    "named_weekday",
    "parse_clock_time",
    "parse_month_day",
    "parse_settings",
    "parse_working_days",
    "read_or",
    "readable_window",
    "resolve_settings",
    "validate_window",
    "REQUIRED_SETTINGS",
    "clear_initialisation_cache",
    "forget",
    "is_initialised",
    "stamped_and_configured",
    "close_stale_sessions",
    "atomic",
    "write_transaction",
    "WalletService",
    "stage_clock_out",
)


def __getattr__(name: str) -> object:
    """Import and cache one service module or symbol on first access."""
    if name in _SUBMODULES:
        module_name, attribute = f"{__name__}.{name}", None
    elif route := _EXPORTS.get(name):
        module, attribute = route
        module_name = f"{__name__}.{module}"
    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    from importlib import import_module

    imported = import_module(module_name)
    resolved = imported if attribute is None else getattr(imported, attribute)
    globals()[name] = resolved
    return resolved


def __dir__() -> list[str]:
    """Include unresolved facade exports in interactive discovery."""
    return sorted(set(globals()) | set(__all__))
