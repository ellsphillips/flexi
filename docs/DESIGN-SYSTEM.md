# Design system

Flexi looks like a **time card**. Not a dashboard of equal tiles — a stiff ruled
card with a column of stamped times down it, which is what the thing it replaces
actually was. Every decision below comes from that.

Warm graphite grounds, hairlines instead of boxes, an overline above every
figure, exactly one accent. The identity:
a cyan accent, a categorical day-type scale, and one signature element.

---

## 1. The signature: the punch strip

A single row of cells across the working-day window, filled where you were on the
clock. It is the time card redrawn, it encodes real data instead of decorating
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
| `▒` | recorded after the fact, never clocked | `$c-accent` |
| `·` | a break between two sessions | `$c-ash` |
| `─` | inside the window, never on the clock | `$c-line` |
| `▌` | on the clock *now* (today's live edge) | `$c-accent-lift`, bold |
| `▓` | absence, whole or half | the day-type colour |
| `░` | bank holiday | `$c-holiday` |
| `┊` | where contracted hours would be met | `$c-muted` |

The glyphs are `flexi.theme.CELL_GLYPHS`, keyed by `flexi.domain.punch.Cell`, and
the order of that enum is the precedence order: a later state overwrites an
earlier one on the same cell.

**Adaptive resolution.** The strip renders into whatever width it is handed and
takes the finest bucket from `BUCKET_SIZES` — `{5, 10, 15, 20, 30, 60}` minutes —
that fits it. It never truncates the window; it coarsens. Below `MIN_CELLS` (12
columns) it falls back to a three-cell summary, which claims no precision it
cannot draw.

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
lightness and chroma, and the three colours that carry series identity in a chart
were checked for contrast against `$c-surface` and for pairwise separation under
deutan and protan simulation — see §3.

```css
/* Grounds — warm graphite. Terminals are dark; a warm ground gives the cool
   accent something to sit against, and it is not the blue-black every other
   terminal app defaults to.

   `ink` is the page AND the modules: a panel is a rounded rule drawn *on* the
   page, not a lighter rectangle floating above it. `surface` and `raised` are
   for the few things that genuinely lift — a modal, a hover, a field. */
$c-ink: #0F0E0D;
$c-surface: #171614;
$c-raised: #201E1B;

/* Hairlines. Editorial layouts are ruled, not boxed. */
$c-line: #2E2B27;
$c-line-soft: #232019;
$c-rule: #443F38;

/* Type. Cream for display, paper for body, muted and ash for everything else. */
$c-cream: #FAF8F4;
$c-paper: #EDE9E3;
$c-muted: #9C948A;
$c-ash: #7A736A;

/* Cyan — THE accent. `lift` reads on dark grounds, `deep` is for fills. */
$c-accent: #00AAAD;
$c-accent-lift: #4CDCDF;
$c-accent-deep: #003031;

/* Balance state. Green for ahead, red for behind — the one number, and the only
   place these appear. */
$c-surplus: #2E9E52;
$c-surplus-lift: #76CF8A;
$c-surplus-deep: #003010;
$c-deficit: #CE3E5D;
$c-deficit-lift: #FE7B90;
$c-deficit-deep: #4B0519;
$c-warning: #C38406;
$c-warning-lift: #F5B34C;
$c-warning-deep: #3A2400;

/* Day types. toil/annual/sick are the validated chart scale — cyan, violet and
   orange, which clear an all-pairs colour-vision check. The other three are
   quieter, because a bank holiday should not compete with a sick
   day for attention. */
$c-toil: #00AAAD;
$c-toil-lift: #4CDCDF;
$c-toil-deep: #003031;
$c-annual: #8451C9;
$c-annual-lift: #C4A4FE;
$c-annual-deep: #2E194B;
$c-sick: #DB703B;
$c-sick-lift: #FFA47A;
$c-sick-deep: #471800;
$c-other: #BE5BAC;
$c-other-lift: #EA97D8;
$c-other-deep: #3D1436;
$c-unpaid: #8B7E6D;
$c-unpaid-lift: #BDAF9D;
$c-unpaid-deep: #2A2319;
$c-holiday: #647D97;
$c-holiday-lift: #97B1CD;
$c-holiday-deep: #142537;
```

**One accent per region.** If a panel already carries a cyan rule, its button is
quiet. Surplus green, deficit red and warning amber are *state* — they appear
where something is genuinely ahead, behind, or unactioned, and never as
decoration.

---

## 3. The day-type scale, and why only three of six are chart colours

Three of the six day-type colours are a categorical chart scale: **TOIL (cyan
`#00AAAD`), annual (violet `#8451C9`) and sick (orange `#DB703B`)**. Against the
`$c-surface` ground `#171614` all three clear the contrast floor, and under
all-pairs colour-vision-deficiency simulation the worst pair separates by ΔE 15.7
deutan (25.6 unsimulated). They are the only colours that may carry series
identity in a chart; a fourth series folds into a neutral "Other".

The method, so any public OKLCH tool can re-check it: fixed hue, chosen
lightness and chroma, all-pairs ΔE under deutan and protan simulation against
that surface.

TOIL wears the house cyan because TOIL is the application's own currency, and
the accent is what everything else has to be picked around. A blue accent was
tried and rejected: it read too dark against the warm ground, and at hue 255 it
left no room for a violet, which pushed annual leave to magenta.

The accent sits at the very top of the dark lightness band (L 0.67). One step
lower and the large fills — the primary button and the punch strip — go muddy.

The remaining three are **not** chart series, and they fail the chroma floor by
design:

- `$c-holiday` and `$c-unpaid` are low-chroma. They say *not one of the things
  you are tracking*. Giving them chart-grade saturation would make a
  bank holiday compete with a sick day for attention, which is exactly backwards.
- `$c-other` is a real hue — magenta — but sits close to `$c-annual` under
  protanopia. It is legal here and illegal in a chart, because
  in the interface it only ever appears as a one-cell rule beside the literal
  word "Other".

**Do not "fix" this.** Anyone re-checking all six against the chart-series
criteria will find the last three fail the chroma floor, and that is the intent.
The rule that makes it safe is the one in [`README.md`](README.md): colour is
never the only encoding.

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
and not as one stat among five.

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

`flexi/components/common.py` holds the pieces every screen needs, so no screen
invents its own. Most are a thin wrapper whose whole job is to carry a class from
the stylesheet.

| Component | API | Notes |
|---|---|---|
| `Tone` | `NEUTRAL OK WARN ERR ACCENT` | Shared vocabulary. A screen never writes `"pill--ok"` as a string. |
| `Pill(label, tone)` | `.set_state(label, tone)` | Reports, never acts. A pill with an empty label sets `display = False` — an empty pill is a grey block, not nothing. |
| `StatCard(label, value, note)` | reactive `.value` | Overline / figure / caption. |
| `KeyHint(key, action)` | — | For the few shortcuts a region wants to teach in place. |
| `Rule(label, accent=False)` | — | A hairline with an optional label above it. This is how sections separate. Distinct from `textual.widgets.Rule`. |
| `Gauge` | `.show(...)` | A track, a fill, and a marker where the target sits. The caller passes the `Tone`, because whether a reading is good news is a question about leave policy, not about bars. The wallet's allowance bars. |
| `EmptyIndicator(message)` | — | What a panel says when there is nothing in it. |

Three more live beside it: `PunchStrip` in `punch.py` (§1), and `ProgressRail`
and `TimeProgress` in `progress.py`. A rail draws overshoot instead of clipping
it — a ten-hour day against a seven-hour contract is the most interesting thing
this application can tell you, and a bar that stopped at full would say it was
ordinary.

`flexi/components/chrome.py` — the frame:
`Lockup`, `NavBar` (+`NavItem` table, the single place a screen is registered),
`AppHeader`, `StatusBar`, `KeyStrip`, `AppFooter`.

**`KeyStrip` is not optional.** Textual's stock `Footer` lays out every binding
and lets the terminal edge cut whatever is left, so a screen advertising twelve
keys at 80 columns draws seven and a half — and the ones lost are the last
declared, which is the navigation. `KeyStrip` measures first, keeps whole entries,
and spends its last columns on `+3 more`. Flexi has a lot of bindings; this is
what stops the keyboard experience being a lie. `footer_key_cost` and
`keys_that_fit` are measured, and tested directly.

---

## 6. Layout

### The module container

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
not a lighter rectangle floating above it — which is what makes a panel read as
part of the application and not as a card dropped onto it. Everything
inside inherits the same ground, including the records table; a `DataTable` left
on `$c-surface` grows a lighter rectangle in the middle of its own panel.

`$c-surface` and `$c-raised` are then reserved for the few things that genuinely
lift: a modal, a hover, a field, the footer.

**Never add or remove a border on `:focus`.** A border occupies layout space, so
adding one on focus reflows the panel by two cells. Change the colour only.

Titles are set from Python in `__init__` via
`super().__setattr__("border_title", ...)` — plain assignment routes through
`Static`'s reactive machinery before mount. Textual's defaults put the title left
and the subtitle right, and that asymmetry is the signature; do not override the
alignments.

**A border title is a live data slot.** The clock module's title is
`Clock`; its *subtitle* is the running elapsed time. The wallet's subtitle is the
leave year. This removes a whole summary row per module.

### Responsiveness

Terminal CSS has no media query, so the class is the query:

```python
def on_resize(self) -> None:
    mark_width(self, self.size.width)  # sets -narrow under 100, -tiny under 64
```

`mark_width` is in `components/common.py` and the thresholds are
`NARROW_COLUMNS = 100` and `TINY_COLUMNS = 64`. The screen's own stylesheet says
what the classes mean for it. Always the terminal's width, never the widget's
own, which is what keeps a fold from changing the measurement that caused it.

| Width | Dashboard |
|---|---|
| ≥ 100 | Control column (34 cells) on the left, records on the right. |
| 64–99 | Stacked: clock, balance and wallet in a row across the top, records below. The calendar is hidden. |
| < 64 | Balance and wallet go too, leaving the clock strip and records. The rest is one jump away. |

### The dashboard, at width ≥ 100

The committed render is [`shots/dashboard-wide.txt`](shots/dashboard-wide.txt),
and the narrow and tiny cases sit beside it. Those files are regenerated by
`scripts/shoot.py` and asserted byte-for-byte by the snapshot tests, so they
cannot drift from what the application draws; a sketch in this document could.

The arrangement they show: header with the nav and the period label, two progress
rails, a control column of Clock / Balance / Wallet / Calendar on the left,
Records taking the rest, and the status bar over the key strip.

### The rails

Two bars under the header, flowed and not docked: **two widgets docked to the
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
`$c-accent` written in `flexi.tcss` is invisible to `dashboard.tcss`. The fix:
**parse the palette out of the stylesheet and republish it through the Textual
`Theme`**, which the app broadcasts to every stylesheet.

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

A value containing `$` is skipped: the parser does no substitution, and a
half-resolved colour is worse than an absent one. Add a colour to the
`PALETTE` block and every screen gets it; there is exactly one place a colour is
written down.

Three traps:

- **An undefined variable fails at startup**, during CSS parse, not at render.
  Anything a stylesheet references must be in the `PALETTE` block or in
  `theme_variables()`.
- **`App.theme = "flexi"` raises `InvalidThemeError` if `register_theme` has not
  run.** Register in `__init__`, not `on_mount` — Flexi pushes the setup screen
  early and `on_mount` is too late.
- **`hatch:` breaks on a variable that is both declared in the stylesheet and
  published through the theme.** On Textual 8.2.8 the value is substituted twice
  and the property sees four tokens where it wants two or three; every other
  property survives it, which is why it looks arbitrary until you hit it. So the
  two hatch colours are **theme-only** tokens — `$c-hatch-empty` and
  `$c-hatch-jump`, derived from the palette in `theme_variables()` and
  absent from `flexi.tcss`. A new `hatch:` rule needs the same
  treatment.

Flexi ships one theme. `ENABLE_COMMAND_PALETTE` stays **on** so the palette can
carry Flexi's own commands — see [`ARCHITECTURE.md`](ARCHITECTURE.md) §5.

---

## 8. Charts

The five Insights panels are drawn character by character, with no plotting
library. Three rules hold across them:

- **Series colours come from the three chart slots only** — `$c-toil`,
  `$c-annual`, `$c-sick` (§3). A fourth series folds into a neutral "Other".
- **Whole cells, both arms.** Eighths read beautifully upward (`▁▂▃▄▅▆▇`) and
  need U+1FB0x Symbols for Legacy Computing to do the same downward. Most
  terminal fonts do not carry those, so a diverging bar uses whole cells in both
  directions.
- **A chart stops at today.** Every working day after it expects hours and has
  none recorded, so charting the rest of a leave year draws a cliff of deficits
  for days that have not happened yet.

Sequential ramps are one hue, light to dark. The year heatmap is diverging: two
poles with a grey midpoint, never a rainbow. `DIVERGING_STEPS` is four per arm,
each a fixed OKLCH hue at rising lightness, chosen for monotonic steps and
contrast against the surface.

The line chart in the running-balance panel is Braille (`flexi/domain/plot.py`):
each cell carries a two-by-four dot grid, so forty cells is eighty positions
across.

---

## 9. Checklist for a new component

1. Does an existing component do it? `Pill`, `StatCard`, `Rule`, `Gauge`,
   `KeyHint` cover most of it.
2. Does it carry colour that is not also carried by a word or a sign? If so, add
   the word.
3. Does it draw its own border? It should use `.module`, or no border at all.
4. Does `:focus` change anything about its *size*? Fix it.
5. Is there a hardcoded hex anywhere in it? Move it to the palette block.
6. Does it degrade below 100 columns and below 64? Add the `-narrow` and
   `-tiny` rules, or say in a comment why it does not need them.
7. Is it in the jump target table if it is focusable? See `KEYMAP.md`.
