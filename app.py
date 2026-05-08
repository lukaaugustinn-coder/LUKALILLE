import os
import sys
import json
import math
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

# KINOVA SYNC — push anonymisé vers le partage équipe (ARGUS)
from kinova_sync import push_state_async


def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


TEMPLATE_DIR = resource_path("templates")
STATIC_DIR = resource_path("static")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

APP_FOLDER_NAME = "T850_VAD_LukaA"


def get_downloads_dir() -> str:
    home = os.path.expanduser("~")
    for p in [
        os.path.join(home, "Downloads"),
        os.path.join(home, "Téléchargements"),
        os.path.join(home, "Download"),
    ]:
        if os.path.isdir(p):
            return p
    return os.path.join(home, "Downloads")


def get_app_data_dir() -> str:
    out_dir = os.path.join(get_downloads_dir(), APP_FOLDER_NAME)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def get_state_path() -> str:
    return os.path.join(get_app_data_dir(), "state.json")


def default_state() -> dict:
    return {
        "version": 2,
        "sales": [],
        "rebonds": [],
        "currentClient": None,
        "catalogCustom": {},
        "catalogOverrides": {},
        "catalogDeleted": {},
        "sgAgenda": 0,
    }


def load_state() -> dict:
    path = get_state_path()
    if not os.path.exists(path):
        s = default_state()
        save_state(s)
        return s
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = json.load(f)
        if not isinstance(s, dict):
            s = default_state()
        s.setdefault("version", 2)
        s.setdefault("sales", [])
        s.setdefault("rebonds", [])
        s.setdefault("currentClient", None)
        s.setdefault("catalogCustom", {})
        s.setdefault("catalogOverrides", {})
        s.setdefault("catalogDeleted", {})
        if not isinstance(s["sales"], list): s["sales"] = []
        if not isinstance(s["rebonds"], list): s["rebonds"] = []
        if not isinstance(s["catalogCustom"], dict): s["catalogCustom"] = {}
        if not isinstance(s["catalogOverrides"], dict): s["catalogOverrides"] = {}
        if not isinstance(s["catalogDeleted"], dict): s["catalogDeleted"] = {}
        return s
    except Exception:
        s = default_state()
        save_state(s)
        return s


