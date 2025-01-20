from textual.theme import Theme

THEME = Theme(
    name="flexi",
    primary="#38bdf8",
    secondary="#0891b2",
    accent="#0d9488",
    foreground="#D8DEE9",
    background="#1d1d1d",
    success="#A3BE8C",
    warning="#EBCB8B",
    error="#BF616A",
    surface="#3B4252",
    panel="#434C5E",
    dark=True,
    variables={
        "block-cursor-text-style": "none",
        "footer-key-foreground": "#88C0D0",
        "input-selection-background": "#0d9488 35%",
    },
)
