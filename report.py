"""Branded PDF design report for AWS Landing Zone Studio (fpdf2, pure Python)."""

from datetime import date

from fpdf import FPDF

INK = (35, 47, 62)        # AWS squid ink
AMBER = (255, 153, 0)
AMBER_DARK = (196, 116, 0)
MUTED = (110, 122, 140)
LIGHT = (244, 246, 249)
GREEN = (63, 134, 36)
RED = (176, 8, 77)
WHITE = (255, 255, 255)

_REPLACEMENTS = {
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "•": "*", "…": "...",
    "→": "->", "×": "x", "≈": "~", "≥": ">=", "≤": "<=",
}


def _tx(text: str) -> str:
    """Sanitize text for latin-1 core fonts."""
    s = str(text)
    for k, v in _REPLACEMENTS.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


class _Report(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, "AWS Landing Zone Studio - Design Report", align="L")
        self.cell(0, 6, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 6, "Educational estimates only - validate with an official "
                        "AWS Well-Architected review.", align="C")

    # -- building blocks ----------------------------------------------------

    def title_band(self, subtitle: str):
        self.set_fill_color(*INK)
        self.rect(0, 0, self.w, 42, "F")
        self.set_fill_color(*AMBER)
        self.rect(0, 42, self.w, 1.4, "F")
        self.set_y(10)
        self.set_text_color(*AMBER)
        self.set_font("Courier", "B", 9)
        self.cell(0, 5, "AWS LANDING ZONE STUDIO", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 22)
        self.cell(0, 11, "Landing Zone Design Report", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(200, 208, 218)
        self.cell(0, 6, _tx(subtitle), new_x="LMARGIN", new_y="NEXT")
        self.set_y(52)

    def section(self, label: str):
        if self.get_y() > self.h - 50:
            self.add_page()
        self.ln(3)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*INK)
        self.cell(0, 7, _tx(label), new_x="LMARGIN", new_y="NEXT")
        y = self.get_y()
        self.set_draw_color(*AMBER)
        self.set_line_width(0.6)
        self.line(self.l_margin, y, self.l_margin + 28, y)
        self.set_draw_color(225, 229, 235)
        self.set_line_width(0.3)
        self.line(self.l_margin + 28, y, self.w - self.r_margin, y)
        self.ln(4)

    def kv_table(self, rows: list[tuple[str, str]]):
        self.set_font("Helvetica", "", 9)
        key_w = 62
        for i, (k, v) in enumerate(rows):
            self.set_fill_color(*(LIGHT if i % 2 == 0 else WHITE))
            self.set_text_color(*MUTED)
            self.set_font("Helvetica", "B", 8.5)
            self.cell(key_w, 6.5, _tx(k), fill=True)
            self.set_text_color(*INK)
            self.set_font("Helvetica", "", 9)
            self.multi_cell(0, 6.5, _tx(v), fill=True, new_x="LMARGIN", new_y="NEXT")

    def score_bar(self, label: str, score: int, width: float = 95):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*INK)
        self.cell(58, 7, _tx(label))
        x, y = self.get_x(), self.get_y()
        self.set_fill_color(232, 236, 241)
        self.rect(x, y + 1.6, width, 3.8, "F")
        color = GREEN if score >= 80 else AMBER if score >= 55 else RED
        self.set_fill_color(*color)
        self.rect(x, y + 1.6, width * score / 100, 3.8, "F")
        self.set_x(x + width + 3)
        self.set_font("Courier", "B", 9)
        self.set_text_color(*color)
        self.cell(0, 7, str(score), new_x="LMARGIN", new_y="NEXT")

    def status_chip(self, status: str):
        color = {"pass": GREEN, "warn": AMBER_DARK, "fail": RED}[status]
        text = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[status]
        self.set_fill_color(*color)
        self.set_text_color(*WHITE)
        self.set_font("Courier", "B", 7)
        self.cell(12, 5.5, text, fill=True, align="C")
        self.set_text_color(*INK)