def save_state(state: dict) -> None:
    with open(get_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    # KINOVA SYNC — push anonymisé non bloquant vers le partage équipe
    push_state_async(state)


def safe_str(x) -> str:
    return "" if x is None else str(x)


def parse_sale_datetime(item: dict):
    if not isinstance(item, dict):
        return None
    iso = item.get("dt")
    if isinstance(iso, str) and iso.strip():
        try:
            return datetime.fromisoformat(iso.strip())
        except Exception:
            pass
    disp = item.get("dt_display")
    if isinstance(disp, str) and disp.strip():
        try:
            return datetime.strptime(disp.strip(), "%d/%m/%Y %H:%M")
        except Exception:
            pass
    return None


def compute_category_stats(items: list) -> dict:
    counts: dict[str, int] = {}
    for s in items or []:
        cat = safe_str((s or {}).get("category")).strip() or "—"
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def compute_time_bins_stats(sales: list) -> list[dict]:
    bins = [
        ("08:00-10:00", 8, 10),
        ("10:00-12:00", 10, 12),
        ("12:00-14:00", 12, 14),
        ("14:00-16:00", 14, 16),
        ("16:00-18:00", 16, 18),
        ("18:00-20:00", 18, 20),
    ]
    counts = {label: 0 for (label, _, _) in bins}
    other = 0
    for s in sales or []:
        dt = parse_sale_datetime(s)
        if not dt:
            other += 1
            continue
        h = dt.hour + dt.minute / 60.0
        placed = False
        for label, start, end in bins:
            if start <= h < end:
                counts[label] += 1
                placed = True
                break
        if not placed:
            other += 1
    total = len(sales or [])
    out = []
    for label, _, _ in bins:
        c = counts[label]
        pct = (c / total * 100.0) if total > 0 else 0.0
        out.append({"label": label, "count": c, "pct": pct})
    out.append({"label": "Autres", "count": other, "pct": (other / total * 100.0) if total > 0 else 0.0})
    return out


PIE_PALETTE = [
    # 12 distinct pastel colours — each visually different
    colors.HexColor("#FFB3B3"),  # pastel red
    colors.HexColor("#FFD9A0"),  # pastel orange
    colors.HexColor("#FFF4A0"),  # pastel yellow
    colors.HexColor("#B8F0B8"),  # pastel green
    colors.HexColor("#A8DFF0"),  # pastel sky blue
    colors.HexColor("#B3B8FF"),  # pastel indigo
    colors.HexColor("#E0B3FF"),  # pastel violet
    colors.HexColor("#FFB3E6"),  # pastel pink
    colors.HexColor("#B3FFE6"),  # pastel mint
    colors.HexColor("#FFD6B3"),  # pastel peach
    colors.HexColor("#C8F0A0"),  # pastel lime
    colors.HexColor("#F0C8A0"),  # pastel tan
]


def clean_cat_label(raw: str) -> str:
    """Strip emoji, keep readable ASCII/latin text only."""
    out = []
    for ch in raw:
        cp = ord(ch)
        # Keep basic latin, latin extended, spaces, hyphens
        if cp < 0x0300 or ch in (' ', '-', '_', '.', '/'):
            out.append(ch)
    result = ''.join(out).strip()
    return result if result else raw[:12]


def draw_pie_chart(c, cx, cy, r, items, total, palette):
    """
    Professional pie chart with guaranteed-visible external labels.

    Strategy: split labels into LEFT column and RIGHT column.
    Each column is a stacked list with fixed vertical spacing → zero overlap.
    A leader line connects each label to its slice midpoint on the pie edge.
    """
    if total <= 0 or not items:
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.HexColor("#6E6E6E"))
        c.drawString(cx - r, cy, "Aucune vente à analyser.")
        return []

    INK_PDF   = colors.HexColor("#0F1B2D")
    MUTED_PDF = colors.HexColor("#5A7190")

    legend = []
    for i, (label, count) in enumerate(items):
        col = palette[i % len(palette)]
        pct = (count / total) * 100.0
        legend.append((col, label, count, pct))

    # ── 1. Draw slices + inside % ────────────────
    start_angle = 90.0
    mid_angles = []
    for col, _label, count, _pct in legend:
        sweep = (count / total) * 360.0
        end_angle = start_angle - sweep
        mid_a = (start_angle + end_angle) / 2.0
        mid_angles.append(mid_a)

        c.setFillColor(col)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.0)
        p = c.beginPath()
        p.moveTo(cx, cy)
        p.arc(cx - r, cy - r, cx + r, cy + r, startAng=end_angle, extent=sweep)
        p.close()
        c.drawPath(p, stroke=1, fill=1)

        # % inside slice
        if sweep >= 24:
            rad = math.radians(mid_a)
            tx = cx + r * 0.62 * math.cos(rad)
            ty = cy + r * 0.62 * math.sin(rad)
            c.setFont("Helvetica-Bold", 7.5)
            c.setFillColor(INK_PDF)
            c.drawCentredString(tx, ty - 3, f"{_pct:.0f}%")

        start_angle = end_angle

    # Outer ring
    c.setStrokeColor(colors.HexColor("#B0C4D8"))
    c.setLineWidth(0.6)
    c.circle(cx, cy, r, stroke=1, fill=0)

    # ── 2. Split items into LEFT / RIGHT columns ──
    LINE_H    = 11      # vertical spacing between label rows (pt)
    FONT_SZ   = 7.0
    COL_MARGIN = r + 22  # horizontal distance from center to column edge

    left_items  = []   # (idx, col, label, pct, mid_angle)
    right_items = []

    for i, (col, label, count, pct) in enumerate(legend):
        mid_a = mid_angles[i]
        rad   = math.radians(mid_a)
        cos_a = math.cos(rad)
        # right half: cos > 0  (angles -90..90 → right side of pie)
        if cos_a >= 0:
            right_items.append((i, col, label, pct, mid_a))
        else:
            left_items.append((i, col, label, pct, mid_a))

    def assign_column_y(items_list, side):
        """
        Assign fixed Y positions for a column of labels.
        side = 'right' or 'left'
        Returns list of (i, col, label, pct, mid_angle, label_y)
        """
        n = len(items_list)
        if n == 0:
            return []
        total_h = (n - 1) * LINE_H
        # Center the block vertically around cy
        y_start = cy + total_h / 2.0
        result = []
        def sort_key(item):
            mid_a = item[4]
            rad = math.radians(mid_a)
            return -math.sin(rad)  # higher sin = higher on page

        sorted_items = sorted(items_list, key=sort_key)
        for row_idx, item in enumerate(sorted_items):
            y = y_start - row_idx * LINE_H
            result.append((*item, y))
        return result

    right_placed = assign_column_y(right_items, 'right')
    left_placed  = assign_column_y(left_items,  'left')

    # ── 3. Draw leader lines + labels ────────────
    def draw_label_item(i, col, label, pct, mid_a, label_y):
        mid_rad = math.radians(mid_a)
        cos_a   = math.cos(mid_rad)
        sin_a   = math.sin(mid_rad)

        # Point on pie edge
        px = cx + r * cos_a
        py = cy + r * sin_a

        # Elbow point (slightly outside pie)
        elbow_r = r + 10
        ex = cx + elbow_r * cos_a
        ey = cy + elbow_r * sin_a

        # Column X — clamp right side to stay within pie zone (legend panel is at x>315)
        if cos_a >= 0:
            col_x = min(cx + COL_MARGIN, cx + r + 28)
        else:
            col_x = cx - COL_MARGIN

        # Leader: pie edge → elbow → horizontal to column
        c.setStrokeColor(col)
        c.setLineWidth(0.7)
        c.line(px, py, ex, ey)
        c.line(ex, ey, col_x, label_y + 3)

        # Dot at column end
        c.setFillColor(col)
        c.circle(col_x, label_y + 3, 1.8, fill=1, stroke=0)

        # Label text
        clean = clean_cat_label(safe_str(label))
        short = clean[:18]
        disp  = f"{short}  {pct:.0f}%"

        c.setFont("Helvetica-Bold", FONT_SZ)
        c.setFillColor(INK_PDF)
        if cos_a >= 0:
            c.drawString(col_x + 5, label_y, disp)
        else:
            c.drawRightString(col_x - 5, label_y, disp)

    for item in right_placed:
        draw_label_item(*item)
    for item in left_placed:
        draw_label_item(*item)

    return legend


