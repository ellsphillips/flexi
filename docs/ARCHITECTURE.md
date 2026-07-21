# Architecture

## 1. Layers

```
  screens/         Textual Screens. Compose widgets, own bindings, push modals.
  components/      Widgets. Own a rectangle. Never touch a database.
      │
      ▼  app.services
  services/        Own a SQLAlchemy Session. Transactions, validation, results.
      │
      ▼
  domain/          Pure Python. Dates, durations, periods, ledgers. No I/O.
  models/          SQLAlchemy tables. No behaviour.
```

The rule that keeps this honest: **`flexi/domain/` may not import `textual` or
`sqlalchemy`, and `flexi/components/` may not import `sqlalchemy`.** Both are
enforced by a test (`tests/test_layering.py`) that walks the AST of every module
and asserts the import sets. It costs twenty lines and it is the reason the
arithmetic stays testable.

## 2. Package layout

```
src/flexi/
  __main__.py            click CLI: `flexi`, `flexi doctor`, `flexi export`
  app.py                 FlexiApp — theme, screens, services, jump mode
  config.py              Hotkeys + defaults, pydantic, ~/.config/flexi/config.yaml
  constants.py           Enums: ClockAction, AbsenceType, Portion, DayKind
  locations.py           XDG paths (unchanged)
  versioning.py          PyPI update check (unchanged)

  theme/
    __init__.py          palette() parser, flexi_theme()
    flexi.tcss           PALETTE block + the design system
  styles/
    dashboard.tcss  modules.tcss  modals.tcss  jump.tcss

  domain/
    period.py            Granularity, Period
    ledger.py            Segment, AbsenceSlice, DayLedger, DayKind
    balance.py           expected_hours, worked, flexi balance accumulation
    punch.py             bucketing for the punch strip (pure)
    format.py            timedelta -> "7:24", "+0:48", "−4:14"

  models/database/
    db.py  app.py  migrate.py

  services/
    registry.py          Services: one object holding every service, on the app
    clock.py  absence.py  wallet.py  ledger.py  calendar.py
    settings.py  bank_holidays.py  startup.py  export.py

  components/
    common.py            Tone, Pill, StatCard, KeyHint, Rule, Gauge
    chrome.py            Wordmark, NavBar, AppHeader, StatusBar, KeyStrip, AppFooter
    punch.py             PunchStrip
    jumper.py            Jumper            (from the reference application, verbatim)
    jump_overlay.py      JumpOverlay       (from the reference application, adapted)
    expandable.py        ExpandableTable   (wraps DataTable — see §6)
    charts/              sparkline.py, bars.py, calendar_heat.py
    modules/
      clock.py  balance.py  wallet.py  records.py  calendar.py  insights.py

  screens/
    dashboard.py  insights.py  settings.py  setup.py  help.py
    modals/  absence.py  session.py  goto.py  confirm.py
```

`pages/` and the per-component `style.scss` files go away. Stylesheets are
collected in `styles/` and listed in `FlexiApp.CSS_PATH`, because a component's
`DEFAULT_CSS` is scoped to that component and always loses to the app stylesheet
at equal specificity — so a component sheet cannot override the design system,
which is a footgun disguised as encapsulation.

## 3. Services and the session

Today every widget does `self.app._session` and constructs its own service.
That is four constructions per rebuild and a private attribute reached through
`# type: ignore`. Replace it with one registry, built once:

```python
@dataclass(slots=True)
class Services:
    session: Session
    settings: SettingsService
    bank_holidays: BankHolidayService
    clock: ClockService
    absence: AbsenceService
    ledger: LedgerService
    wallet: WalletService

    @classmethod
    def build(cls, session: Session) -> Services: ...
```

`FlexiApp.services` holds one. A widget reaches it through a typed helper:

```python
def flexi_app(widget: Widget) -> FlexiApp:
    return cast("FlexiApp", widget.app)
```

One cast in one place beats an `isinstance` dance in a dozen handlers.

**`LedgerService` is new and it is the important one.** It computes a `DayLedger`
per date and memoises per rebuild generation, so a records table showing 31 days
does one pass over the period rather than 31 × 4 service calls. The current
`DayTable.rebuild` issues a query per day per concern; on a month view that is
roughly 150 round trips to redraw one table.

## 4. Data flow

There is one direction and one refresh path.

```
  keypress / click
        │
        ▼
  Screen action or widget message
        │
        ▼
  service call  ──►  returns a Result(success, message, payload)
        │
        ├──►  status bar shows result.message
        │
        ▼
  post DataChanged(scope)   ── a Textual Message bubbling to the screen
        │
        ▼
  DashboardScreen.on_data_changed  ──►  ledger.invalidate()
        │                              └─►  module.rebuild() for each module
        ▼                                   in the scope
  redraw
```

`DataChanged.scope` is a flag set (`CLOCK | ABSENCE | SETTINGS | PERIOD`) so
clocking in does not rebuild the calendar's bank-holiday markers. Modules declare
what they care about:

```python
class WalletModule(Module):
    WATCHES = Scope.ABSENCE | Scope.CLOCK | Scope.SETTINGS
```

**Never call another module's `rebuild()` directly.** The current `Home.rebuild()`
calls four modules by attribute; that is why adding a fifth means editing a
method in a different file.

### The live tick

While a session is open, the dashboard runs `set_interval(1, ...)` — one second,
not sixty. The clock module's subtitle is a running duration and a minute-grained
clock that jumps in 60-second steps looks broken. The tick only refreshes the two
widgets that show elapsed time (clock subtitle, balance) and only while
`ledger.today.is_open`; it is torn down on clock-out and on unmount.

