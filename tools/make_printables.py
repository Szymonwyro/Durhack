"""Prep tool: generate A4 printables (ArUco markers + chessboard) as vector PDFs.

Usage: .venv/bin/python tools/make_printables.py
Print at 100% / "Actual size" and measure the 100 mm scale bar on every page.
"""
from pathlib import Path

import cv2
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "printables"
MARKER_MM = 180.0
DICT_NAME = "DICT_4X4_50"
# Only these IDs are used: corner markers flat on the floor (config/markers.json)
CORNER_ROLES = {
    1: "CORNER ID 1 = world origin (0, 0). Flat on floor",
    3: "CORNER ID 3 = opposite ID 1 (1.5, 1.5). Flat on floor",
    5: "CORNER ID 5 = along +y from ID 1 (0, 1.5). Flat on floor",
    7: "CORNER ID 7 = along +x from ID 1 (1.5, 0). Flat on floor",
}


def scale_bar(c, x, y, length_mm=100, note=True):
    c.setLineWidth(0.6)
    c.setFillColorRGB(0, 0, 0)
    c.rect(x, y, length_mm * mm, 3 * mm, stroke=1, fill=0)
    for i in range(0, length_mm + 1, 10):
        h = 5 * mm if i % 50 == 0 else 3.5 * mm
        c.line(x + i * mm, y, x + i * mm, y + h)
        if i % 50 == 0:
            c.setFont("Helvetica", 7)
            c.drawCentredString(x + i * mm, y + 6 * mm, f"{i}")
    if not note:
        return
    c.setFont("Helvetica", 8)
    c.drawString(x, y - 4.5 * mm, f"Scale check: this bar must measure exactly {length_mm} mm. If not, reprint at 100% / Actual size.")


def markers():
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    W, H = A4
    c = canvas.Canvas(str(OUT / "markers.pdf"), pagesize=A4)
    for mid in range(10):
        cells = cv2.aruco.generateImageMarker(d, mid, 6)  # 4x4 bits + 1-cell black border
        n = cells.shape[0]
        cell = MARKER_MM * mm / n
        x0 = (W - MARKER_MM * mm) / 2
        y0 = H - 30 * mm - MARKER_MM * mm
        # solid black square first, then white bit cells on top (no seams between black cells)
        c.setFillColorRGB(0, 0, 0)
        c.rect(x0, y0, MARKER_MM * mm, MARKER_MM * mm, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        for r in range(n):
            for col in range(n):
                if cells[r, col] != 0:
                    c.rect(x0 + col * cell, y0 + (n - 1 - r) * cell, cell, cell, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 9)
        c.drawCentredString(W / 2, y0 - 7 * mm, f"{DICT_NAME}  ID {mid}  |  black square = {MARKER_MM:.0f} mm")
        role = CORNER_ROLES.get(mid)
        if role:
            c.setFont("Helvetica-Bold", 11)
            c.drawCentredString(W / 2, y0 - 14 * mm, role)
        c.setFont("Helvetica", 8)
        c.drawCentredString(W / 2, y0 - 20 * mm, "Keep the white margin around the marker. Do not trim into it.")
        scale_bar(c, (W - 100 * mm) / 2, 30 * mm)
        c.showPage()
    c.save()


def chessboard(inner=(9, 6), sq_mm=25.0):
    cols, rows = inner[0] + 1, inner[1] + 1
    W, H = landscape(A4)
    c = canvas.Canvas(str(OUT / "chessboard.pdf"), pagesize=(W, H))
    bw, bh = cols * sq_mm * mm, rows * sq_mm * mm
    x0, y0 = (W - bw) / 2, H - 8 * mm - bh
    c.setFillColorRGB(0, 0, 0)
    for r in range(rows):
        for col in range(cols):
            if (r + col) % 2 == 0:
                c.rect(x0 + col * sq_mm * mm, y0 + r * sq_mm * mm, sq_mm * mm, sq_mm * mm, stroke=0, fill=1)
    c.setFont("Helvetica", 8)
    c.drawString(x0, y0 - 5 * mm, f"Chessboard {inner[0]}x{inner[1]} inner corners, {sq_mm:.0f} mm squares. Tape flat to rigid board.")
    scale_bar(c, x0, 11 * mm, note=False)
    c.setFont("Helvetica", 8)
    c.drawString(x0 + 105 * mm, 11 * mm, "<- must measure exactly 100 mm (print at 100% / Actual size)")
    c.showPage()
    c.save()


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    markers()
    chessboard()
    print("wrote", OUT / "markers.pdf", "and", OUT / "chessboard.pdf")
