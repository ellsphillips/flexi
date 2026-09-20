# Keyboard

Flexi is a keyboard application that happens to accept a mouse. The test for
every feature is: **can it be done without reaching for the arrow keys, and is it
discoverable without reading this file?**

Three layers of discoverability, in the order a user meets them:

1. **The key strip** — the footer, trimmed to what actually fits, with `+n more`
   when it does not. Shows the six or seven keys that matter on this screen.
2. **`?`** — the help screen: every binding on the current screen, grouped by the
   widget that declared it, including the ones the strip dropped.
3. **`ctrl+p`** — the command palette: every action, fuzzy-searchable, including
   the ones that have no key at all.

Plus **`v`** — jump mode, which is navigation, not discovery.

Every key below is a default. [§ `config.yaml`](#configyaml) says how to change
one.

---

## Everywhere

| Key | Action |
|---|---|
| `/` | Clock in, or clock out. |
| `v` | Jump mode. |
| `?` | Help. |
| `ctrl+p` | Command palette. |
| `f1` `f2` `f3` `f4` | Dashboard / Leave / Insights / Settings. |
| `ctrl+q` | Quit. |

`escape` closes whatever is on top — a modal, the jump overlay, the help screen,
or the screen `f2`/`f3`/`f4` pushed. Those screens bind it; the application does
not, so on the dashboard it does nothing.

`/` is bound at **app** level with `priority=True`, so it works from any screen
and any focused widget — except inside an `Input` or a `TextArea`, where the app
stands it down and the field gets the key. That exception is correct: typing a
date into "go to date" must be able to contain a slash.

**A session under a minute never happened.** Clocking in and straight back out is
a slip of the finger. The clock-out reports `Discarded — under 1 minute on the
clock`, the events are kept, and the session is voided so it is absent from the
table and from every figure derived from it. The threshold is
`defaults.minimum_session_seconds`, 0–3600 seconds, and it is applied when that
session is closed: changing it never reclassifies historical work.

Clocking in is refused, on the status bar and never in a dialog, on a bank
holiday, on a day booked off in full, and during the half of a day that is booked
— a booked morning leaves the afternoon workable.

---

## Dashboard

### Period

| Key | Action |
|---|---|
| `d` `w` `m` `y` | Set granularity to day / week / month / leave year. Keeps the anchor. |
| `p` | Cycle granularity forward: `d → w → m → y → d`. |
| `[` `]` | Previous / next period. |
| `t` | Today. Resets the anchor, keeps the granularity. |
| `g` | Go to date — a modal that accepts `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`. |

### Records table

Live when the table has focus.

| Key | Action |
|---|---|
| `j` `k`, `down` `up` | Move the cursor. |
| `space` | Expand or collapse the day under the cursor. The cursor stays put. |
| `enter` | Expand the day under the cursor and move to its first child. Never collapses. |
| `shift+space` | Expand or collapse every day. |
| `home` `end` | First / last row. |
| `a` | Book an absence on the day under the cursor. |
| `x` | Remove the absence booking under the cursor. It asks first. |

`x` removes leave bookings. Clock records are retained as an audit trail.
Use `n` to add work that was missed; editing or deleting an existing clocked
session is not available in the interface.

`left` and `right` are `DataTable`'s own column-cursor keys here. They do not
step the period.

### Work that was never clocked

| Key | Action |
|---|---|
| `n` | Record a session on the day under the records cursor, or on the period's anchor when the table does not hold it. |
| `N` | List every correction in the period. |

### Booking, from anywhere on the dashboard

| Key | Action |
|---|---|
| `A` | Book annual leave. |
| `S` | Record sickness. |
| `T` | Take a TOIL day. |
| `U` | Record unpaid leave. |
| `O` | Record other, with a note. |

Capitals, so they never collide with the records-table letters, and so a single
shifted keystroke books from anywhere on the screen. Each opens the absence modal
pre-filled with that type and the period's anchor date — one day, not the period.
To book a range, use the leave screen.

### Calendar

Live when the month grid has focus.

| Key | Action |
|---|---|
| `left` `right` `up` `down` | Move the period's anchor by a day or a week. |
| `,` `.` | Browse to the previous / next month without moving the period. |

A click on a day is the same request an arrow key makes. The grid returns to the
anchor's month the next time the anchor moves.

---

## Leave

The whole screen acts on the cursor, or the range you extended it into.

| Key | Action |
|---|---|
| `←` `→` `↑` `↓`, `h` `j` `k` `l` | Move the cursor a day or a week. |
| `shift` + an arrow | Extend the selection. |
| `escape` | Collapse the selection — or leave the screen, when there is nothing to collapse. |
| `A` `S` `T` `U` `O` | Book annual / sick / TOIL / unpaid / other on the selection. |
| `space` | Cycle the portion: full → morning → afternoon, *before* booking. |
| `x` | Remove what is booked on the selection. More than three bookings asks first, naming them. |
| `e` | The booking modal, for a note or an odd case. |
| `g` `t` | Go to a date · today. |
| `[` `]` | A month at a time, clamped into a shorter one. |
| `home` `end` | The first and last day of the leave year. |
| `pgup` `pgdn` | Scroll a screen at a time. The cursor stays where it is. |

`O` goes straight to the modal, because other absence needs a note and a note
needs somewhere to be typed.

---

## Insights

| Key | Action |
|---|---|
| `[` `]` | Previous / next period. |
| `t` | Today. |
| `p` | Cycle the granularity. |
| `escape` | Back to the dashboard. |

The screen opens on the leave year. `d`, `w`, `m`, `y` and `g` are not bound
here; `p` reaches each granularity in turn.

---

## Modals

Every modal, without exception:

| Key | Action |
|---|---|
| `escape` | Cancel, dismissing with `None`. |
| `enter` | Confirm. |
| `tab` `shift+tab` | Next / previous field. |

`enter` stands down while a button other than Confirm holds focus, so it presses
that button instead. Otherwise the key that reaches Cancel on "Remove leave?"
would be the key that removes the leave.

A modal that breaks one of these is a bug. They are asserted for every modal by
`tests/tui/test_keyboard.py::test_every_modal_binds_escape_and_enter`, which
discovers `FlexiModal` subclasses by walking the package — so a new modal is
covered the day it is written.

---

## Jump mode

Press `v`. Every jumpable region grows a one-key badge over a corner. Press that
key and focus lands there; press `escape` and focus returns exactly where it was.

### Dashboard

| Key | Target |
|---|---|
| `c` | Clock |
| `b` | Balance |
| `w` | Wallet |
| `r` | Records |
| `p` | Calendar |

### Leave

| Key | Target |
|---|---|
| `c` | The calendar |
| `w` | The wallet |
| `s` | The selection panel |
| `b` | The "Book" legend |

The legend cannot take focus, so jumping to it clicks it and leaves the keyboard
on the calendar.

### Insights

| Key | Target |
|---|---|
| `r` | Running balance |
| `b` | Balance by week |
| `l` | Annual leave |
| `s` | Shape of the days |
| `y` | The leave year |

### Row targets

On the dashboard, the records table additionally exposes `1`–`9` over its first
nine *visible* day rows, so `v` `4` puts the cursor on the fourth day. A row is
not a widget, so those offsets come from the table's own geometry, and a row
scrolled out of view is not offered.

### Adding a target

Implement `jump_targets()` on the screen:

```python
def jump_targets(self) -> Mapping[str, str]:
    return {"clock-module": "c", "records-module": "r"}
```

The `Jumper` is rebuilt from this every time the overlay opens, so a target for a
widget that is not mounted simply does not appear. One global dict listing ids
from every screen would drop the misses silently instead.

---

## `config.yaml`

Optional, hand-written, and never written by Flexi. It lives at
`~/.config/flexi/config.yaml`, or `%APPDATA%\flexi\config.yaml` on Windows, or
under `XDG_CONFIG_HOME` when that holds an absolute path.

```yaml
hotkeys:
  clock_toggle: "/"
  period_prev: "comma"
  period_next: "full_stop"
  today: "j,down"
defaults:
  period: month
  first_day_of_week: 6
```

A value is a Textual key name or the single character it stands for, so `slash`,
`/` and `ctrl+l` are all keys. Anything longer is markup wherever the key is
drawn — the help screen and the border subtitles read `[/]` as a closing tag — so
it is refused.

**A comma separates two keys**, as in `"j,down"` above. It cannot therefore be
the key itself: write `comma` and `full_stop` for `,` and `.`, not `","` and
`"."`.

Two more rules: a hotkey must be one or more complete key names, and no key may
be bound to two actions — Textual gives the key to one of them and says nothing
about the other. Either failure, and any unknown field or out-of-range value,
makes Flexi fall back to the defaults **for that section alone**, so a typo under
`defaults` does not cost you your hotkeys. It says so once on launch, in a
warning notification.

### `hotkeys`

| Field | Default | Where |
|---|---|---|
| `clock_toggle` | `slash` | everywhere |
| `toggle_jump_mode` | `v` | everywhere |
| `help` | `question_mark` | everywhere |
| `period_day` | `d` | dashboard |
| `period_week` | `w` | dashboard |
| `period_month` | `m` | dashboard |
| `period_year` | `y` | dashboard |
| `period_cycle` | `p` | dashboard, insights |
| `period_prev` | `left_square_bracket` | dashboard, insights, leave calendar |
| `period_next` | `right_square_bracket` | dashboard, insights, leave calendar |
| `today` | `t` | dashboard, insights, leave |
| `go_to_date` | `g` | dashboard, leave |
| `new_session` | `n` | dashboard |
| `corrections` | `N` | dashboard |
| `edit` | `e` | leave |
| `delete` | `x` | records table, leave |
| `book_annual` | `A` | dashboard, leave |
| `book_sick` | `S` | dashboard, leave |
| `book_toil` | `T` | dashboard, leave |
| `book_unpaid` | `U` | dashboard, leave |
| `book_other` | `O` | dashboard, leave |
| `book_absence` | `a` | records table |

The leave screen's portion cycle is the one key this file does not reach. It is
`space` there, fixed, because `expand` already claims `space` on the records
table and one key may only answer to one action across the whole file.

### `defaults`

| Field | Default | Meaning |
|---|---|---|
| `period` | `week` | Which span the dashboard opens on: `day`, `week`, `month` or `year`. |
| `first_day_of_week` | `0` | Monday is 0. Bounded to 0–6. |
| `minimum_session_seconds` | `60` | Below this, a session never happened. Bounded to 0–3600. |
| `tick_seconds` | `1` | How often the live readout refreshes while a session is open. Bounded to 1–60. |

Application *settings* — contracted minutes, leave year, working days, bank
holiday division, auto-close time — are not here. They live in the database,
because the balance depends on them, and they are edited on `f4`.

---

## Rules for adding a binding

1. **It goes in `config.py` under `hotkeys`**, and the `Binding` reads it from
   there. No literal key strings in a widget.
2. **Decide `show`.** `show=True` means it competes for the key strip's limited
   width. A screen should show at most seven. Everything else is `show=False`
   and is found through `?` or the palette.
3. **Give it a `description` that is a verb phrase in the imperative** — `Book
   leave`, not `Leave booking`. The same words appear in the strip, the help
   screen and the palette, so they have to read as an instruction.
4. **Check the collision test.**
   `tests/tui/test_keyboard.py::test_no_two_shown_bindings_share_a_key` asserts
   that no two *shown* bindings active on the same screen use the same key, and
   that every action named by a binding exists as a method. A typo in an action
   name is otherwise silent until a user presses the key.
5. **If it acts on "the thing under the cursor", it belongs on the widget**, not
   the screen — so it is only live when that widget has focus, and the strip
   changes as the user moves. Textual's `active_bindings` does the rest.
