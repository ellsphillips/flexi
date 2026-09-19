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
  models/          Tables, the engine, migrations, backups. Reaches nothing above.
```

Three rules keep this honest: **`flexi/domain/` may not import `textual` or
`sqlalchemy`; `flexi/components/` and `flexi/screens/` may not import
`sqlalchemy`; and `flexi/models/` may not import anything above it.** All three
are enforced by `tests/test_layering.py`, which walks the AST of every module and
asserts the import sets. The first is what keeps the arithmetic testable without
a terminal; the last is what keeps the graph acyclic.

## 2. Package layout

```
src/flexi/
  __main__.py            click CLI: flexi, and the groups in cli/
  app.py                 FlexiApp — theme, screens, services, jump mode
  config.py              Hotkeys + defaults, pydantic, ~/.config/flexi/config.yaml
  constants.py           Enums: ClockAction, AbsenceType, Portion, DayKind,
                         EventSource, Division, Granularity
  context.py             Protocols a widget or provider needs from the app
  locations.py           XDG and Windows paths
  messages.py            Scope, DateSelected, BankHolidayRefreshCompleted
  provider.py            FlexiCommands — the command palette catalogue
  versioning.py          PyPI update check
  wallclock.py           The only module that reads the system clock

  theme/
    __init__.py          palette() parser, flexi_theme()
    flexi.tcss           PALETTE block + the design system
  styles/
    dashboard.tcss  leave.tcss

  domain/
    period.py            Granularity, Period
    leaveyear.py         Leave-year bounds and stepping
    dates.py             Month grids, weekday helpers, date arithmetic
    stitch.py            MonthBlock, Selection — months as one continuous grid
    ledger.py            Segment, AbsenceSlice, DayLedger
    balance.py           expected_hours, worked, flexi balance accumulation
    punch.py             bucketing for the punch strip (pure)
    plot.py              axis and series shaping for the charts (pure)
    wallet.py            Allowance, Pace, WalletData
    format.py            timedelta -> "7:24", "+0:48", "−4:14"

  models/database/
    db.py  engine.py  migrate.py  backup.py  moment.py  lease.py  invariants.py
  migrations/            alembic revisions 0001..

  services/
    registry.py          Services, build_services — the whole graph, once
    transactions.py      WriteTransaction: one commit, rolled back on failure
    clock.py  absence.py  wallet.py  ledger.py  adjustments.py  work_sessions.py
    settings.py  bank_holidays.py  startup.py  setup.py  samples.py  outcome.py

  cli/
    balance.py  clock.py  holidays.py  init.py  leave.py  output.py
    ui/                  prompts, menus, key reading, the progress rail

  components/
    common.py            Tone, Pill, StatCard, KeyHint, Rule, Gauge
    chrome.py            Lockup, NavBar, AppHeader, StatusBar, KeyStrip, AppFooter
    punch.py             PunchStrip
    jumper.py  jump_overlay.py
    expandable.py        ExpandableTable   (wraps DataTable — see §6)
    yearcalendar.py      YearCalendar      (the scrolling leave year)
    progress.py          ProgressRail, TimeProgress
    charts.py            DivergingBars, Burndown, WeekRibbon, YearHeatmap
    plot.py              Plot (the running-balance line)
    allowance.py  options.py  splash.py  wordmark.py
    modules/
      base.py  clock.py  balance.py  wallet.py  records.py  monthview.py

  screens/
    dashboard.py  leave.py  insights.py  settings.py  setup.py  help.py
    modals.py            FlexiModal, AbsenceModal, GoToDateModal, ConfirmModal,
                         CorrectionModal, CorrectionsModal
```

Stylesheets live in `styles/` and are listed in `FlexiApp.CSS_PATH`. A
component's `DEFAULT_CSS` is scoped to that component and loses to the app
stylesheet at equal specificity, so a component sheet cannot override the design
system.

## 3. Services and the session

One registry, built once at startup and held on the app. Nothing caches a
settings value, so there is never a reason to build a second: two registries mean
a screen pushed before the rebuild reads one while its own modules read the
other.

```python
@dataclass(frozen=True, slots=True)
class Services:
    settings: SettingsService
    bank_holidays: BankHolidayService
    clock: ClockService
    absence: AbsenceService
    adjustments: AdjustmentService
    ledger: LedgerService
    wallet: WalletService
    write: WriteTransaction


