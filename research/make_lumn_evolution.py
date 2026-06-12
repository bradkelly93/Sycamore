"""Render the LUMN evolution infographic (research/LUMN_evolution.png).

Single-figure summary of Lumen's corporate evolution for interview prep:
era bands, annual revenue bars, year-end share-price overlay (log),
numbered event markers + key, and three era snapshot cards.

Revenue 2021-2025 and balance-sheet figures are from SEC filings [P];
pre-2021 revenue and all share prices are approximate year-end values
from public records [S] -- this graphic is narrative, not for math.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

INK = "#1a2332"
MUTED = "#5b6776"
BLUE = "#0e5fa8"
GREEN = "#0b7a5c"
RED = "#b3392e"
AMBER = "#9a6b00"
PAPER = "#f7f8fa"

# ---- data -------------------------------------------------------------
years = list(range(2011, 2026))
revenue = [15.4, 18.4, 18.1, 18.0, 17.9, 17.5, 17.7, 23.4, 22.4, 20.7,
           19.7, 17.5, 14.6, 13.1, 12.4]          # $B; 2011-20 approx [S], 2021-25 [P]
rev_2025_pf = 11.7                                  # pro forma ex-FTTH [P]

px_years = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020,
            2021, 2022, 2023, 2024, 2025, 2026.45]
px = [37.2, 39.1, 31.8, 39.6, 25.2, 23.8, 16.7, 15.2, 13.2, 9.8,
      12.6, 5.2, 1.8, 5.4, 7.9, 8.49]              # year-end approx, unadjusted [S]

eras = [
    (2010.5, 2017.0, "#dce8f5", "THE BABY BELL ERA", "Qwest deal: copper, voice,\nand a fat dividend"),
    (2017.0, 2022.0, "#d9efe9", "THE ENTERPRISE BET", "Level 3: fiber backbone,\n~$35B debt, write-offs begin"),
    (2022.0, 2024.2, "#f7e1de", "THE RECKONING", "Dividend killed, asset sales,\nstock hits ~$1"),
    (2024.2, 2026.0, "#f7edd8", "THE RESCUE & PIVOT", "TSA restructuring +\nAI prepayments"),
    (2026.0, 2026.6, "#dff0e3", "AI\nBACKBONE", ""),
]

events = [
    (2011.3, "Buys Qwest — $22.4B EV incl. $11.8B debt. A Baby Bell built on copper."),
    (2013.1, "Dividend cut #1 (−26%). Stock's worst day in 30 yrs."),
    (2017.85, "Buys Level 3 — ~$34B with debt. Pivot to enterprise fiber; CEO Jeff Storey."),
    (2019.1, "Dividend cut #2 (−54%). Goodwill write-offs begin (~$26.5B total by 2025)."),
    (2020.7, "Rebrands CenturyLink → 'Lumen'. Revenue melts ~5-10%/yr."),
    (2022.5, "Shrinks to survive: sells LatAm ($2.7B) + 20-state ILEC (~$5.6B). Dividend ELIMINATED. Kate Johnson named CEO (Nov)."),
    (2023.8, "Near-death: stock ~$1, debt complex marked ~67¢ on the dollar."),
    (2024.25, "TSA debt restructuring closes — maturities pushed to 2029+. S&P calls it Selective Default."),
    (2024.6, "AI pivot lands: $5B PCF deals (MSFT, Meta, AWS, Google) → ~$13B by Feb '26. Stock ~4x off the low."),
    (2026.1, "AT&T buys consumer fiber for $5.75B. Debt $17.8B → $13.1B; upgraded by all 3 agencies."),
]

cards = [
    ("2011  ·  THE BABY BELL", "#dce8f5", BLUE, [
        ("Revenue ~$15.4B", "~half consumer phone/DSL,"),
        ("", "half business services"),
        ("Dividend $2.90/sh", "the entire investment case"),
        ("Identity", "rural copper cash cow"),
        ("Customers", "households, small towns"),
    ]),
    ("2019  ·  THE ENTERPRISE BET", "#d9efe9", "#0a6e54", [
        ("Revenue ~$22.4B", "~75% business / 25% consumer"),
        ("Debt ~$35B", "Level 3 deal leverage"),
        ("Dividend $1.00/sh", "just cut 54%"),
        ("Identity", "indebted fiber conglomerate"),
        ("Customers", "global enterprises + carriers"),
    ]),
    ("2026  ·  THE AI BACKBONE", "#dff0e3", GREEN, [
        ("Revenue ~$11.7B PF", "~84% business / 16% copper tail"),
        ("Debt $13.1B", "leverage ~3.5-3.8x, no wall to 2030"),
        ("Dividend $0", "cash → fiber build + paydown"),
        ("Identity", "enterprise fiber pure-play"),
        ("Customers", "MSFT, Meta, AWS, Google,\nAnthropic — ~$13B prepaid PCF"),
    ]),
]

# ---- figure -----------------------------------------------------------
fig = plt.figure(figsize=(20, 13.2), facecolor=PAPER)
gs = fig.add_gridspec(3, 1, height_ratios=[5.4, 1.85, 2.45],
                      hspace=0.16, left=0.05, right=0.95, top=0.90, bottom=0.045)

fig.text(0.05, 0.965, "THE EVOLUTION OF LUMEN TECHNOLOGIES (LUMN)",
         fontsize=27, fontweight="bold", color=INK, family="DejaVu Sans")
fig.text(0.05, 0.937,
         "From rural phone dividend machine → levered fiber conglomerate → near-bankruptcy → prepaid backbone for the AI buildout"
         "      |      1930s–2010 prologue: CenturyTel rolls up rural phone lines",
         fontsize=13.5, color=MUTED, family="DejaVu Sans")

# ---- main chart -------------------------------------------------------
ax = fig.add_subplot(gs[0])
ax.set_facecolor("white")

for x0, x1, color, title, sub in eras:
    ax.axvspan(x0, x1, color=color, alpha=0.85, zorder=0)
    cx = (x0 + x1) / 2
    ax.text(cx, 26.6, title, ha="center", va="top", fontsize=12.5,
            fontweight="bold", color=INK, family="DejaVu Sans", zorder=6)
    if sub:
        ax.text(cx, 25.1, sub, ha="center", va="top", fontsize=9.5,
                color=MUTED, family="DejaVu Sans", zorder=6)

bars = ax.bar(years, revenue, width=0.62, color=BLUE, zorder=3, label="Revenue ($B, left)")
bars[-1].set_alpha(0.95)
ax.bar([2025.0], [rev_2025_pf], width=0.62, color="#9bb9d6", zorder=2)
ax.bar([2025], [rev_2025_pf], width=0.62, color="none", zorder=4)
ax.text(2025.42, rev_2025_pf - 0.4, "'25 pro forma\nex-FTTH: $11.7B",
        fontsize=8.5, color=MUTED, family="DejaVu Sans", va="top")
for yr, rv in zip(years, revenue):
    ax.text(yr, rv + 0.35, f"{rv:.1f}", ha="center", fontsize=9,
            color=INK, family="DejaVu Sans", zorder=5)

ax.set_xlim(2010.5, 2026.6)
ax.set_ylim(0, 27.2)
ax.set_ylabel("Revenue ($B)", fontsize=12, color=BLUE, family="DejaVu Sans")
ax.set_xticks(list(range(2011, 2027)))
ax.set_xticklabels([f"'{y % 100:02d}" for y in range(2011, 2027)], fontsize=10.5)
ax.tick_params(colors=MUTED)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)

ax2 = ax.twinx()
ax2.set_yscale("log")
ax2.plot(px_years, px, color=RED, lw=2.6, marker="o", ms=4.5, zorder=7,
         label="Share price (year-end, log, right)")
ax2.set_ylim(0.7, 70)
ax2.set_yticks([1, 5, 10, 20, 40])
ax2.set_yticklabels(["$1", "$5", "$10", "$20", "$40"], fontsize=10.5)
ax2.set_ylabel("Share price (log scale)", fontsize=12, color=RED, family="DejaVu Sans")
ax2.tick_params(colors=RED)
for spine in ("top",):
    ax2.spines[spine].set_visible(False)

ax2.annotate("~$1.00\nJul '24 low", xy=(2023.95, 1.05), xytext=(2022.7, 2.1),
             fontsize=9, color=RED, family="DejaVu Sans", fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
ax2.annotate("$11.95 high\nNov '25", xy=(2025.45, 11.95), xytext=(2025.35, 26),
             fontsize=9, color=RED, family="DejaVu Sans", fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
ax2.plot([2025.45], [11.95], marker="^", ms=7, color=RED)
ax2.annotate("$8.49\nJun '26", xy=(2026.45, 8.49), xytext=(2026.0, 3.1),
             fontsize=10, color=RED, family="DejaVu Sans", fontweight="bold",
             arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
ax2.plot([2025.7], [6.0], marker="*", ms=16, color=GREEN, zorder=9)
ax2.text(2025.62, 4.4, "entry ~$6", fontsize=9.5, color=GREEN,
         fontweight="bold", family="DejaVu Sans", ha="center")

# numbered event markers
for i, (x, _) in enumerate(events, start=1):
    ax.axvline(x, color=MUTED, lw=0.8, ls=":", alpha=0.55, zorder=1)
    ax.scatter([x], [22.6], s=300, color=INK, zorder=8)
    ax.text(x, 22.6, str(i), ha="center", va="center", fontsize=10.5,
            color="white", fontweight="bold", family="DejaVu Sans", zorder=9)

h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.385, 1.0),
          fontsize=10, frameon=False, ncol=2)

# ---- event key --------------------------------------------------------
axk = fig.add_subplot(gs[1])
axk.axis("off")
ncol, per = 2, 5
for i, (x, label) in enumerate(events, start=1):
    col = (i - 1) // per
    row = (i - 1) % per
    cx = 0.005 + col * 0.5
    cy = 0.92 - row * 0.21
    axk.scatter([cx], [cy], s=240, color=INK, transform=axk.transAxes,
                clip_on=False, zorder=3)
    axk.text(cx, cy, str(i), ha="center", va="center", fontsize=9.5, color="white",
             fontweight="bold", family="DejaVu Sans", transform=axk.transAxes, zorder=4)
    axk.text(cx + 0.016, cy, label, ha="left", va="center", fontsize=10.3,
             color=INK, family="DejaVu Sans", transform=axk.transAxes)

# ---- snapshot cards ---------------------------------------------------
axc = fig.add_subplot(gs[2])
axc.axis("off")
card_w, gap = 0.30, 0.026
for j, (title, bg, accent, rows) in enumerate(cards):
    x0 = 0.005 + j * (card_w + gap)
    box = FancyBboxPatch((x0, 0.03), card_w, 0.93,
                         boxstyle="round,pad=0.008,rounding_size=0.012",
                         transform=axc.transAxes, facecolor=bg,
                         edgecolor=accent, lw=1.6, zorder=2)
    axc.add_patch(box)
    axc.text(x0 + 0.012, 0.885, title, fontsize=13, fontweight="bold",
             color=accent, family="DejaVu Sans", transform=axc.transAxes, zorder=3)
    y = 0.745
    for k, v in rows:
        if k:
            axc.text(x0 + 0.012, y, k, fontsize=10.6, fontweight="bold",
                     color=INK, family="DejaVu Sans", transform=axc.transAxes, zorder=3)
        axc.text(x0 + 0.118, y, v, fontsize=10.2, color=MUTED,
                 family="DejaVu Sans", transform=axc.transAxes, zorder=3, va="top" if "\n" in v else "center")
        y -= 0.135
    # arrow between cards
    if j < 2:
        axc.annotate("", xy=(x0 + card_w + gap - 0.004, 0.5),
                     xytext=(x0 + card_w + 0.006, 0.5),
                     xycoords=axc.transAxes, textcoords=axc.transAxes,
                     arrowprops=dict(arrowstyle="-|>", color=INK, lw=2.4))

fig.text(0.05, 0.012,
         "Sources: revenue 2021-25 & balance-sheet items from SEC filings [P]; pre-2021 revenue and share prices approximate year-end "
         "values from public records [S]; pro-forma and leverage figures computed [C] — see research/LUMN_deep_dive_2026-06-11.md for the full ledger. "
         "Prices unadjusted for dividends. Narrative graphic — not for precise math.",
         fontsize=9.3, color=MUTED, family="DejaVu Sans")

fig.savefig("research/LUMN_evolution.png", dpi=160, facecolor=PAPER,
            bbox_inches="tight")
print("saved research/LUMN_evolution.png")
