# Keyboard

Flexi is a keyboard application that happens to accept a mouse. The test for
every feature is: **can it be done without reaching for the arrow keys, and is it
discoverable without reading this file?**

Three layers of discoverability, in the order a user meets them:

1. **The key strip** — the footer, trimmed to what actually fits, with `+n more`
   when it does not. Shows the six or seven keys that matter on this screen.
2. **`?`** — the help screen: every binding, grouped, including the ones the
   strip dropped.
3. **`ctrl+p`** — the command palette: everything, fuzzy-searchable, including
   actions that have no key at all.

Plus **`v`** — jump mode, which is navigation rather than discovery.

---

## Global

| Key | Action |
|---|---|
| `/` | Clock in, or clock out. One key, because it is the thing you do twice a day. |
| `v` | Jump mode. |
| `?` | Help. |
| `ctrl+p` | Command palette. |
| `f1` `f2` `f3` `f4` | Dashboard / Leave / Insights / Settings. |
| `escape` | Close the modal, or leave this screen for the dashboard. |
| `ctrl+q` | Quit. |
| `ctrl+l` | Toggle the log pane. |

**A session under a minute never happened.** Clocking in and straight back out
is a slip of the finger, not a minute of work, and a records table full of them
is a records table nobody trusts. The clock-out reports `Discarded — under 1
minute on the clock`, the events are kept, and the session is voided so it is
absent from the table and from every figure derived from it. The threshold is
`defaults.minimum_session_seconds`.

`/` is deliberately a single unshifted key on the home row of the right hand, and
it is bound at **app** level with `priority=True` so it works from any screen and
any focused widget — except inside an `Input`, where Textual's own focus rules
give the key to the field. That exception is correct: typing a date into
"go to day" must be able to contain a slash.

### Clocking with one key

`/` toggles. It never asks. Two guards, both non-blocking:

- Clocking out before `defaults.confirm_clock_out_before` (16:00) shows a
  confirmation, because an accidental `/` at 11:00 silently ends your morning.
- Clocking in on a day already marked as a full-day absence is refused with a
  status-bar message naming the absence, not a modal.

The result of every clock action goes to the status bar (`Clocked in at 09:12`),
the clock module's pill flips, and the punch strip grows a live edge.

---

## Dashboard

### Period

| Key | Action |
|---|---|
| `d` `w` `m` `y` | Set granularity to day / week / month / year. Keeps the anchor. |
| `[` / `]` | Previous / next period. |
| `left` / `right` | Same, when the calendar or records table has focus. |
| `t` | Today. Resets the anchor, keeps the granularity. |
| `g` | Go to date — a modal that accepts `12`, `12 Jun`, `2026-06-12`, `+3d`, `-2w`. |
| `p` | Cycle granularity forward (the the reference application muscle memory: `d → w → m → y → d`). |

### Records table

| Key | Action |
|---|---|
| `j` / `k` or `down` / `up` | Move the cursor. |
| `space` | Expand or collapse the day under the cursor. |
| `enter` | Expand, and focus the first child. |
| `shift+space` | Expand or collapse every row. |
| `a` | Book an absence on the day under the cursor. |
| `x` | Delete the absence or session under the cursor (confirmed). |
| `e` | Edit the session under the cursor — start, end, note. |
| `n` | Add a session manually to the day under the cursor. |
| `home` / `end` | First / last row. |

### Wallet

| Key | Action |
|---|---|
| `A` | Book annual leave. |
| `S` | Record sickness. |
| `T` | Take a TOIL day. |
| `U` | Record unpaid leave. |
| `O` | Record other, with a note. |

Capitals, so they never collide with the record-table letters, and so a single
shifted keystroke books leave from anywhere on the dashboard. Each opens the
absence modal pre-filled with that type and the currently selected date.

### Calendar

| Key | Action |
|---|---|
| `left` `right` `up` `down` | Move the selection by a day or a week. |
| `,` / `.` | Previous / next month. |
| `enter` | Set the period anchor to the selected day. |

---

## Leave

The whole screen acts on the cursor, or the range you extended it into.

| Key | Action |
|---|---|
| `←` `→` `↑` `↓` / `h` `j` `k` `l` | Move the cursor a day or a week. |
| `shift` + those | Extend the selection. |
| `escape` | Collapse the selection — or leave the screen, when there is nothing to collapse. |
| `A` `S` `T` `U` `O` | Book annual / sick / TOIL / unpaid / other on the selection. |
| `space` | Cycle the portion: full → morning → afternoon, *before* booking. |
| `x` | Remove what is booked. More than three days asks first. |
| `e` | The booking modal, for a note or an odd case. |
| `[` `]` | A month at a time, clamped into a shorter one. |
| `home` `end` | The start and the end of the leave year. |

`O` goes straight to the modal, because other absence needs a note and a note
needs somewhere to be typed.

---

## Modals

Every modal, without exception:

| Key | Action |
|---|---|
| `escape` | Cancel, dismissing with `None`. |
| `enter` | Confirm, when the focused widget is not multi-line. |
| `tab` / `shift+tab` | Next / previous field. |

A modal that breaks one of these is a bug. They are asserted for every modal in
`tests/tui/test_modal_contract.py`, which discovers `ModalScreen` subclasses by
walking the package — so a new modal is covered the day it is written.

---

## Jump mode

Press `v`. Every jumpable region grows a one-key badge over its top-left corner.
Press that key and focus lands there; press `escape` and focus returns exactly
where it was.

### Panel targets

| Key | Target |
|---|---|
| `c` | Clock |
| `b` | Balance |
| `w` | Wallet |
| `r` | Records |
| `p` | Calendar (period) |
| `i` | Insights, on the insights screen |

### Row targets

The records table additionally exposes `1`–`9` for its first nine visible day
rows. `v` `4` puts the cursor on the fourth day without leaving the home row.
This is Flexi's extension to the reference application's jump mode and it is the reason jump mode is
worth having in a table-heavy application.

### Adding a target

Implement `jump_targets()` on the screen:

```python
def jump_targets(self) -> Mapping[str, str]:
    return {"clock-module": "c", "records-module": "r", ...}
```

The `Jumper` is rebuilt from this every time the overlay opens, so a target for a
widget that is not mounted simply does not appear — unlike the reference application, where one
global dict lists ids from every screen and the misses are silent.

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
4. **Check the collision test.** `tests/tui/test_bindings.py` asserts that no two
   *shown* bindings active on the same screen use the same key, and that every
   action named by a binding exists as a method. A typo in an action name is
   otherwise silent until a user presses the key.
5. **If it acts on "the thing under the cursor", it belongs on the widget**, not
   the screen — so it is only live when that widget has focus, and the strip
   changes as the user moves. Textual's `active_bindings` does the rest.