def build_services(session: Session) -> Services: ...
```

The SQLAlchemy session is an implementation detail of those services and is not
a field. Construction is a free function because building a value is not
behaviour of the value.

Every write goes through `services.write()`, a context manager that commits once
and rolls back on any failure — SQLAlchemy leaves a session unusable after a
failed flush until it is rolled back.

A widget or a command provider reaches the app through `flexi/context.py`, which
names the capability it needs as a `runtime_checkable` Protocol and validates it
once at the edge:

```python
def service_app[ResultT](app: TextualApp[ResultT]) -> ServiceApplication: ...
def command_app[ResultT](app: TextualApp[ResultT]) -> CommandApplication: ...
def flexi_app[ResultT](app: TextualApp[ResultT]) -> FlexiApplication: ...
```

`ServiceApplication` is the registry, `CommandApplication` is the navigation and
palette actions, and `FlexiApplication` composes both. A dashboard module needs
the first and not the second. None of them imports `flexi.app`, so the
presentation graph has no cycle.

`LedgerService` computes a `DayLedger` per date and memoises per rebuild
generation, so a records table showing 31 days makes one pass over the period
instead of a query per day per concern.

## 4. Data flow

There is one direction and one refresh path.

```
  keypress / click
        │
        ▼
  Screen action or widget message
        │
        ▼
  Screen calls the service  ──►  a Result(success, message)
        │
        ├──►  status bar shows result.message
        │
        ▼
  DashboardScreen.refresh_modules(scope)  ──►  ledger.invalidate()
        │                                     └─►  module.rebuild_if(scope)
        ▼                                          for each module
  redraw
```

A module never writes. It posts a message the screen handles — `BookHere`,
`DeleteHere`, `BookRequested` — and the screen does the writing, the reporting
and the redraw.

`Scope` is a flag set (`CLOCK | ABSENCE | SETTINGS | PERIOD`) so clocking in does
not rebuild the calendar's bank-holiday markers. Modules declare what they care
about:

```python
class BalanceModule(Module):
    WATCHES = Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS
```

**Never call another module's `rebuild()` directly.** Adding a module is a
declaration, not an edit to a method in a different file.

### The live tick

The dashboard starts a `set_interval` only while a session is open, and stops it
on clock-out and on unmount. The interval is `defaults.tick_seconds`, one second
by default: the clock module's subtitle is a running duration, and a
minute-grained clock that jumps in 60-second steps looks broken.

The tick redraws the clock module, the balance module and the two progress
rails, and calls no `invalidate()` — nothing was written, and `LedgerService`
rebuilds today on every call anyway, so clearing the memo would throw away the
other thirty days of a month view once a second.

## 5. Screens, navigation and the command palette

```python
NAV_ITEMS = (
    NavItem("f1", "dashboard", "Dashboard", "Clock, balance, wallet and records"),
    NavItem("f2", "leave", "Leave", "Book and remove leave across the year"),
    NavItem("f3", "insights", "Insights", "How the balance and the allowances moved"),
    NavItem("f4", "settings", "Settings", "Hours, leave year, bank holidays"),
)
```

One table. The app builds its bindings from it, `NavBar` builds its clickable
items from it, and `FlexiCommands` builds palette entries from it. Adding a
screen is one line.

`NavItemLabel` posts `NavBar.Selected` on click and `FlexiApp` handles it by
calling the same `action_go_to` the function keys call.

The dashboard is the base of the stack. `f2` and `f3` push a destination over it
— one at a time, so opening one closes the other — and `f1` dismisses whatever is
pushed. `f4` is different: Settings is a dialog that sits on top of whichever
destination is open, held in its own attribute so `f4` twice cannot stack two
forms, and pressing the key of the destination underneath closes it. Asking for a
destination before setup is answered is refused with a notification.

`FlexiApp.COMMANDS = {FlexiCommands}` replaces Textual's stock providers, so the
palette carries Flexi's commands and nothing else. `commands(app)` builds the
catalogue: clock in or out, help, go to each screen, a period per granularity, go
to today, go to a date, book leave, book each absence type on the selected day,
and refresh bank holidays. On the setup screen, where there is no dashboard, only
the first two appear — every other entry is drawn from the period the dashboard
holds.

## 6. The records table

Requirements: a row per day in the period, expandable to the day's breakdown,
responsive, and fast enough to redraw on a one-second tick.

**Do not fork `DataTable`.** Vendoring it to add a `style_name` argument for
per-row styling costs 2,700 lines. Flexi gets the same effect by passing
`rich.text.Text` with an explicit style into the cell:

```python
table.add_row(
    *(Text(cell, style=self.get_component_rich_style("record--sub")) for cell in cells),
    key=f"s-{session.id}",
)
```

`ExpandableTable` wraps the stock `DataTable` and owns the expansion state:

```python
class ExpandableTable(DataTable):
    @property
    def expanded(self) -> frozenset[str]: ...  # row keys currently open

    def set_groups(self, groups: Iterable[RowGroup]) -> None: ...
    def toggle(self, key: str | None = None) -> bool: ...
    def focus_key(self, key: str) -> None: ...

    class Expanded(Message): ...
