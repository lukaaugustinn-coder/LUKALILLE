"""
app.py — T850 VAD — Serveur Flask local
"""

from __future__ import annotations

import logging
import math
import os
import sys
import json
import tempfile
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request, jsonify
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

# ── KINOVA SYNC (optionnel) ───────────────────────────────────────────────────
# Si le module n'est pas disponible l'app fonctionne sans push équipe.
try:
    from kinova_sync import push_state_async as _push_state_async  # type: ignore[import]
    _KINOVA_AVAILABLE = True
except ImportError:
    _KINOVA_AVAILABLE = False

    def _push_state_async(state: dict[str, Any]) -> None:  # noqa: D401
        """No-op fallback quand kinova_sync n'est pas installé."""


# ── Logging ───────────────────────────────────────────────────────────────────
def _setup_logging() -> logging.Logger:
    log_path = Path(__file__).parent / "app.log"
    logger = logging.getLogger("t850.app")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh = RotatingFileHandler(
            log_path, maxBytes=2 * 1_024 * 1_024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


log = _setup_logging()


# ── PyInstaller resource path ─────────────────────────────────────────────────
def resource_path(relative_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


TEMPLATE_DIR = resource_path("templates")
STATIC_DIR   = resource_path("static")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 Mo max par requête

APP_FOLDER_NAME = "T850_VAD_LukaA"


# ── Répertoires ───────────────────────────────────────────────────────────────
def get_downloads_dir() -> str:
    home = os.path.expanduser("~")
    for candidate in ("Downloads", "Téléchargements", "Download"):
        p = os.path.join(home, candidate)
        if os.path.isdir(p):
            return p
    return os.path.join(home, "Downloads")


def get_app_data_dir() -> str:
    out_dir = os.path.join(get_downloads_dir(), APP_FOLDER_NAME)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def get_state_path() -> str:
    return os.path.join(get_app_data_dir(), "state.json")


# ── State ─────────────────────────────────────────────────────────────────────
def default_state() -> dict[str, Any]:
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


def _sanitize_state(s: dict[str, Any]) -> dict[str, Any]:
    """Garantit que chaque clé a le bon type après chargement."""
    s.setdefault("version", 2)
    s.setdefault("sales", [])
    s.setdefault("rebonds", [])
    s.setdefault("currentClient", None)
    s.setdefault("catalogCustom", {})
    s.setdefault("catalogOverrides", {})
    s.setdefault("catalogDeleted", {})
    s.setdefault("sgAgenda", 0)

    if not isinstance(s["sales"], list):            s["sales"] = []
    if not isinstance(s["rebonds"], list):          s["rebonds"] = []
    if not isinstance(s["catalogCustom"], dict):    s["catalogCustom"] = {}
    if not isinstance(s["catalogOverrides"], dict): s["catalogOverrides"] = {}
    if not isinstance(s["catalogDeleted"], dict):   s["catalogDeleted"] = {}

    # Migration : sgAgenda était parfois stocké dans catalogOverrides.__sgAgenda
    sga_legacy = (s["catalogOverrides"] or {}).get("__sgAgenda")
    if sga_legacy and not s.get("sgAgenda"):
        s["sgAgenda"] = int(sga_legacy)

    try:
        s["sgAgenda"] = int(s.get("sgAgenda") or 0)
    except (TypeError, ValueError):
        s["sgAgenda"] = 0

    return s


def load_state() -> dict[str, Any]:
    path = get_state_path()
    if not os.path.exists(path):
        s = default_state()
        save_state(s)
        return s
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            log.warning("state.json corrompu (pas un dict) — réinitialisation.")
            s = default_state()
            save_state(s)
            return s
        return _sanitize_state(raw)
    except Exception:
        log.exception("Impossible de lire state.json — réinitialisation.")
        s = default_state()
        save_state(s)
        return s


def save_state(state: dict[str, Any]) -> None:
    """Écriture atomique : write→temp, puis os.replace (évite la corruption)."""
    path = get_state_path()
    dir_ = os.path.dirname(path)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(state, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
        log.debug("State sauvegardé (%d ventes, %d rebonds).",
                  len(state.get("sales") or []), len(state.get("rebonds") or []))
    except Exception:
        log.exception("Échec de la sauvegarde du state.")
        return

    if _KINOVA_AVAILABLE:
        try:
            _push_state_async(state)
        except Exception:
            log.warning("kinova_sync.push_state_async a échoué (non bloquant).")


# ── Utilitaires ───────────────────────────────────────────────────────────────
def safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def parse_sale_datetime(item: dict[str, Any]) -> datetime | None:
    if not isinstance(item, dict):
        return None
    iso = item.get("dt")
    if isinstance(iso, str) and iso.strip():
        try:
            return datetime.fromisoformat(iso.strip())
        except ValueError:
            pass
    disp = item.get("dt_display")
    if isinstance(disp, str) and disp.strip():
        try:
            return datetime.strptime(disp.strip(), "%d/%m/%Y %H:%M")
        except ValueError:
            pass
    return None


def compute_category_stats(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in items or []:
        cat = safe_str((s or {}).get("category")).strip() or "—"
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def compute_time_bins_stats(sales: list[dict[str, Any]]) -> list[dict[str, Any]]:
    BINS: list[tuple[str, int, int]] = [
        ("08:00-10:00", 8, 10),
        ("10:00-12:00", 10, 12),
        ("12:00-14:00", 12, 14),
        ("14:00-16:00", 14, 16),
        ("16:00-18:00", 16, 18),
        ("18:00-20:00", 18, 20),
    ]
    counts: dict[str, int] = {label: 0 for label, _, _ in BINS}
    other = 0
    for s in sales or []:
        dt = parse_sale_datetime(s)
        if not dt:
            other += 1
            continue
        h = dt.hour + dt.minute / 60.0
        placed = False
        for label, start, end in BINS:
            if start <= h < end:
                counts[label] += 1
                placed = True
                break
        if not placed:
            other += 1
    total = len(sales or [])
    out: list[dict[str, Any]] = []
    for label, _, _ in BINS:
        cnt = counts[label]
        out.append({"label": label, "count": cnt, "pct": (cnt / total * 100.0) if total > 0 else 0.0})
    out.append({"label": "Autres", "count": other,
                "pct": (other / total * 100.0) if total > 0 else 0.0})
    return out


def compute_multi_vad(sales: list[dict[str, Any]]) -> dict[str, Any]:
    """Multi VAD = ventes consécutives sur le même client (groupe ≥ 2)."""
    if not sales:
        return {"count_multi": 0, "count_solo": 0, "total": 0, "pct_multi": 0.0}

    def client_key(s: dict[str, Any]) -> str:
        cli    = s.get("client") or {}
        nom    = safe_str(cli.get("nom", "")).strip().lower()
        prenom = safe_str(cli.get("prenom", "")).strip().lower()
        return f"{prenom}|{nom}" if (nom or prenom) else ""

    count_multi = 0
    i = 0
    while i < len(sales):
        key = client_key(sales[i])
        if not key:
            i += 1
            continue
        j = i + 1
        while j < len(sales) and client_key(sales[j]) == key:
            j += 1
        if j - i >= 2:
            count_multi += j - i
        i = j

    total     = len(sales)
    count_solo = total - count_multi
    return {
        "count_multi": count_multi,
        "count_solo":  count_solo,
        "total":       total,
        "pct_multi":   round(count_multi / total * 100, 1) if total > 0 else 0.0,
    }


def compute_product_conversion_table(
    sales: list[dict[str, Any]], rebonds: list[dict[str, Any]]
) -> list[tuple[str, int, int, float | None]]:
    sales_counts: dict[str, int] = {}
    reb_counts:   dict[str, int] = {}
    for s in sales or []:
        p = safe_str((s or {}).get("product")).strip() or "—"
        sales_counts[p] = sales_counts.get(p, 0) + 1
    for r in rebonds or []:
        p = safe_str((r or {}).get("product")).strip() or "—"
        reb_counts[p] = reb_counts.get(p, 0) + 1
    rows = []
    for p in set(sales_counts) | set(reb_counts):
        rb = int(reb_counts.get(p, 0))
        sl = int(sales_counts.get(p, 0))
        conv: float | None = (sl / rb * 100.0) if rb > 0 else None
        rows.append((p, rb, sl, conv))
    rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return rows


def clean_cat_label(raw: str) -> str:
    """Supprime les emojis, conserve le texte latin lisible."""
    out = [ch for ch in raw if ord(ch) < 0x0300 or ch in (" ", "-", "_", ".", "/")]
    result = "".join(out).strip()
    return result if result else raw[:12]


# ── PDF — palette ─────────────────────────────────────────────────────────────
PIE_PALETTE = [
    colors.HexColor("#FFB3B3"), colors.HexColor("#FFD9A0"), colors.HexColor("#FFF4A0"),
    colors.HexColor("#B8F0B8"), colors.HexColor("#A8DFF0"), colors.HexColor("#B3B8FF"),
    colors.HexColor("#E0B3FF"), colors.HexColor("#FFB3E6"), colors.HexColor("#B3FFE6"),
    colors.HexColor("#FFD6B3"), colors.HexColor("#C8F0A0"), colors.HexColor("#F0C8A0"),
]


# ── PDF — pie chart ───────────────────────────────────────────────────────────
def draw_pie_chart(
    c: canvas.Canvas,
    cx: float, cy: float, r: float,
    items: list[tuple[str, int]],
    total: int,
    palette: list[Any],
) -> list[tuple[Any, str, int, float]]:
    """Camembert professionnel avec étiquettes externes sans chevauchement."""
    if total <= 0 or not items:
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.HexColor("#6E6E6E"))
        c.drawString(cx - r, cy, "Aucune vente à analyser.")
        return []

    INK_PDF = colors.HexColor("#0F1B2D")

    legend = [
        (palette[i % len(palette)], label, count, count / total * 100.0)
        for i, (label, count) in enumerate(items)
    ]

    # 1. Tranches
    start_angle = 90.0
    mid_angles: list[float] = []
    for col, _label, count, pct in legend:
        sweep = count / total * 360.0
        end_angle = start_angle - sweep
        mid_angles.append((start_angle + end_angle) / 2.0)

        c.setFillColor(col)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.0)
        p = c.beginPath()
        p.moveTo(cx, cy)
        p.arc(cx - r, cy - r, cx + r, cy + r, startAng=end_angle, extent=sweep)
        p.close()
        c.drawPath(p, stroke=1, fill=1)

        if sweep >= 24:
            rad = math.radians((start_angle + end_angle) / 2.0)
            c.setFont("Helvetica-Bold", 7.5)
            c.setFillColor(INK_PDF)
            c.drawCentredString(
                cx + r * 0.62 * math.cos(rad),
                cy + r * 0.62 * math.sin(rad) - 3,
                f"{pct:.0f}%",
            )
        start_angle = end_angle

    c.setStrokeColor(colors.HexColor("#B0C4D8"))
    c.setLineWidth(0.6)
    c.circle(cx, cy, r, stroke=1, fill=0)

    # 2. Colonnes gauche / droite
    LINE_H    = 11
    COL_MARGIN = r + 22

    left_items:  list[tuple[int, Any, str, float, float]] = []
    right_items: list[tuple[int, Any, str, float, float]] = []
    for i, (col, label, count, pct) in enumerate(legend):
        entry = (i, col, label, pct, mid_angles[i])
        (right_items if math.cos(math.radians(mid_angles[i])) >= 0 else left_items).append(entry)

    def assign_column_y(
        items_list: list[tuple[int, Any, str, float, float]]
    ) -> list[tuple[int, Any, str, float, float, float]]:
        n = len(items_list)
        if not n:
            return []
        y_start = cy + (n - 1) * LINE_H / 2.0
        sorted_items = sorted(items_list, key=lambda it: -math.sin(math.radians(it[4])))
        return [(*item, y_start - row * LINE_H) for row, item in enumerate(sorted_items)]  # type: ignore[return-value]

    def draw_label_item(
        _i: int, col: Any, label: str, pct: float, mid_a: float, label_y: float
    ) -> None:
        rad   = math.radians(mid_a)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        px, py  = cx + r * cos_a, cy + r * sin_a
        er = r + 10
        ex, ey = cx + er * cos_a, cy + er * sin_a
        col_x = min(cx + COL_MARGIN, cx + r + 28) if cos_a >= 0 else cx - COL_MARGIN

        c.setStrokeColor(col)
        c.setLineWidth(0.7)
        c.line(px, py, ex, ey)
        c.line(ex, ey, col_x, label_y + 3)
        c.setFillColor(col)
        c.circle(col_x, label_y + 3, 1.8, fill=1, stroke=0)

        disp = f"{clean_cat_label(safe_str(label))[:18]}  {pct:.0f}%"
        c.setFont("Helvetica-Bold", 7.0)
        c.setFillColor(INK_PDF)
        if cos_a >= 0:
            c.drawString(col_x + 5, label_y, disp)
        else:
            c.drawRightString(col_x - 5, label_y, disp)

    for item in assign_column_y(right_items):
        draw_label_item(*item)
    for item in assign_column_y(left_items):
        draw_label_item(*item)

    return legend


# ── PDF — export ──────────────────────────────────────────────────────────────
def export_pdf(
    general: int,
    sales: list[dict[str, Any]],
    rebonds: list[dict[str, Any]],
    title: str = "RECAP VENTES",
    sg_agenda: int = 0,
) -> str:
    out_dir = get_app_data_dir()
    now = datetime.now()
    path = os.path.join(out_dir, f"t850_vad_{now.strftime('%Y-%m-%d_%H-%M')}.pdf")
    log.info("Export PDF → %s", path)

    cv = canvas.Canvas(path, pagesize=A4)
    W, H = A4

    # Palette Silver
    INK      = colors.HexColor("#0F1B2D")
    SILVER   = colors.HexColor("#C9D6E3")
    SILVER_L = colors.HexColor("#E8EEF4")
    SILVER_D = colors.HexColor("#8A9BB0")
    ACCENT   = colors.HexColor("#1E3A5F")
    ROW_ALT  = colors.HexColor("#F4F7FA")
    ROW_HDR  = colors.HexColor("#1E3A5F")
    GREEN    = colors.HexColor("#1A6B3A")
    AMBER    = colors.HexColor("#7A5C00")
    RED      = colors.HexColor("#8B1A1A")
    WHITE    = colors.white
    CONTENT_TOP = H - 72

    def draw_header_block(heading: str, subtitle: str = "") -> None:
        HDR_H = 60
        cv.setFillColor(INK)
        cv.rect(0, H - HDR_H, W, HDR_H, fill=1, stroke=0)
        cv.setFillColor(colors.HexColor("#C9A000"))
        cv.rect(0, H - HDR_H, 4, HDR_H, fill=1, stroke=0)

        cv.setFillColor(WHITE); cv.setFont("Helvetica-Bold", 20)
        cv.drawString(16, H - 28, "T850")
        cv.setFillColor(SILVER_D); cv.setFont("Helvetica", 7.5)
        cv.drawString(16, H - 40, "COMPTEUR VAD")
        cv.setFillColor(colors.HexColor("#3A5070"))
        cv.rect(118, H - HDR_H + 8, 1, HDR_H - 16, fill=1, stroke=0)

        cv.setFillColor(WHITE); cv.setFont("Helvetica-Bold", 12)
        cv.drawString(130, H - 26, heading[:55])
        if subtitle:
            cv.setFillColor(SILVER_D); cv.setFont("Helvetica", 7.5)
            cv.drawString(130, H - 38, subtitle[:80])

        cv.setFillColor(SILVER_D); cv.setFont("Helvetica", 8)
        cv.drawRightString(W - 14, H - 26, now.strftime("%d/%m/%Y"))
        cv.drawRightString(W - 14, H - 38, now.strftime("%H:%M"))
        cv.setFillColor(colors.HexColor("#2A4060"))
        cv.rect(0, H - HDR_H - 1, W, 1, fill=1, stroke=0)

    def draw_footer(page_num: int = 0) -> None:
        cv.setFillColor(SILVER_L); cv.rect(0, 0, W, 34, fill=1, stroke=0)
        cv.setFillColor(SILVER_D); cv.setFont("Helvetica", 7.5)
        cv.drawString(20, 12, "T850 — Compteur VAD — Un logiciel KINOVA  ·  By. Luka Augustin")
        if page_num:
            cv.drawRightString(W - 20, 12, f"Page {page_num}")

    def section_title(title_text: str, y_pos: float) -> float:
        cv.setFillColor(ACCENT); cv.rect(20, y_pos - 4, W - 40, 18, fill=1, stroke=0)
        cv.setFillColor(WHITE); cv.setFont("Helvetica-Bold", 9)
        cv.drawString(28, y_pos + 1, title_text.upper())
        return y_pos - 24

    def table_header(cols: list[tuple[str, int, int, str]], y_pos: float) -> float:
        row_h = 16
        cv.setFillColor(ROW_HDR); cv.rect(20, y_pos - 2, W - 40, row_h, fill=1, stroke=0)
        cv.setFillColor(SILVER); cv.setFont("Helvetica-Bold", 8)
        for label, x, w, align in cols:
            if align == "right":    cv.drawRightString(x + w, y_pos + 3, label)
            elif align == "center": cv.drawCentredString(x + w // 2, y_pos + 3, label)
            else:                   cv.drawString(x, y_pos + 3, label)
        return y_pos - row_h - 2

    def table_row(
        cols_data: list[tuple[str, Any]],
        y_pos: float,
        row_index: int,
        col_defs: list[tuple[str, int, int, str]],
    ) -> float:
        row_h = 14
        if row_index % 2 == 0:
            cv.setFillColor(ROW_ALT); cv.rect(20, y_pos - 2, W - 40, row_h, fill=1, stroke=0)
        cv.setFont("Helvetica", 8)
        for (text, txt_color), (_, x, w, align) in zip(cols_data, col_defs):
            cv.setFillColor(txt_color if txt_color else INK)
            s = str(text)
            if align == "right":    cv.drawRightString(x + w, y_pos + 2, s[:40])
            elif align == "center": cv.drawCentredString(x + w // 2, y_pos + 2, s[:40])
            else:                   cv.drawString(x, y_pos + 2, s[:50])
        cv.setStrokeColor(SILVER_L); cv.setLineWidth(0.4)
        cv.line(20, y_pos - 2, W - 20, y_pos - 2)
        return y_pos - row_h

    SALES_COLS: list[tuple[str, int, int, str]] = [
        ("#",           20,  18, "center"),
        ("DATE / HEURE", 42, 100, "left"),
        ("PRODUIT",     146, 110, "left"),
        ("CATÉGORIE",   260,  90, "left"),
        ("CLIENT",      354, 120, "left"),
        ("NÉ(E) LE",    478,  75, "left"),
    ]

    # ── PAGE 1 — RÉCAP ────────────────────────────────────────────────────────
    page_num = 1
    n_sales, n_rebonds = len(sales or []), len(rebonds or [])
    draw_header_block(
        title,
        f"Export du {now.strftime('%d/%m/%Y à %H:%M')}  ·  {n_sales} vente(s)  ·  {n_rebonds} rebond(s)",
    )
    draw_footer(page_num)

    kpi_y = CONTENT_TOP - 6
    multi_stats = compute_multi_vad(sales or [])
    kpi_boxes = [
        ("VENTES TOTALES", str(int(general or 0))),
        ("REBONDS",        str(n_rebonds)),
        ("TAUX CONV.",     f"{n_sales / n_rebonds * 100:.0f}%" if n_rebonds else "—"),
        ("MULTI VAD",      f"{multi_stats['pct_multi']:.0f}%  ({multi_stats['count_multi']} v.)"),
        ("SG AGENDA",      str(int(sg_agenda or 0))),
    ]
    box_w = (W - 40) / len(kpi_boxes)
    for i, (lbl, val) in enumerate(kpi_boxes):
        bx = 20 + i * box_w
        cv.setFillColor(SILVER_L); cv.rect(bx, kpi_y - 22, box_w - 4, 28, fill=1, stroke=0)
        cv.setStrokeColor(SILVER); cv.setLineWidth(0.6)
        cv.rect(bx, kpi_y - 22, box_w - 4, 28, fill=0, stroke=1)
        cv.setFillColor(ACCENT); cv.setFont("Helvetica-Bold", 14)
        cv.drawCentredString(bx + (box_w - 4) / 2, kpi_y - 12, val)
        cv.setFillColor(SILVER_D); cv.setFont("Helvetica", 7)
        cv.drawCentredString(bx + (box_w - 4) / 2, kpi_y - 21, lbl)

    y = kpi_y - 36
    y = section_title("Détail des ventes", y)
    y = table_header(SALES_COLS, y)

    row_idx = 0
    for s in sales or []:
        if y < 50:
            draw_footer(page_num); cv.showPage(); page_num += 1
            draw_header_block(f"{title}  (suite p.{page_num})")
            draw_footer(page_num); y = CONTENT_TOP
            y = section_title("Détail des ventes (suite)", y)
            y = table_header(SALES_COLS, y); row_idx = 0

        cli = s.get("client") or {}
        nom    = safe_str(cli.get("nom") or "").strip()
        prenom = safe_str(cli.get("prenom") or "").strip()
        row_data = [
            (str(row_idx + 1),                               SILVER_D),
            (safe_str(s.get("dt_display") or s.get("dt") or "")[:22], INK),
            (safe_str(s.get("product") or "")[:26],          INK),
            (safe_str(s.get("category") or "")[:20],         SILVER_D),
            (f"{prenom} {nom}".strip() or "—",               INK),
            (safe_str(cli.get("naissance") or "—").strip()[:12], SILVER_D),
        ]
        y = table_row(row_data, y, row_idx, SALES_COLS)
        row_idx += 1

    if rebonds:
        if y < 120:
            draw_footer(page_num); cv.showPage(); page_num += 1
            draw_header_block(f"{title}  (suite p.{page_num})", "Rebonds")
            draw_footer(page_num); y = CONTENT_TOP
        y -= 10
        REBOND_COLS: list[tuple[str, int, int, str]] = [
            ("#",            20,  18, "center"),
            ("DATE / HEURE", 42, 110, "left"),
            ("PRODUIT",     156, 120, "left"),
            ("CATÉGORIE",   280, 100, "left"),
        ]
        y = section_title("Détail des rebonds", y)
        y = table_header(REBOND_COLS, y)
        for ri, rb in enumerate(rebonds):
            if y < 50:
                draw_footer(page_num); cv.showPage(); page_num += 1
                draw_header_block(f"{title}  (suite p.{page_num})", "Rebonds (suite)")
                draw_footer(page_num); y = CONTENT_TOP
                y = section_title("Détail des rebonds (suite)", y)
                y = table_header(REBOND_COLS, y)
            row_data = [
                (str(ri + 1),                                          SILVER_D),
                (safe_str(rb.get("dt_display") or rb.get("dt") or "")[:22], INK),
                (safe_str(rb.get("product") or "—")[:28],             INK),
                (safe_str(rb.get("category") or "—")[:24],            SILVER_D),
            ]
            y = table_row(row_data, y, ri, REBOND_COLS)

    draw_footer(page_num)

    if sg_agenda:
        if y < 80:
            draw_footer(page_num); cv.showPage(); page_num += 1
            draw_header_block(f"{title}  (suite p.{page_num})")
            draw_footer(page_num); y = CONTENT_TOP
        y -= 8
        cv.setFillColor(colors.HexColor("#1A3A5F"))
        cv.rect(20, y - 4, W - 40, 20, fill=1, stroke=0)
        cv.setFillColor(colors.HexColor("#C9A000"))
        cv.rect(20, y - 4, 4, 20, fill=1, stroke=0)
        cv.setFillColor(colors.white); cv.setFont("Helvetica-Bold", 9)
        cv.drawString(30, y + 2, f"SG AGENDA RÉALISÉS : {int(sg_agenda)}")

    # ── PAGE ANALYSE ──────────────────────────────────────────────────────────
    cv.showPage(); page_num += 1
    draw_header_block("ANALYSE DES VENTES", f"{n_sales} vente(s) analysée(s)")
    draw_footer(page_num)
    y = CONTENT_TOP

    y = section_title("Répartition par catégorie", y)
    cat_counts = compute_category_stats(sales or [])
    items_sorted = sorted(cat_counts.items(), key=lambda kv: kv[1], reverse=True)
    if len(items_sorted) > 10:
        rest_sum = sum(v for _, v in items_sorted[10:])
        items_sorted = items_sorted[:10] + [("Autres", rest_sum)]

    PIE_R, PIE_CX, PIE_CY = 80, 165, y - 108
    pie_height = 220
    legend_entries = draw_pie_chart(cv, PIE_CX, PIE_CY, PIE_R, items_sorted, max(1, n_sales), PIE_PALETTE)

    LEG_X   = 318
    LEG_W   = W - LEG_X - 16
    SWATCH_W, SWATCH_H, ROW_H_L = 11, 9, 15
    LEG_TITLE_Y = y - 12

    cv.setFont("Helvetica-Bold", 8); cv.setFillColor(ACCENT)
    cv.drawString(LEG_X, LEG_TITLE_Y, "VENTES PAR CATÉGORIE")
    cv.setStrokeColor(SILVER_L); cv.setLineWidth(0.5)
    cv.line(LEG_X, LEG_TITLE_Y - 3, LEG_X + 160, LEG_TITLE_Y - 3)

    COUNT_X = LEG_X + LEG_W - 10
    legy = LEG_TITLE_Y - 16
    cv.setFont("Helvetica-Bold", 7); cv.setFillColor(SILVER_D)
    cv.drawString(LEG_X + SWATCH_W + 6, legy, "CATÉGORIE")
    cv.drawRightString(COUNT_X, legy, "NB")
    legy -= 4
    cv.line(LEG_X, legy, LEG_X + LEG_W, legy)
    legy -= 10

    for row_i, (col, label, count, pct) in enumerate(legend_entries):
        if legy < PIE_CY - PIE_R - 10:
            break
        if row_i % 2 == 0:
            cv.setFillColor(ROW_ALT)
            cv.rect(LEG_X, legy - 3, LEG_W, ROW_H_L - 1, fill=1, stroke=0)
        cv.setFillColor(col)
        cv.roundRect(LEG_X + 1, legy, SWATCH_W, SWATCH_H, 2, fill=1, stroke=0)
        cv.setFont("Helvetica", 7.5); cv.setFillColor(INK)
        cv.drawString(LEG_X + SWATCH_W + 6, legy + 1, clean_cat_label(safe_str(label))[:22])
        cv.setFont("Helvetica-Bold", 8); cv.setFillColor(ACCENT)
        cv.drawRightString(COUNT_X, legy + 1, str(int(count)))
        cv.setFont("Helvetica", 6.5); cv.setFillColor(SILVER_D)
        cv.drawRightString(COUNT_X - 20, legy + 1, f"{pct:.0f}%")
        legy -= ROW_H_L

    legy -= 3
    cv.setStrokeColor(SILVER); cv.setLineWidth(0.5)
    cv.line(LEG_X, legy + 6, LEG_X + LEG_W, legy + 6)
    cv.setFont("Helvetica-Bold", 7.5); cv.setFillColor(INK)
    cv.drawString(LEG_X + SWATCH_W + 6, legy - 2, "TOTAL")
    cv.setFillColor(ACCENT); cv.drawRightString(COUNT_X, legy - 2, str(n_sales))

    y -= pie_height

    y = section_title("Répartition par tranche horaire", y)
    bins = compute_time_bins_stats(sales or [])
    best_label = max(bins, key=lambda b: b["count"])["label"] if bins else ""
    TIME_COLS: list[tuple[str, int, int, str]] = [
        ("TRANCHE", 28, 110, "left"), ("VENTES", 142, 40, "center"),
        ("%", 190, 40, "center"),     ("PERF.",  238, 50, "center"),
    ]
    y = table_header(TIME_COLS, y)
    for bi, b in enumerate(bins):
        lbl = b["label"]; cnt = int(b["count"]); pct = float(b["pct"])
        if lbl == best_label and cnt > 0: perf, pcol = "TOP ★", GREEN
        elif pct >= 20:                   perf, pcol = "Fort",   GREEN
        elif pct >= 10:                   perf, pcol = "Moyen",  AMBER
        else:                             perf, pcol = "Faible", RED
        y = table_row([(lbl, INK), (str(cnt), ACCENT), (f"{pct:.1f}%", INK), (perf, pcol)],
                      y, bi, TIME_COLS)
        if y < 80:
            break

    y -= 12
    if y < 150:
        draw_footer(page_num); cv.showPage(); page_num += 1
        draw_header_block("ANALYSE DES VENTES  (suite)")
        draw_footer(page_num); y = CONTENT_TOP

    y = section_title("Taux de conversion par produit", y)
    CONV_COLS: list[tuple[str, int, int, str]] = [
        ("PRODUIT",    28, 180, "left"),  ("REBONDS",    212, 55, "center"),
        ("VENTES",    271,  55, "center"), ("CONVERSION", 330, 90, "center"),
    ]
    y = table_header(CONV_COLS, y)
    for ri, (p, rb, sl, conv) in enumerate(compute_product_conversion_table(sales or [], rebonds or [])):
        if y < 60:
            draw_footer(page_num); cv.showPage(); page_num += 1
            draw_header_block("ANALYSE DES VENTES  (suite)")
            draw_footer(page_num); y = CONTENT_TOP
            y = section_title("Taux de conversion par produit (suite)", y)
            y = table_header(CONV_COLS, y)
        if rb <= 0:        conv_txt, conv_col = "—",              SILVER_D
        elif conv >= 30:   conv_txt, conv_col = f"{conv:.1f}%  ▲", GREEN
        elif conv >= 15:   conv_txt, conv_col = f"{conv:.1f}%",    AMBER
        else:              conv_txt, conv_col = f"{conv:.1f}%  ▼", RED
        y = table_row([(safe_str(p)[:36], INK), (str(rb), SILVER_D),
                       (str(sl), ACCENT), (conv_txt, conv_col)], y, ri, CONV_COLS)

    total_rb = n_rebonds; total_sl = n_sales
    g_txt = f"{total_sl / total_rb * 100.0:.1f}%" if total_rb > 0 else "—"
    y -= 4
    cv.setFillColor(INK); cv.rect(20, y - 4, W - 40, 16, fill=1, stroke=0)
    cv.setFillColor(WHITE); cv.setFont("Helvetica-Bold", 8.5)
    cv.drawString(28, y + 1, "TOTAL")
    cv.drawCentredString(212 + 27, y + 1, str(total_rb))
    cv.drawCentredString(271 + 27, y + 1, str(total_sl))
    cv.setFillColor(SILVER); cv.drawCentredString(330 + 45, y + 1, g_txt)

    draw_footer(page_num)
    cv.save()
    return path


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index() -> Any:
    return render_template("index.html")


@app.route("/api/load", methods=["GET"])
def api_load() -> Any:
    return jsonify({"ok": True, "state": load_state()})


@app.route("/api/save", methods=["POST"])
def api_save() -> Any:
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Payload invalide"}), 400

    state = load_state()

    # Accepte le state soit à plat (correct), soit imbriqué {state: {...}} (legacy)
    payload: dict[str, Any] = data.get("state", data) if "state" in data and isinstance(data.get("state"), dict) else data  # noqa: E501

    state["sales"]          = payload.get("sales",          state["sales"])
    state["rebonds"]        = payload.get("rebonds",        state["rebonds"])
    state["currentClient"]  = payload.get("currentClient",  state["currentClient"])
    state["catalogCustom"]  = payload.get("catalogCustom",  state["catalogCustom"])
    state["catalogOverrides"] = payload.get("catalogOverrides", state["catalogOverrides"])
    state["catalogDeleted"] = payload.get("catalogDeleted", state["catalogDeleted"])

    # sgAgenda : priorité au champ top-level, sinon legacy catalogOverrides.__sgAgenda
    sga = payload.get("sgAgenda")
    if sga is None:
        sga = (payload.get("catalogOverrides") or {}).get("__sgAgenda", state.get("sgAgenda", 0))
    try:
        state["sgAgenda"] = int(sga or 0)
    except (TypeError, ValueError):
        state["sgAgenda"] = 0

    if not isinstance(state["sales"], list):            state["sales"] = []
    if not isinstance(state["rebonds"], list):          state["rebonds"] = []
    if not isinstance(state["catalogCustom"], dict):    state["catalogCustom"] = {}
    if not isinstance(state["catalogOverrides"], dict): state["catalogOverrides"] = {}
    if not isinstance(state["catalogDeleted"], dict):   state["catalogDeleted"] = {}

    save_state(state)
    return jsonify({"ok": True})


@app.route("/api/reset_sales", methods=["POST"])
def api_reset_sales() -> Any:
    s = load_state()
    s["sales"] = []
    s["rebonds"] = []
    s["currentClient"] = None
    save_state(s)
    return jsonify({"ok": True, "state": s})


@app.route("/api/reset_all", methods=["POST"])
def api_reset_all() -> Any:
    s = default_state()
    save_state(s)
    return jsonify({"ok": True, "state": s})


@app.route("/export_pdf", methods=["POST"])
def export_pdf_route() -> Any:
    try:
        data      = request.get_json(silent=True) or {}
        general   = int(data.get("general") or 0)
        sales     = data.get("sales") or []
        rebonds   = data.get("rebonds") or []
        title     = safe_str(data.get("title") or "RECAP VENTES")
        sg_agenda = int(data.get("sgAgenda") or 0)
        if not isinstance(sales, list):   sales = []
        if not isinstance(rebonds, list): rebonds = []

        path = export_pdf(
            general=general, sales=sales, rebonds=rebonds,
            title=title, sg_agenda=sg_agenda,
        )
        return jsonify({
            "ok": True,
            "path": path,
            "folder": os.path.dirname(path),
            "filename": os.path.basename(path),
        })
    except Exception as e:
        log.exception("Échec de l'export PDF.")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/open_folder", methods=["POST"])
def open_folder_route() -> Any:
    """Ouvre le dossier de données dans l'explorateur de fichiers.

    Sécurité : le dossier est validé pour qu'il soit bien le dossier de données
    de l'application — on n'ouvre jamais un chemin arbitraire venant du client.
    """
    try:
        app_dir = os.path.realpath(get_app_data_dir())
        data    = request.get_json(silent=True) or {}
        raw     = safe_str(data.get("folder") or "")
        # Valider que le dossier demandé est le dossier app ou un sous-dossier
        candidate = os.path.realpath(raw) if raw else app_dir
        if not candidate.startswith(app_dir):
            candidate = app_dir

        if sys.platform.startswith("win"):
            os.startfile(candidate)  # type: ignore[attr-defined]
        elif sys.platform.startswith("darwin"):
            import subprocess
            subprocess.Popen(["open", candidate])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", candidate])

        return jsonify({"ok": True})
    except Exception as e:
        log.exception("Impossible d'ouvrir le dossier.")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7000, debug=False)