## 5. Screens, navigation and the command palette

```python
NAV_ITEMS = (
    NavItem("f1", "dashboard", "Dashboard", "Clock, balance, records"),
    NavItem("f2", "insights",  "Insights",  "Charts of consumption and activity"),
    NavItem("f3", "settings",  "Settings",  "Hours, leave year, bank holidays"),
)
```

One table. The app builds its bindings from it, `NavBar` builds its clickable
items from it, and `FlexiCommands` builds palette entries from it. Adding a
screen is one line.

`ENABLE_COMMAND_PALETTE` is **on**. `FlexiCommands(Provider)` supplies:
clock in/out, book each absence type, go to date, jump to each period
granularity, export CSV, open settings, and Textual's own theme list. The palette
is the discoverability backstop for the long tail of actions that do not deserve
a key.

Screens are registered as *factories* so an import error in one screen is a
placeholder panel and a log line, not a crash at startup (the design reference's pattern).

## 6. The records table

Requirements: a row per day in the period, expandable to the day's breakdown,
responsive, and fast enough to redraw on a one-second tick.

**Do not fork `DataTable`.** the reference application vendors 2,700 lines of it to add a
`style_name` argument for per-row styling. Flexi gets the same effect by passing
`rich.text.Text` with an explicit style into the cell:

```python
table.add_row(*(Text(cell, style=self.get_component_rich_style("record--sub"))
                for cell in cells), key=f"s-{session.id}")
```

`ExpandableTable` wraps the stock `DataTable` and owns the expansion state:

```python
class ExpandableTable(DataTable):
    expanded: set[str]                 # row keys currently open

    def set_rows(self, groups: Sequence[RowGroup]) -> None: ...
    def toggle(self, key: str) -> None: ...
    class Expanded(Message): ...
```

A `RowGroup` is a parent row plus its children; `set_rows` flattens it according
to `expanded`, preserving the cursor by key rather than by index — an expansion
above the cursor must not move it. Row keys are typed by prefix: `d-<iso>` for a
day, `s-<id>` for a session, `a-<id>` for an absence slice, `t-<iso>` for a
total. Every handler switches on that prefix, so a key is self-describing.

Children of a day row, in order:

```
  Thu 11 Jun                    ────█████▌            3:10  −4:14
    ├ 09:12 → 12:04  worked                           2:52
    ├ 12:04 → 13:30  break                            1:26
    ├ 13:30 → open   worked (running)                 0:18
    ├ expected                                        7:24
    └ delta                                          −4:14
```

## 7. Jump mode

Copied from the reference application (`components/jumper.py` verbatim, `jump_overlay.py` adapted to
Flexi's config) and extended in two ways:

1. **Targets are declared, not hardcoded.** the reference application keeps one dict in
   `App.on_mount` listing every container id in the whole application, including
   ids from screens that are not mounted. Flexi asks the screen:

   ```python
   class FlexiScreen(Screen):
       def jump_targets(self) -> Mapping[str, str]:
           return {}          # widget id -> key
   ```

   The app builds a `Jumper` from `self.screen.jump_targets()` each time the
   overlay opens, so a target can never point at something that is not there.

2. **Rows are jumpable too.** In addition to panels, the records table exposes a
   key per visible day row, so `v` then `3` puts the cursor on the third day.
   That is what makes jump mode useful in a table rather than only between
   panels.

The overlay dismisses with the widget or id, and the app focuses it — or, if it
is not focusable, posts a synthetic `Click`, which is how the reference application makes a button
jumpable. Full mechanics, with both files verbatim, in
[`research/flexi-jump-mode.md`](research/flexi-jump-mode.md).

## 8. Configuration

`~/.config/flexi/config.yaml`, pydantic-validated, written back on change.
Two sections:

```yaml
hotkeys:            # every binding, see KEYMAP.md
  clock_toggle: "/"
  toggle_jump_mode: "v"
  ...
defaults:
  period: week
  first_day_of_week: 0
  round_to_minutes: 1
  confirm_clock_out_before: "16:00"   # ask if departing unusually early
```

Bindings read from `CONFIG.hotkeys` at class-definition time (the reference application's pattern),
so the config is loaded before any widget module is imported. `config.py` must
therefore have no Flexi imports beyond `locations`.

Application *settings* (contracted hours, leave year, division) live in the
database, not here: they are data the balance depends on and they belong with the
records they explain. Config is preference; settings are domain.

## 9. Dependencies

| Change | Why |
|---|---|
| `textual>=8.2,<9` (from `1.0.0`) | The compact `Footer`/`FooterKey`/`FooterLabel` that `KeyStrip` subclasses, the `Content` API, and current theming. The UI is being rewritten, so the breaking changes land in code that is going anyway. |
| `+ pydantic>=2` | Config validation. |
| `+ pyyaml` | Config file. |
| `+ plotext` | Charts on the insights screen (the reference application's choice; a `PlotextPlot` widget wraps it). |
| `+ pytest-textual-snapshot`, `+ time-machine` | See `TESTING.md`. |

`rich`, `sqlalchemy`, `alembic`, `httpx`, `click`, `xdg-base-dirs` unchanged.

## 10. Migration path from v1

The overhaul is additive at the storage layer — no table is dropped and no
column is renamed, so an existing `flexi.db` opens in v2 after `alembic upgrade
head`. The v1 UI modules (`pages/home.py`, `components/{status,wallet,calendar,
day_table,badge,welcome,header}/`) are deleted once their v2 replacements pass
the same tests; `tests/components/test_calendar.py` and
`tests/components/test_day_table.py` are rewritten against the new modules rather
than deleted, so the behaviour they pin survives the rewrite.