def build_pdf_report(design_dict: dict, scores: dict, cost: dict, n_total: int,
                     assessment: dict, waf_overall: int, guardrails: list,
                     exec_summary: str | None = None) -> bytes:
    pdf = _Report(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.title_band(f"Generated {date.today().isoformat()}  |  "
                   f"{design_dict.get('org_size', '')} profile  |  {n_total} accounts  |  "
                   f"Well-Architected alignment {waf_overall}/100")

    # --- 0. Executive summary (AI-generated, optional) ---
    if exec_summary:
        pdf.section("Executive Summary")
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*INK)
        for para in exec_summary.split("\n"):
            para = para.strip()
            if not para:
                pdf.ln(2)
                continue
            # strip markdown bold/bullets for clean prose rendering
            clean = para.replace("**", "").replace("##", "").replace("# ", "")
            if clean.startswith(("- ", "* ")):
                pdf.set_x(pdf.l_margin + 4)
                clean = "* " + clean[2:]
            pdf.multi_cell(0, 5.2, _tx(clean), new_x="LMARGIN", new_y="NEXT")
        pdf.add_page()

    # --- 1. Design parameters ---
    pdf.section("1. Design Parameters")
    nice = {
        "org_size": "Organization size", "compliance": "Compliance frameworks",
        "num_teams": "Application teams", "num_workloads": "Workloads",
        "environments": "Environments", "regions": "Regions",
        "account_strategy": "Account strategy", "network_pattern": "Network pattern",
        "identity_model": "Identity model", "governance": "Governance tooling",
        "centralized_logging": "Centralized logging", "security_tooling": "Org-wide security tooling",
        "backup_dr": "Centralized backup & DR",
    }
    rows = []
    for k, label in nice.items():
        v = design_dict.get(k)
        if isinstance(v, list):
            v = ", ".join(map(str, v)) or "-"
        elif isinstance(v, bool):
            v = "Yes" if v else "No"
        rows.append((label, str(v)))
    rows.append(("Total AWS accounts", str(n_total)))
    pdf.kv_table(rows)

    # --- 2. Design scorecard ---
    pdf.section("2. Design Scorecard")
    for k, v in scores.items():
        pdf.score_bar(k, v)

    # --- 3. WAF ---
    pdf.section(f"3. Well-Architected Alignment - Overall {waf_overall}/100")
    for pillar, data in assessment.items():
        pdf.score_bar(pillar, data["score"])
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*INK)
    pdf.cell(0, 7, "Findings & remediations", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    for pillar, data in assessment.items():
        non_pass = [c for c in data["checks"] if c["status"] != "pass"]
        if not non_pass:
            continue
        if pdf.get_y() > pdf.h - 45:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6, _tx(pillar.upper()), new_x="LMARGIN", new_y="NEXT")
        for c in non_pass:
            if pdf.get_y() > pdf.h - 35:
                pdf.add_page()
            pdf.status_chip(c["status"])
            pdf.set_x(pdf.l_margin + 15)
            pdf.set_font("Helvetica", "B", 9)
            pdf.multi_cell(0, 5.5, _tx(f"{c['id']} - {c['title']}"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(pdf.l_margin + 15)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*MUTED)
            pdf.multi_cell(pdf.w - pdf.l_margin - pdf.r_margin - 15, 4.6,
                           _tx(c["finding"] + "  Remediation: " + c["remediation"]),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*INK)
            pdf.ln(1.5)

    # --- 4. Cost ---
    pdf.section("4. Estimated Monthly Platform Cost")
    pdf.kv_table([(item, f"${val:,.0f} / month") for item, val in cost["items"].items()]
                 + [("TOTAL (estimate)", f"${cost['total']:,.0f} / month")])

    # --- 5. Guardrails ---
    pdf.section("5. Recommended Guardrails (SCPs)")
    for name, mech, scope in guardrails:
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*INK)
        pdf.cell(0, 5.5, _tx(f"* {name}  ({scope})"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.set_x(pdf.l_margin + 4)
        pdf.multi_cell(0, 4.6, _tx(mech), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*INK)
        pdf.ln(0.8)

    return bytes(pdf.output())
