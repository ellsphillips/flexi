"""Pure domain logic: dates, durations, periods, ledgers and the balance.

Nothing in this package may import ``textual`` or ``sqlalchemy``. That rule is
what makes the arithmetic testable without a terminal or a database, and it is
enforced by ``tests/test_layering.py``.
"""