def compute_product_conversion_table(sales: list, rebonds: list):
    sales_counts = {}
    reb_counts = {}
    for s in sales or []:
        p = safe_str((s or {}).get("product")).strip() or "—"
        sales_counts[p] = sales_counts.get(p, 0) + 1
    for r in rebonds or []:
        p = safe_str((r or {}).get("product")).strip() or "—"
        reb_counts[p] = reb_counts.get(p, 0) + 1
    products = set(sales_counts.keys()) | set(reb_counts.keys())
    rows = []
    for p in products:
        rb = int(reb_counts.get(p, 0))
        sl = int(sales_counts.get(p, 0))
        conv = (sl / rb * 100.0) if rb > 0 else None
        rows.append((p, rb, sl, conv))
    rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return rows


# =========================
# PDF export — Silver theme
# =========================

def compute_multi_vad(sales: list) -> dict:
    """
    Multi VAD = ventes consécutives sur le même client.
    Un groupe de N ventes consécutives sur le même client = N ventes en multi.
    Retourne: { count_multi, count_solo, total, pct_multi }
    """
    if not sales:
        return {"count_multi": 0, "count_solo": 0, "total": 0, "pct_multi": 0.0}

    def client_key(s):
        c = s.get("client") or {}
        nom    = safe_str(c.get("nom", "")).strip().lower()
        prenom = safe_str(c.get("prenom", "")).strip().lower()
        return f"{prenom}|{nom}" if (nom or prenom) else ""

    count_multi = 0
    i = 0
    while i < len(sales):
        key = client_key(sales[i])
        if not key:  # pas de client = solo
            i += 1
            continue
        j = i + 1
        while j < len(sales) and client_key(sales[j]) == key:
            j += 1
        group_size = j - i
        if group_size >= 2:
            count_multi += group_size
        i = j

    count_solo = len(sales) - count_multi
    total      = len(sales)
    pct_multi  = round(count_multi / total * 100, 1) if total > 0 else 0.0
    return {
        "count_multi": count_multi,
        "count_solo":  count_solo,
        "total":       total,
        "pct_multi":   pct_multi,
    }

