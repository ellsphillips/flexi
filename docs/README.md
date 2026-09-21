# Flexi — documentation

Flexi is a terminal application for tracking flexitime: when you were on the
clock, how far ahead or behind your contracted hours you are, and what is left
in your leave allowances. [`../README.md`](../README.md) is the tour; these are
the details behind it.

If you are here to use Flexi, [`KEYMAP.md`](KEYMAP.md) is the only one you need.
If you are here to change it, read them in this order:

| Document | What it settles |
|---|---|
| [`TASKS.md`](TASKS.md) | Developer setup and the just recipes for running, checking, building, and preparing releases. |
| [`DOMAIN.md`](DOMAIN.md) | The data model, the period model, and the arithmetic of a flexi balance. Every screen is a view of this. |
| [`DESIGN-SYSTEM.md`](DESIGN-SYSTEM.md) | The palette, the type scale, the component contract, and the rules that keep the interface coherent. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Package layout, layering, and how a keypress becomes a database write and a redraw. |
| [`LEAVE-SCREEN.md`](LEAVE-SCREEN.md) | Why the leave year scrolls instead of paging, and how booking costs one keystroke. |
| [`KEYMAP.md`](KEYMAP.md) | Every binding, jump mode, `config.yaml`, and the rules for adding a key. |
| [`TESTING.md`](TESTING.md) | Domain, service, Pilot and snapshot tests; how to capture a screenshot for review. |
| [`RELEASING.md`](RELEASING.md) | The branch flow, the gates before anything reaches PyPI, and the one-time GitHub setup. |

[`../CHANGELOG.md`](../CHANGELOG.md) is what shipped when.

## Ground rules

1. **The domain does not import Textual.** `flexi/domain/` is pure Python over
   dates and durations. It is the part that has to be right, and it is tested
   without a terminal.
2. **A service owns a session, a widget owns a rectangle.** Widgets never write
   SQL and never hold a `Session` of their own; they call a service through
   `app.services`.
3. **Colour is never the only encoding.** Every coloured rule, cell and bar sits
   beside a word or a signed number. This is an accessibility requirement and it
   is also what makes the interface readable over SSH on a 16-colour terminal.
4. **Rules, not boxes.** Grouping comes from a change of ground and a hairline.
   The only borders in the application are the module containers and the one
   accent rule down the left edge of a focused region.

## Not built yet

Wanted, and absent from 0.2.0:

- `flexi export` — CSV of sessions and absences, and an ICS feed of booked leave.
- `flexi doctor` — database integrity, orphaned sessions, cache age, config
  validation.
- Import from a CSV timesheet.
- A contracted-day setting. The service layer takes one; no screen or command
  offers it, so every install is on 7h 24m.
- A day-of-week profile chart: median start, end and hours worked per weekday.
- Booking a range by dragging in the calendar, as well as `shift` and an arrow.
- A warning as a leave year ends with allowance unspent.
