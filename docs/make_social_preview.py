"""Render docs/social-preview.png (1280 x 640): what it checks, and the measured result.

    python docs/make_social_preview.py      # needs matplotlib
Upload the result under Settings -> General -> Social preview.
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

BG, INK, MUTED, PANEL = "#12151b", "#e6e9ef", "#9aa3b2", "#1b2029"
GOOD, WARN, BAD = "#45c08a", "#e3b341", "#ff8f6b"
TILES = [("100.0 %", "recall, 1 000 synthetic invoices", GOOD),
         ("0.0 %", "false alarms, same invoices", GOOD),
         ("8.4 %", "recall of the usual baseline", BAD)]
# The engine's own messages for the demo case (AA.00.0010, AA.00.0030, AA.00.0020 x20).
FINDINGS = [("error", "AA.00.0010 and AA.00.0030 may not be billed in one session", BAD),
            ("error", "AA.00.0020 billed 20 times in session 1; the limit is 15", BAD),
            ("rules", "YAML data  ·  a 160-line engine  ·  every finding explained", WARN)]

fig = plt.figure(figsize=(12.8, 6.4), dpi=100, facecolor=BG)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1280)
ax.set_ylim(0, 640)
ax.axis("off")
ax.text(64, 560, "tardoc-validator", color=INK, fontsize=40, fontweight="bold", va="center")
ax.text(64, 505, "Checks a Swiss outpatient service list against the TARDOC tariff rules",
        color=MUTED, fontsize=18, va="center")
for i, (value, label, colour) in enumerate(TILES):
    x = 64 + i * 392
    ax.add_patch(FancyBboxPatch((x, 330), 368, 120, boxstyle="round,pad=0,rounding_size=10",
                                facecolor=PANEL, edgecolor=colour, linewidth=2.5))
    ax.text(x + 24, 405, value, color=colour, fontsize=34, fontweight="bold", va="center")
    ax.text(x + 24, 355, label, color=INK, fontsize=14, va="center")
for i, (kind, text, colour) in enumerate(FINDINGS):
    y = 240 - i * 64
    ax.add_patch(FancyBboxPatch((64, y), 1152, 48, boxstyle="round,pad=0,rounding_size=8",
                                facecolor=PANEL, edgecolor="#2a303b", linewidth=1.2))
    ax.text(88, y + 24, kind, color=colour, fontsize=15, fontweight="bold", va="center",
            family="monospace")
    ax.text(180, y + 24, text, color=INK, fontsize=15, va="center", family="monospace")
ax.text(64, 40, "The engine decides; a language model only explains, and a guard checks the explanation.",
        color=MUTED, fontsize=15, style="italic", va="center")
out = Path(__file__).with_name("social-preview.png")
fig.savefig(out, facecolor=BG)
print(out)