def export_pdf(general: int, sales: list, rebonds: list, title: str = "RECAP VENTES", sg_agenda: int = 0) -> str:
    out_dir = get_app_data_dir()
    now = datetime.now()
    path = os.path.join(out_dir, f"t850_vad_{now.strftime('%Y-%m-%d_%H-%M')}.pdf")

    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4

    # ── Silver palette ───────────────────────────
    INK      = colors.HexColor("#0F1B2D")      # near-black navy
    SILVER   = colors.HexColor("#C9D6E3")      # silver-blue
    SILVER_L = colors.HexColor("#E8EEF4")      # light silver
    SILVER_D = colors.HexColor("#8A9BB0")      # muted silver
    ACCENT   = colors.HexColor("#1E3A5F")      # deep blue accent
    ROW_ALT  = colors.HexColor("#F4F7FA")      # alternating row tint
    ROW_HDR  = colors.HexColor("#1E3A5F")      # header row fill
    GREEN    = colors.HexColor("#1A6B3A")
    AMBER    = colors.HexColor("#7A5C00")
    RED      = colors.HexColor("#8B1A1A")
    WHITE    = colors.white

    def draw_header_block(heading: str, subtitle: str = ""):
        """
        Professional 3-zone header — zero overlap guaranteed.
        Zone A (left, 110px): T850 branding
        Zone B (center, flexible): page title + subtitle
        Zone C (right, 110px): date/time
        Total height: 60px.  Content starts at H-80.
        """
        HDR_H = 60

        # ── Background ──────────────────────────────
        c.setFillColor(INK)
        c.rect(0, H - HDR_H, W, HDR_H, fill=1, stroke=0)

        # Left gold accent stripe
        c.setFillColor(colors.HexColor("#C9A000"))
        c.rect(0, H - HDR_H, 4, HDR_H, fill=1, stroke=0)

        # ── ZONE A : T850 branding (x: 16..115) ────
        ZONE_A_X = 16
        # "T850" large
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(ZONE_A_X, H - 28, "T850")
        # "COMPTEUR VAD" small below
        c.setFillColor(SILVER_D)
        c.setFont("Helvetica", 7.5)
        c.drawString(ZONE_A_X, H - 40, "COMPTEUR VAD")
        # Thin dot separator between zones
        c.setFillColor(colors.HexColor("#3A5070"))
        c.rect(118, H - HDR_H + 8, 1, HDR_H - 16, fill=1, stroke=0)

        # ── ZONE B : page title (x: 128..W-120) ────
        ZONE_B_X = 130
        ZONE_B_MAX_W = W - 120 - ZONE_B_X  # leave space for zone C
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 12)
        # Truncate heading if too long
        heading_disp = heading[:55] if heading else ""
        c.drawString(ZONE_B_X, H - 26, heading_disp)
        if subtitle:
            c.setFillColor(SILVER_D)
            c.setFont("Helvetica", 7.5)
            # Truncate subtitle
            sub_disp = subtitle[:80] if subtitle else ""
            c.drawString(ZONE_B_X, H - 38, sub_disp)

        # ── ZONE C : date/time (right-aligned, x: W-110..W-12) ─
        c.setFillColor(SILVER_D)
        c.setFont("Helvetica", 8)
        c.drawRightString(W - 14, H - 26, now.strftime("%d/%m/%Y"))
        c.drawRightString(W - 14, H - 38, now.strftime("%H:%M"))

        # ── Bottom accent line ───────────────────────
        c.setFillColor(colors.HexColor("#2A4060"))
        c.rect(0, H - HDR_H - 1, W, 1, fill=1, stroke=0)

    def draw_footer(page_num: int = 0):
        c.setFillColor(SILVER_L)
        c.rect(0, 0, W, 34, fill=1, stroke=0)
        c.setFillColor(SILVER_D)
        c.setFont("Helvetica", 7.5)
        c.drawString(20, 12, "T850 — Compteur VAD — Un logiciel KINOVA  ·  By. Luka Augustin")
        if page_num:
            c.drawRightString(W - 20, 12, f"Page {page_num}")

    def section_title(title_text: str, y_pos: float) -> float:
        """Draw a section title bar, return new y below it."""
        c.setFillColor(ACCENT)
        c.rect(20, y_pos - 4, W - 40, 18, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(28, y_pos + 1, title_text.upper())
        return y_pos - 24

    def table_header(cols: list, y_pos: float) -> float:
        """
        cols: list of (label, x, width, align)
        Draw header row, return new y below it.
        """
        row_h = 16
        c.setFillColor(ROW_HDR)
        c.rect(20, y_pos - 2, W - 40, row_h, fill=1, stroke=0)
        c.setFillColor(SILVER)
        c.setFont("Helvetica-Bold", 8)
        for label, x, w, align in cols:
            if align == "right":
                c.drawRightString(x + w, y_pos + 3, label)
            elif align == "center":
                c.drawCentredString(x + w // 2, y_pos + 3, label)
            else:
                c.drawString(x, y_pos + 3, label)
        return y_pos - row_h - 2

    def table_row(cols_data: list, y_pos: float, row_index: int, col_defs: list) -> float:
        """Draw one data row. cols_data: list of (text, color_or_None)"""
        row_h = 14
        if row_index % 2 == 0:
            c.setFillColor(ROW_ALT)
            c.rect(20, y_pos - 2, W - 40, row_h, fill=1, stroke=0)
        c.setFont("Helvetica", 8)
        for (text, txt_color), (label, x, w, align) in zip(cols_data, col_defs):
            c.setFillColor(txt_color if txt_color else INK)
            if align == "right":
                c.drawRightString(x + w, y_pos + 2, str(text)[:40])
            elif align == "center":
                c.drawCentredString(x + w // 2, y_pos + 2, str(text)[:40])
            else:
                c.drawString(x, y_pos + 2, str(text)[:50])
        # subtle bottom border
        c.setStrokeColor(SILVER_L)
        c.setLineWidth(0.4)
        c.line(20, y_pos - 2, W - 20, y_pos - 2)
        return y_pos - row_h

    def fmt_client(cli: dict) -> str:
        cli = cli or {}
        nom = safe_str(cli.get("nom")).strip()
        prenom = safe_str(cli.get("prenom")).strip()
        naissance = safe_str(cli.get("naissance")).strip()
        full = f"{prenom} {nom}".strip() or "—"
        return f"{full}  ({naissance})" if naissance else full

    # ── Column defs for sales table ──────────────
    # (label, x_start, width, align)
    SALES_COLS = [
        ("#",          20,  18, "center"),
        ("DATE / HEURE",  42, 100, "left"),
        ("PRODUIT",     146, 110, "left"),
        ("CATÉGORIE",   260,  90, "left"),
        ("CLIENT",      354, 120, "left"),
        ("NÉ(E) LE",    478,  75, "left"),
    ]

    CONTENT_TOP = H - 72   # safe y start below 60px header + 12px margin

    # ════════════════════════════════════════════
    #  PAGE 1 — RÉCAP VENTES
    # ════════════════════════════════════════════
    page_num = 1
    draw_header_block(
        title,
        f"Export du {now.strftime('%d/%m/%Y à %H:%M')}  ·  {len(sales or [])} vente(s)  ·  {len(rebonds or [])} rebond(s)"
    )
    draw_footer(page_num)

    # Summary KPI strip
    kpi_y = CONTENT_TOP - 6
    multi_stats = compute_multi_vad(sales or [])
    kpi_boxes = [
        ("VENTES TOTALES",  str(int(general or 0))),
        ("REBONDS",         str(len(rebonds or []))),
        ("TAUX CONV.",      f"{(len(sales or []) / len(rebonds) * 100):.0f}%" if rebonds else "—"),
        ("MULTI VAD",       f"{multi_stats['pct_multi']:.0f}%  ({multi_stats['count_multi']} v.)"),
        ("SG AGENDA",       str(int(sg_agenda or 0))),
    ]
    box_w = (W - 40) / len(kpi_boxes)
    for i, (lbl, val) in enumerate(kpi_boxes):
        bx = 20 + i * box_w
        c.setFillColor(SILVER_L)
        c.rect(bx, kpi_y - 22, box_w - 4, 28, fill=1, stroke=0)
        c.setStrokeColor(SILVER)
        c.setLineWidth(0.6)
        c.rect(bx, kpi_y - 22, box_w - 4, 28, fill=0, stroke=1)
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(bx + (box_w - 4) / 2, kpi_y - 12, val)
        c.setFillColor(SILVER_D)
        c.setFont("Helvetica", 7)
        c.drawCentredString(bx + (box_w - 4) / 2, kpi_y - 21, lbl)

    y = kpi_y - 36

    # Section title
    y = section_title("Détail des ventes", y)

    # Table header
    y = table_header(SALES_COLS, y)

    row_idx = 0
    for s in sales or []:
        if y < 50:
            draw_footer(page_num)
            c.showPage()
            page_num += 1
            draw_header_block(f"{title}  (suite p.{page_num})")
            draw_footer(page_num)
            y = CONTENT_TOP
            y = section_title("Détail des ventes (suite)", y)
            y = table_header(SALES_COLS, y)
            row_idx = 0

        dt_disp  = safe_str(s.get("dt_display") or s.get("dt") or "")
        product  = safe_str(s.get("product") or "")
        category = safe_str(s.get("category") or "")
        cli      = s.get("client") or {}
        nom      = safe_str(cli.get("nom") or "").strip()
        prenom   = safe_str(cli.get("prenom") or "").strip()
        client_name = f"{prenom} {nom}".strip() or "—"
        naissance   = safe_str(cli.get("naissance") or "—").strip()

        row_data = [
            (str(row_idx + 1), SILVER_D),
            (dt_disp[:22],     INK),
            (product[:26],     INK),
            (category[:20],    SILVER_D),
            (client_name[:26], INK),
            (naissance[:12],   SILVER_D),
        ]
        y = table_row(row_data, y, row_idx, SALES_COLS)
        row_idx += 1

    # ── Tableau des rebonds ──────────────────────────────────────────
    if rebonds:
        if y < 120:
            draw_footer(page_num)
            c.showPage()
            page_num += 1
            draw_header_block(f"{title}  (suite p.{page_num})", "Rebonds")
            draw_footer(page_num)
            y = CONTENT_TOP

        y -= 10
        y = section_title("Détail des rebonds", y)

        REBOND_COLS = [
            ("#",            20,  18, "center"),
            ("DATE / HEURE", 42, 110, "left"),
            ("PRODUIT",     156, 120, "left"),
            ("CATÉGORIE",   280, 100, "left"),
        ]
        y = table_header(REBOND_COLS, y)

        for ri, r in enumerate(rebonds):
            if y < 50:
                draw_footer(page_num)
                c.showPage()
                page_num += 1
                draw_header_block(f"{title}  (suite p.{page_num})", "Rebonds (suite)")
                draw_footer(page_num)
                y = CONTENT_TOP
                y = section_title("Détail des rebonds (suite)", y)
                y = table_header(REBOND_COLS, y)

            dt_disp  = safe_str(r.get("dt_display") or r.get("dt") or "")
            product  = safe_str(r.get("product") or "—")
            category = safe_str(r.get("category") or "—")
            row_data = [
                (str(ri + 1),   SILVER_D),
                (dt_disp[:22],  INK),
                (product[:28],  INK),
                (category[:24], SILVER_D),
            ]
            y = table_row(row_data, y, ri, REBOND_COLS)

    draw_footer(page_num)

    # ── SG Agenda note ───────────────────────────────────────────────────────
    if sg_agenda:
        if y < 80:
            draw_footer(page_num)
            c.showPage()
            page_num += 1
            draw_header_block(f"{title}  (suite p.{page_num})")
            draw_footer(page_num)
            y = CONTENT_TOP
        y -= 8
        c.setFillColor(colors.HexColor("#1A3A5F"))
        c.rect(20, y - 4, W - 40, 20, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#C9A000"))
        c.rect(20, y - 4, 4, 20, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(30, y + 2, f"SG AGENDA RÉALISÉS : {int(sg_agenda or 0)}")

    # ════════════════════════════════════════════
    #  PAGE ANALYSE
    # ════════════════════════════════════════════
    c.showPage()
    page_num += 1
    draw_header_block("ANALYSE DES VENTES", f"{len(sales or [])} vente(s) analysée(s)")
    draw_footer(page_num)

    y = CONTENT_TOP

    # ── Pie chart by category ─────────────────
    y = section_title("Répartition par catégorie", y)

    cat_counts = compute_category_stats(sales or [])
    total_sales = len(sales or [])
    items_sorted = sorted(cat_counts.items(), key=lambda kv: kv[1], reverse=True)
    if len(items_sorted) > 10:
        top = items_sorted[:10]
        rest_sum = sum(v for _, v in items_sorted[10:])
        top.append(("Autres", rest_sum))
        items_sorted = top

    # ── Layout: pie left-of-center, legend panel on the right ──
    # A4 width = 595pt. Margins 20pt each side → usable = 555pt.
    # Pie area: x 20..310 (290pt wide), center at x=155
    # Legend area: x 320..W-20 (255pt wide)
    PIE_R  = 80
    PIE_CX = 165
    PIE_CY = y - 108
    pie_height = 220   # vertical space consumed

    legend_entries = draw_pie_chart(c, PIE_CX, PIE_CY, PIE_R, items_sorted, max(1, total_sales), PIE_PALETTE)

    # ── RIGHT LEGEND — colored swatch + category name + count ──
    LEG_X       = 318          # left edge of legend block
    LEG_W       = W - LEG_X - 16   # available width (~261pt)
    SWATCH_W    = 11
    SWATCH_H    = 9
    ROW_H       = 15
    LEG_TITLE_Y = y - 12

    # Header
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(ACCENT)
    c.drawString(LEG_X, LEG_TITLE_Y, "VENTES PAR CATÉGORIE")
    c.setStrokeColor(SILVER_L)
    c.setLineWidth(0.5)
    c.line(LEG_X, LEG_TITLE_Y - 3, LEG_X + 160, LEG_TITLE_Y - 3)

    # Column headers
    COUNT_X = LEG_X + LEG_W - 10  # right-align count column
    legy = LEG_TITLE_Y - 16
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(SILVER_D)
    c.drawString(LEG_X + SWATCH_W + 6, legy, "CATÉGORIE")
    c.drawRightString(COUNT_X, legy, "NB")
    legy -= 4
    c.setStrokeColor(SILVER_L)
    c.line(LEG_X, legy, LEG_X + LEG_W, legy)
    legy -= 10

    for row_i, (col, label, count, pct) in enumerate(legend_entries):
        if legy < PIE_CY - PIE_R - 10:
            break
        # Alternating row background
        if row_i % 2 == 0:
            c.setFillColor(ROW_ALT)
            c.rect(LEG_X, legy - 3, LEG_W, ROW_H - 1, fill=1, stroke=0)

        # Colored swatch (rounded rect)
        c.setFillColor(col)
        c.roundRect(LEG_X + 1, legy, SWATCH_W, SWATCH_H, 2, fill=1, stroke=0)

        # Category name — cleaned, truncated to fit
        clean = clean_cat_label(safe_str(label))
        short = clean[:22]
        c.setFont("Helvetica", 7.5)
        c.setFillColor(INK)
        c.drawString(LEG_X + SWATCH_W + 6, legy + 1, short)

        # Count — right-aligned, bold, accent color
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(ACCENT)
        c.drawRightString(COUNT_X, legy + 1, str(int(count)))

        # Pct — smaller, muted
        c.setFont("Helvetica", 6.5)
        c.setFillColor(SILVER_D)
        c.drawRightString(COUNT_X - 20, legy + 1, f"{pct:.0f}%")

        legy -= ROW_H

    # Total row
    legy -= 3
    c.setStrokeColor(SILVER)
    c.setLineWidth(0.5)
    c.line(LEG_X, legy + 6, LEG_X + LEG_W, legy + 6)
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(INK)
    c.drawString(LEG_X + SWATCH_W + 6, legy - 2, "TOTAL")
    c.setFillColor(ACCENT)
    c.drawRightString(COUNT_X, legy - 2, str(total_sales))

    y = y - pie_height

    # ── Time bins ─────────────────────────────
    y = section_title("Répartition par tranche horaire", y)
    bins = compute_time_bins_stats(sales or [])
    best_label = max(bins, key=lambda b: b["count"])["label"] if bins else ""

    TIME_COLS = [
        ("TRANCHE",  28,  110, "left"),
        ("VENTES",  142,   40, "center"),
        ("%",       190,   40, "center"),
        ("PERF.",   238,   50, "center"),
    ]
    y = table_header(TIME_COLS, y)
    for bi, b in enumerate(bins):
        lbl = b["label"]
        cnt = int(b["count"])
        pct = float(b["pct"])
        if lbl == best_label and cnt > 0: perf, pcol = "TOP ★", GREEN
        elif pct >= 20: perf, pcol = "Fort", GREEN
        elif pct >= 10: perf, pcol = "Moyen", AMBER
        else: perf, pcol = "Faible", RED
        row_data = [
            (lbl, INK), (str(cnt), ACCENT), (f"{pct:.1f}%", INK), (perf, pcol)
        ]
        y = table_row(row_data, y, bi, TIME_COLS)
        if y < 80:
            break

    y -= 12

    # ── Conversion table ──────────────────────
    if y < 150:
        draw_footer(page_num)
        c.showPage()
        page_num += 1
        draw_header_block("ANALYSE DES VENTES  (suite)")
        draw_footer(page_num)
        y = CONTENT_TOP

    y = section_title("Taux de conversion par produit", y)

    rows = compute_product_conversion_table(sales or [], rebonds or [])

    CONV_COLS = [
        ("PRODUIT",      28, 180, "left"),
        ("REBONDS",     212,  55, "center"),
        ("VENTES",      271,  55, "center"),
        ("CONVERSION",  330,  90, "center"),
    ]
    y = table_header(CONV_COLS, y)

    for ri, (p, rb, sl, conv) in enumerate(rows):
        if y < 60:
            draw_footer(page_num)
            c.showPage()
            page_num += 1
            draw_header_block("ANALYSE DES VENTES  (suite)")
            draw_footer(page_num)
            y = CONTENT_TOP
            y = section_title("Taux de conversion par produit (suite)", y)
            y = table_header(CONV_COLS, y)

        if rb <= 0:
            conv_txt, conv_col = "—", SILVER_D
        elif conv >= 30:
            conv_txt, conv_col = f"{conv:.1f}%  ▲", GREEN
        elif conv >= 15:
            conv_txt, conv_col = f"{conv:.1f}%", AMBER
        else:
            conv_txt, conv_col = f"{conv:.1f}%  ▼", RED

        row_data = [(safe_str(p)[:36], INK), (str(rb), SILVER_D), (str(sl), ACCENT), (conv_txt, conv_col)]
        y = table_row(row_data, y, ri, CONV_COLS)

    # Total row
    total_rb  = len(rebonds or [])
    total_sl  = len(sales or [])
    g_conv    = (total_sl / total_rb * 100.0) if total_rb > 0 else None
    g_txt     = f"{g_conv:.1f}%" if g_conv is not None else "—"

    y -= 4
    c.setFillColor(INK)
    c.rect(20, y - 4, W - 40, 16, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(28, y + 1, "TOTAL")
    c.drawCentredString(212 + 27, y + 1, str(total_rb))
    c.drawCentredString(271 + 27, y + 1, str(total_sl))
    c.setFillColor(SILVER)
    c.drawCentredString(330 + 45, y + 1, g_txt)

    draw_footer(page_num)
    c.save()
    return path


# =========================
# Routes
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/load", methods=["GET"])
def api_load():
    s = load_state()
    return jsonify({"ok": True, "state": s})


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Invalid payload"}), 400
    state = load_state()
    state["sales"]          = data.get("sales",          state.get("sales", []))
    state["rebonds"]        = data.get("rebonds",        state.get("rebonds", []))
    state["sgAgenda"]       = int(data.get("sgAgenda", state.get("sgAgenda", 0)) or 0)
    state["currentClient"]  = data.get("currentClient",  state.get("currentClient"))
    state["catalogCustom"]  = data.get("catalogCustom",  state.get("catalogCustom", {}))
    state["catalogOverrides"] = data.get("catalogOverrides", state.get("catalogOverrides", {}))
    state["catalogDeleted"] = data.get("catalogDeleted", state.get("catalogDeleted", {}))
    if not isinstance(state["sales"], list): state["sales"] = []
    if not isinstance(state["rebonds"], list): state["rebonds"] = []
    if not isinstance(state["catalogCustom"], dict): state["catalogCustom"] = {}
    if not isinstance(state["catalogOverrides"], dict): state["catalogOverrides"] = {}
    if not isinstance(state["catalogDeleted"], dict): state["catalogDeleted"] = {}
    save_state(state)
    return jsonify({"ok": True})


@app.route("/api/reset_sales", methods=["POST"])
def api_reset_sales():
    s = load_state()
    s["sales"] = []
    s["rebonds"] = []
    s["currentClient"] = None
    save_state(s)
    return jsonify({"ok": True, "state": s})


@app.route("/api/reset_all", methods=["POST"])
def api_reset_all():
    s = default_state()
    save_state(s)
    return jsonify({"ok": True, "state": s})


@app.route("/export_pdf", methods=["POST"])
def export_pdf_route():
    try:
        data    = request.get_json(silent=True) or {}
        general = int(data.get("general") or 0)
        sales   = data.get("sales") or []
        rebonds = data.get("rebonds") or []
        title   = safe_str(data.get("title") or "RECAP VENTES")
        if not isinstance(sales, list):   sales   = []
        if not isinstance(rebonds, list): rebonds = []
        sg_agenda = int(data.get("sgAgenda") or 0)
        path = export_pdf(general=general, sales=sales, rebonds=rebonds, title=title, sg_agenda=sg_agenda)
        folder = os.path.dirname(path)
        return jsonify({"ok": True, "path": path, "folder": folder, "filename": os.path.basename(path)})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "detail": traceback.format_exc()}), 500


@app.route("/open_folder", methods=["POST"])
def open_folder_route():
    try:
        data   = request.get_json(silent=True) or {}
        folder = safe_str(data.get("folder") or get_app_data_dir())
        if not os.path.isdir(folder):
            folder = get_app_data_dir()
        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform.startswith("darwin"):
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7000, debug=False)
