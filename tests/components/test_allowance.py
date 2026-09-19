"""The readout on an allowance gauge, which is the figure a reader acts on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

from textual.app import App, ComposeResult
from textual.pilot import Pilot

from flexi.components.allowance import paint_balance, paint_entitlement
from flexi.components.common import Gauge
from flexi.constants import AbsenceType
from flexi.domain.balance import BalanceSummary
from flexi.domain.wallet import Allowance, WalletData

CONTRACTED = timedelta(minutes=444)
LEAVE_YEAR = (date(2026, 4, 6), date(2027, 4, 5))


@asynccontextmanager
async def mounted(gauge: Gauge) -> AsyncIterator[Pilot[None]]:
    """A gauge in an app with nothing else in it."""

    class Harness(App[None]):
        def compose(self) -> ComposeResult:
            yield gauge

    async with Harness().run_test(size=(60, 20)) as pilot:
        yield pilot


def wallet(balance: timedelta) -> WalletData:
    return WalletData(
        leave_year=LEAVE_YEAR,
        balance=BalanceSummary(worked=balance),
        period=BalanceSummary(),
        contracted=CONTRACTED,
        allowances=(),
    )


async def test_sub_day_balance_stays_in_hours() -> None:
    """A "(0d)" tag beside a figure that is not zero is a contradiction."""
    gauge = Gauge("TOIL")
    async with mounted(gauge):
        paint_balance(gauge, wallet(timedelta(minutes=15)))

        assert gauge.readout == "+0:15"


async def test_balance_worth_days_says_how_many() -> None:
    """Hours are what a balance is measured in; days are what it is spent in."""
    gauge = Gauge("TOIL")
    async with mounted(gauge):
        paint_balance(gauge, wallet(timedelta(hours=19, minutes=48)))

        assert gauge.readout == "+19:48  (+2.7d)"


async def test_overspent_entitlement_uses_a_minus_sign() -> None:
    """Every other negative on the panel is written with U+2212, so this one is."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        paint_entitlement(
            gauge,
            Allowance(type=AbsenceType.ANNUAL, used=27.0, occurrences=9, total=25.0),
        )

        assert gauge.readout == "−2 left of 25"


async def test_days_left_are_stated_unsigned() -> None:
    """A remainder is a count, not a movement, so it carries no plus."""
    gauge = Gauge("Annual")
    async with mounted(gauge):
        paint_entitlement(
            gauge,
            Allowance(type=AbsenceType.ANNUAL, used=20.0, occurrences=9, total=25.0),
        )

        assert gauge.readout == "5 left of 25"
