# Flexi — documentation

Flexi is a terminal application for tracking flexitime: when you were on the
clock, how far ahead or behind your contracted hours you are, and what is left
in your leave allowances.

These documents are the handoff. Read them in this order:

| Document | What it settles |
|---|---|
| [`DOMAIN.md`](DOMAIN.md) | The data model, the period model, and the arithmetic of a flexi balance. Read first — every screen is a view of this. |
| [`DESIGN-SYSTEM.md`](DESIGN-SYSTEM.md) | The palette, the type scale, the component contract, and the rules that keep the interface coherent. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Package layout, layering, how a keypress becomes a database write and a redraw. |
| [`LEAVE-SCREEN.md`](LEAVE-SCREEN.md) | Why the leave year scrolls rather than pages, and how booking costs one keystroke. |
| [`KEYMAP.md`](KEYMAP.md) | Every binding, jump mode, and the rules for adding a new one. |
| [`RELEASING.md`](RELEASING.md) | The branch flow, the four gates before anything reaches PyPI, and the one-time GitHub setup. |
| [`TESTING.md`](TESTING.md) | Unit, Pilot and snapshot testing; how to capture a screenshot for review. |
| [`ROADMAP.md`](ROADMAP.md) | The delivery slices, each with acceptance criteria. Pick up here. |

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