```

A `RowGroup` is a parent row plus its children; `set_groups` flattens it
according to `expanded`, preserving the cursor by key and not by index — an
expansion above the cursor must not move it. Expansion state is pruned to the
groups currently loaded, so `expanded` answers "open now" and not "ever opened".

Row keys are typed by prefix: `d-<iso>` for a day, `s-<id>` for a session,
`a-<id>` for an absence slice, `t-<iso>` for a total. Every handler switches on
that prefix, so a key is self-describing.

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

Two properties are worth stating, because both look like they should need
per-widget hooks and do not:

1. **Targets are declared, not hardcoded.** An application-wide dict built in
   `App.on_mount` would list every container id in the application, including
   ids from screens that are not mounted. Flexi asks the screen:

   ```python
   class FlexiScreen(Screen):
       def jump_targets(self) -> Mapping[str, str]:
           return {}  # widget id -> key
   ```

   The app builds a `Jumper` from `self.screen.jump_targets()` each time the
   overlay opens, so a target can never point at something that is not there.

2. **Rows are jumpable too.** In addition to panels, the records table exposes a
   key per visible day row through `jump_overlays()`, so `v` then `3` puts the
   cursor on the third day. A row is not a widget, so those offsets come from
   the table's own geometry.

The overlay dismisses with the widget or id, and the app focuses it — or, if it
is not focusable, posts a synthetic `Click`, which is what makes a button
jumpable.

## 8. Configuration

`~/.config/flexi/config.yaml`, pydantic-validated, read once at import. Flexi
never writes it; it is edited by hand and it does not have to exist. Two
sections:

```yaml
hotkeys:            # every binding, see KEYMAP.md
  clock_toggle: "/"
  toggle_jump_mode: "v"
defaults:
  period: week
  first_day_of_week: 0
```

A section that fails validation falls back to that section's defaults on its own,
so a typo under `defaults` does not throw away the hotkeys with it. The reason is
published as `CONFIG_PROBLEM` beside `CONFIG`, and the app notifies once on
mount.

Bindings read from `CONFIG.hotkeys` at class-definition time, so the config is
loaded before any widget module is imported. `config.py` must therefore have no
Flexi imports beyond `locations` and `constants`.

Application *settings* (contracted minutes, leave year, working days, division,
auto-close time) live in the database, not here: they are data the balance
depends on and they belong with the records they explain. The Settings screen
writes those, and never touches `config.yaml`. Config is preference; settings are
domain.

## 9. Dependencies

| Package | For |
|---|---|
| `textual>=8.2,<9` | The compact `Footer`/`FooterKey`/`FooterLabel` that `KeyStrip` subclasses, the `Content` API, and current theming. |
| `rich` | Cell and strip rendering below Textual. |
| `sqlalchemy`, `alembic` | The schema, and the migrations that reach it. |
| `click` | The command line. |
| `httpx` | GOV.UK bank holidays, and the PyPI version check. |
| `pydantic`, `pyyaml` | Config validation, and the file it validates. |
| `packaging` | Comparing the installed version against the published one. |
| `tzdata` (Windows only) | Windows ships no zoneinfo database. |

`time-machine` is a development dependency; see [`TESTING.md`](TESTING.md).
`flexi/locations.py` resolves the XDG and Windows paths itself: the two variables
it reads are two lines of `os.environ`.
