"""Single source of truth for the Studio colour palette + WAF pillar colours.

Pure leaf module (no imports) so any module can depend on it. Values are the
exact hexes previously duplicated across arch_diagram / gv_diagrams /
interactive_diagrams — consumers import the names they need (and alias where a
module historically used a different spelling, e.g. gv_diagrams' BLUE == NAVY).
"""

# Backgrounds / neutrals
INK = "#0B1422"        # arch_diagram deep canvas
INKT = "#0B1220"
PANEL = "#101A2C"
LINE = "#2A3852"
DARK = "#232F3E"       # AWS "squid ink" (gv_diagrams DARK / interactive_diagrams INK)

# Accents
AMBER = "#FF9900"      # AWS orange
TEAL = "#2DD4BF"
NAVY = "#1A476F"
GREEN = "#3F8624"
RED = "#B0084D"
BLUE = "#5B8DEF"
GREY = "#6E7A8C"
GREY_GV = "#5A6B86"    # gv_diagrams' slightly cooler grey
MUT = "#8C9CB8"

# AWS Well-Architected pillars -> (label, colour)
WAF_PILLARS = {
    "OE":   ("Operational Excellence", AMBER),
    "SEC":  ("Security", RED),
    "REL":  ("Reliability", TEAL),
    "PE":   ("Performance Efficiency", BLUE),
    "COST": ("Cost Optimization", GREEN),
    "SUS":  ("Sustainability", MUT),
}
