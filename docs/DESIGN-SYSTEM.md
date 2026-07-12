# Design system

Flexi looks like a **time card**. Not a dashboard of equal tiles — a stiff ruled
card with a column of stamped times down it, which is what the thing it replaces
actually was. Every decision below comes from that.

The structure is the design reference's: warm graphite grounds, hairlines instead of boxes,
an overline above every figure, exactly one accent. The identity is Flexi's own:
a teal accent, a categorical day-type scale, and one signature element.

---

## 1. The signature: the punch strip

A single row of cells across the working-day window, filled where you were on the
clock. It is the time card redrawn, it encodes real data rather than decorating
the screen, and it appears at three scales:

```
        07:00        09:00        12:00        15:00        18:00
Mon 08   ─────────────████████████·············█████████────  7:24  +0:00
Tue 09   ─────────█████████████████······███████████████────  8:12  +0:48
Wed 10   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     annual leave
Thu 11   ─────────████████████████▌                    ───    3:10  −4:14
Fri 12   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░     bank holiday
```

| Glyph | Meaning | Colour |
|---|---|---|
| `█` | on the clock | `$c-accent` |
| `·` | a break between two sessions | `$c-ash` |
| `─` | inside the window, never on the clock | `$c-line` |
| `▌` | on the clock *now* (today's live edge) | `$c-accent-lift`, bold |
| `▓` | absence, whole or half | the day-type colour |
| `░` | bank holiday | `$c-holiday` |
| `┊` | where contracted hours would be met | `$c-muted` |

**Adaptive resolution.** The strip renders into whatever width it is handed and
picks the largest bucket from `{5, 10, 15, 20, 30, 60}` minutes that fits. It
never truncates the window; it coarsens. Below 24 columns it degrades to a
three-cell summary (`morning · afternoon · evening`) rather than lying about
precision.

Three placements: full width in an expanded row, one line per day in the records
table (above), and — the reason it is the signature — **seven stacked on one time
axis** in the week view, where the shape of your week becomes legible at a glance
in a way no table of numbers is.

Specified in `flexi/components/punch.py`, tested against fixed
`DayLedger` fixtures with exact expected strings. It is a pure function of
`(ledger, width, window)` → `rich.text.Text`; keeping it pure is what makes it
testable and what lets the same code draw all three placements.

---

## 2. Palette

Declared once, at the top of `src/flexi/theme/flexi.tcss`, and parsed out of that
file into a Textual `Theme` so the names are available in every stylesheet. See
§7 for why.

Values are not eyeballed. Each ramp is a fixed OKLCH hue rendered at a chosen
lightness and chroma, and the day-type scale is validated with the
`dataviz` skill's checker (`scripts/validate_palette.js`) — see §3.

```css
/* Grounds — warm graphite. Terminals are dark; a warm ground gives the cool
   accent something to sit against, and it is not the blue-black every other
   terminal app defaults to. */
$c-ink: #0F0E0D;
$c-surface: #171614;
$c-raised: #201E1B;

/* Hairlines. Editorial layouts are ruled, not boxed. */
$c-line: #2E2B27;
$c-line-soft: #232019;

/* Type. Cream for display, paper for body, muted and ash for everything else. */
$c-cream: #FAF8F4;
$c-paper: #EDE9E3;
$c-muted: #9C948A;
$c-ash: #7A736A;

/* Teal — THE accent. `lift` reads on dark grounds, `deep` is for fills. */
$c-accent: #02A6AD;
$c-accent-lift: #4CDBE3;
$c-accent-deep: #003032;

/* Balance state. The one number, and the only place these appear. */
$c-surplus: #2E9E52;
$c-surplus-lift: #76CF8A;
$c-surplus-deep: #002D0F;
$c-deficit: #CE3E57;
$c-deficit-lift: #FA7F8C;
$c-deficit-deep: #460915;
$c-warning: #C38406;
$c-warning-lift: #F5B34C;
$c-warning-deep: #3A2400;

/* Day types. toil/annual/sick are the validated chart scale; the other three
   are deliberately quieter, because a bank holiday should not compete with a
   sick day for attention. */
$c-toil: #02A6AD;
$c-toil-lift: #4CDBE3;
$c-toil-deep: #003032;
$c-annual: #8459C3;
$c-annual-lift: #BE9DF7;
$c-annual-deep: #2B1844;
$c-sick: #D56326;
$c-sick-lift: #FE9F73;
$c-sick-deep: #411904;
$c-other: #C06099;
$c-other-lift: #EE97C9;
$c-other-deep: #3A142C;
$c-unpaid: #8B7E6D;
$c-unpaid-lift: #B9AC99;
$c-unpaid-deep: #272017;
$c-holiday: #557EA8;
$c-holiday-lift: #89AFD6;
$c-holiday-deep: #0D2339;
```

**One accent per region.** If a panel already carries a teal rule, its button is
quiet. Surplus green, deficit red and warning amber are *state* — they appear
where something is genuinely ahead, behind, or unactioned, and never as
decoration.

---

## 3. The day-type scale, and why it fails the validator on purpose

Three of the six day-type colours are a validated categorical scale. Run:

```
node scripts/validate_palette.js "#00AAAD,#8451C9,#DB703B" --mode dark \
     --surface "#171614" --pairs all
```

All five checks pass, including all-pairs CVD separation (worst pair ΔE 15.7
deutan / 25.6 normal). These three — **TOIL (cyan), annual (violet) and sick
(orange)** — are the only colours that may carry series identity in a chart. A
fourth series folds into a neutral "Other".

TOIL wears the house cyan because TOIL is the application's own currency, and
the accent is what everything else has to be picked around. A blue accent was
tried and rejected: it read too dark against the warm ground, and at hue 255 it
left no room for a violet, which pushed annual leave to magenta.

The accent sits at the very top of the dark lightness band (L 0.67). That is
deliberate — one step lower and the large fills, the primary button and the
punch strip, go muddy.

The remaining three are **not** chart series and deliberately fail the chroma
floor:

- `$c-holiday` and `$c-unpaid` are low-chroma on purpose. They say *not one of
  the things you are tracking*. Giving them chart-grade saturation would make a
  bank holiday compete with a sick day for attention, which is exactly backwards.
- `$c-other` is a real hue — magenta — but sits close to `$c-annual` under
  protanopia. It is legal here and illegal in a chart, because
  in the interface it only ever appears as a one-cell rule beside the literal
  word "Other".

**Do not "fix" this.** A future agent running the validator over all six will get
a FAIL; that FAIL is documented, intended, and load-bearing. The rule that makes
it safe is the one in `docs/README.md`: colour is never the only encoding.

---

## 4. Type

A terminal has one font at one size, so the scale is built from weight, case,
colour and space. Five roles, and no sixth:

| Class | Use | Style |
|---|---|---|
| `.overline` | The label above a figure. Written in upper case in the source — terminal CSS has no `text-transform`. | bold, `$c-muted`, height 1 |
| `.headline` | The one large line a region is about. | bold, `$c-cream` |
| *(body)* | Default. | `$c-paper` |
| `.figure` | A number that is being compared to another number. | bold, `$c-cream` |
| `.caption` | A note under something. | `$c-ash` |

**Scale, where a terminal has none.** The balance is the one number the
application exists to show, so it is drawn with Textual's `Digits` widget —
seven-segment glyphs three rows tall. That is the only place in Flexi where type
gets bigger, and spending the effect there is what makes it read as the headline
rather than as one stat among five.

```
   ┏━┓ ┓  ┏━┓ ╻ ┏━┓
 ┏╋┫ ┃ ┃  ┏━┫ ╹ ┃ ┃      +12:40
   ┗━┛ ┻  ┗━┛ ╹ ┗━┛      FLEXI BALANCE
```

Sign is mandatory and always drawn: `+` in `$c-surplus-lift`, `−` in
`$c-deficit-lift` (U+2212 minus, not a hyphen — it aligns with the digits).
`0:00` is `$c-muted` and unsigned.

---

## 5. Components

`flexi/components/common.py` — the four every screen needs, so no screen invents
its own. Each is a thin wrapper whose whole job is to carry a class from the
stylesheet.

| Component | API | Notes |
|---|---|---|
| `Tone` | `NEUTRAL OK WARN ERR ACCENT` | Shared vocabulary. A screen never writes `"pill--ok"` as a string. |
| `Pill(label, tone)` | `.set_state(label, tone)` | Reports, never acts. A pill with an empty label sets `display = False` — an empty pill is a grey block, not nothing. |
| `StatCard(label, value, note)` | reactive `.value` | Overline / figure / caption. |
| `KeyHint(key, action)` | — | For the few shortcuts a region wants to teach in place. |
| `Rule(label, accent=False)` | — | A hairline with an optional label above it. This is how sections separate. Distinct from `textual.widgets.Rule`. |
| `Gauge(label, low, high, mode)` | `.show(value, target)` | Adapted from the design reference's `Meter`. A track, a fill, and a marker where the target sits, coloured by distance from it. The wallet's allowance bars. |
| `PunchStrip(ledger, window)` | `.set_ledger()` | §1. |

`flexi/components/chrome.py` — the frame, adapted from the design reference:
`Wordmark`, `NavBar` (+`NavItem` table, the single place a screen is registered),
`AppHeader`, `StatusBar`, `KeyStrip`, `AppFooter`.

**`KeyStrip` is not optional.** Textual's stock `Footer` lays out every binding
and lets the terminal edge cut whatever is left, so a screen advertising twelve
keys at 80 columns draws seven and a half — and the ones lost are the last
declared, which is the navigation. `KeyStrip` measures first, keeps whole entries,
and spends its last columns on `+3 more`. Flexi has a lot of bindings; this is
what stops the keyboard experience being a lie. Copy the design reference's
`footer_key_cost` / `keys_that_fit` and their tests verbatim.

---

## 6. Layout

### The module container

Inherited from the reference application, because it is the one part of its look worth keeping and
users of the reference application will recognise it immediately.

```css
.module {
    width: 1fr;
    border: round $c-rule;              /* always present */
    border-title-color: $c-muted;       /* NOT transparent when unfocused */
    background: transparent;            /* glued on, not floating */
    padding: 0 1;
}
.module:focus, .module:focus-within {
    border: round $c-accent;            /* colour changes, border does not appear */
    border-title-color: $c-accent-lift;
}
```

**A module has no ground of its own.** It is a rounded rule drawn *on* the page,
not a lighter rectangle floating above it — which is what makes the reference application's panels
read as part of the application rather than as cards dropped onto it. Everything
inside inherits the same ground, including the records table; a `DataTable` left
on `$c-surface` grows a lighter rectangle in the middle of its own panel.

`$c-surface` and `$c-raised` are then reserved for the few things that genuinely
lift: a modal, a hover, a field, the footer.

**Never add or remove a border on `:focus`.** A border occupies layout space, so
adding one on focus reflows the panel by two cells. Change the colour only. (The
current `components/welcome/style.scss` gets this wrong; it goes.)

Titles are set from Python in `__init__` via
`super().__setattr__("border_title", ...)` — plain assignment routes through
`Static`'s reactive machinery before mount. Textual's defaults put the title left
and the subtitle right, and that asymmetry is the signature; do not override the
alignments.

**A border title is a live data slot.** The clock module's title is
`Clock`; its *subtitle* is the running elapsed time. The wallet's subtitle is the
leave year. This removes a whole summary row per module and is straight out of
the reference application.

### Responsiveness

Terminal CSS has no media query, so the class is the query:

```python
def on_resize(self, event: Resize) -> None:
    self.set_class(event.size.width < 100, "-narrow")
    self.set_class(event.size.width < 64,  "-tiny")
```

Three layouts, driven by the terminal's width — never a widget's own, which is
what keeps a fold from changing the measurement that caused it:

| Width | Dashboard |
|---|---|
| ≥ 100 | Control column (30 cells) left, records right. |
| 64–99 | Stacked: clock + balance strip across the top, records below, wallet and calendar behind tabs. |
| < 64 | Records only, with the balance in the header context slot. Everything else is reachable by jump mode. |

### The dashboard, at width ≥ 100

```
┌──────────────────────────────────────────────────────────────────────────┐
│ flexi·   Dashboard  Insights  Settings          Thu 11 Jun · Week of 8 Jun│
├──────────────────────────────────────────────────────────────────────────┤
│ TODAY ━━━━━━━━━━━━━━━ 6:08 of 7:24   WEEK ━━━━━━━━━━━━━━┿ 29:29 of 25:54 │
│ ╭─ Clock ─────────── 6:08:00 ─╮ ╭─ Records ──────────── 29:29 of 25:54 ─╮ │
│ │ on the clock          [▉ ]  │ │  Day                       Worked   ± │ │
│ │ ───██████████████▌─┊────    │ │  Mon 08  ──███████··██████  8:13 +0:49│ │
│ │ since 08:44 · go home 16:48 │ │  Tue 09  ▓▓▓▓▓▓───█████████ 7:36 +3:54│ │
│ │           Depart            │ │  Wed 10  ────█████··███████ 7:32 +0:08│ │
│ ╰─────────────────────────────╯ │  Thu 11  ───███████··███▌─┊ 6:08 −1:16│ │
│ ╭─ Balance ─── 6 Apr–5 Apr 27 ─╮ │  Fri 12  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  TOIL     │ │
│ │    ╶─╮╭─╮   ╷ ╷╭─╮          │ │  Sat 13  ─────────────────      —     │ │
│ │ ╶┼╴┌─┘│ │ : ╰─┤├─┤          │ │  Sun 14  ─────────────────      —     │ │
│ │    ╰─╴╰─╯     ╵╰─╯          │ │  Week                     29:29 −3:49 │ │
│ │       FLEXI BALANCE         │ │                                       │ │
│ │  20:48 banked · +2.8 days   │ │                                       │ │
│ ╰─────────────────────────────╯ │                                       │ │
│ ╭─ Wallet ──── −3:49 this period ╮                                      │ │
│ │ ANNUAL  20.5 left of 25     │ │                                       │ │
│ │ ━━━━━┿━━━━━━━━━━━━━━━━━━━━━ │ │                                       │ │
│ │ TOIL        +20:48  (+2.8d) │ │                                       │ │
│ │ SICK      1d · 1 occasion   │ │                                       │ │
│ ╰─────────────────────────────╯ │                                       │ │
│ ╭─ Calendar ───── Week of 8 Jun ╮                                       │ │
│ │  ‹      June 2026       ›    │ │                                      │ │
│ │  M  T  W  T  F  S  S         │ ╰───────────────────────────────────────╯ │
│ │  8  9 10 11 12 13 14         │                                          │
│ ╰─────────────────────────────╯                                          │
├──────────────────────────────────────────────────────────────────────────┤
│ Clocked in at 09:12                                          ● on clock  │
│ t Today  p Period  / Clock  v Jump  ? Help  f1 Dashboard  f2 Insights    │
└──────────────────────────────────────────────────────────────────────────┘
```

### The rails

Two bars under the header, flowed rather than docked: **two widgets docked to the
same edge both land on the same row and the later one wins**, so the header is
docked above them and the footer below, which leaves exactly one row.

Progress is **worked against expected**, not wall-clock against the working-day
window. A day started at seven is further through than one started at ten, and
the clock on the wall does not know that.

The period rail relabels itself with the granularity, and disappears below 100
columns — two rails there leave each other about twenty cells, which is a label,
a figure and no bar.

---

## 7. Where the palette lives, and why

Textual scopes CSS variables to the stylesheet that declares them, so a
`$c-accent` written in `flexi.tcss` is invisible to `dashboard.tcss`. The fix,
lifted from the design reference: **parse the palette out of the stylesheet and republish it
through the Textual `Theme`**, which the app broadcasts to every stylesheet.

```python
_PALETTE = re.compile(r"^\s*\$([a-z0-9-]+)\s*:\s*([^;${}]+);", re.MULTILINE)

@cache
def palette(path: Path = THEME_PATH) -> dict[str, str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {name: value.strip() for name, value in _PALETTE.findall(source)}
```

A value containing `$` is skipped deliberately — the parser does no substitution
and a half-resolved colour is worse than an absent one. Add a colour to the
`PALETTE` block and every screen gets it; there is exactly one place a colour is
written down.

Two traps, both from the research notes:

- **An undefined variable fails at startup**, during CSS parse, not at render.
  Anything a stylesheet references must be in the `PALETTE` block or in
  `theme_variables()`.
- **`App.theme = "flexi"` raises `InvalidThemeError` if `register_theme` has not
  run.** Register in `__init__`, not `on_mount` — Flexi pushes the setup screen
  early and `on_mount` is too late.


Flexi ships one theme. `ENABLE_COMMAND_PALETTE` stays **on** (the current app
turns it off, which also disables Textual's theme provider) so the palette can
carry Flexi's own commands — see `ARCHITECTURE.md` §5.

---

## 8. Checklist for a new component

1. Does an existing component do it? `Pill`, `StatCard`, `Rule`, `Gauge`,
   `KeyHint` cover most of it.
2. Does it carry colour that is not also carried by a word or a sign? If so, add
   the word.
3. Does it draw its own border? It should use `.module`, or no border at all.
4. Does `:focus` change anything about its *size*? Fix it.
5. Is there a hardcoded hex anywhere in it? Move it to the palette block.
6. Does it degrade at 64 columns and at 40? Add the `-narrow` rule or say in a
   comment why it does not need one.
7. Is it in the jump target table if it is focusable? See `KEYMAP.md`.
