"""Build evidence-focused ICBC figures from the released reported estimates.

The script deliberately uses only values already reported in the paper's
generated tables and prose.  It writes vector PDF and raster PNG variants so
the figures can be reused in the paper, blog, and release packet.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, Rectangle


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["DejaVu Serif"],
        "mathtext.fontset": "stix",
        "axes.titlesize": 10.5,
        "axes.labelsize": 9.5,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.fontsize": 8.2,
        "figure.dpi": 160,
        "savefig.dpi": 300,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def save(fig, stem: str) -> None:
    fig.savefig(OUT / f"{stem}.pdf", bbox_inches="tight", pad_inches=0.05)
    fig.savefig(OUT / f"{stem}.png", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def confirmation_order() -> None:
    """Illustrate identity reshuffling with equal confirmation counts."""
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 3.15), sharex=True)
    colors = {"P": "#e5c07b", "C": "#4f7cac", "U": "#7f8c8d", "V": "#9aa6b2"}
    names = {"P": "P  parent\n2 sat/vB", "C": "C  child\n100 sat/vB", "U": "U  unrelated\n40 sat/vB", "V": "V  unrelated\n39 sat/vB"}
    rows = [("Scalar fee-rate rule", [["U", "V"], ["P", "C"]]), ("Package-aware rule", [["P", "C"], ["U", "V"]])]
    for ax, (title, blocks) in zip(axes, rows):
        ax.set_xlim(-0.1, 2.1)
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.set_xticks([0.5, 1.5], ["Block 1", "Block 2"])
        ax.set_title(title, loc="left", pad=4, fontweight="bold")
        for b, txs in enumerate(blocks):
            ax.add_patch(Rectangle((b + 0.05, 0.12), 0.90, 0.72, fill=False, ec="#404040", lw=0.9))
            for i, tx in enumerate(txs):
                y = 0.52 if i == 0 else 0.20
                ax.add_patch(Rectangle((b + 0.12, y), 0.76, 0.23, fc=colors[tx], ec="white", lw=0.8))
                ax.text(b + 0.50, y + 0.115, names[tx], ha="center", va="center", fontsize=7.3, linespacing=0.95)
        ax.text(2.06, 0.50, "same 2 tx/block", ha="right", va="center", fontsize=7.2, color="#555555")
    # Brackets make the identity change explicit without implying measured user welfare.
    axes[1].annotate("U and V wait one\nadditional block", xy=(1.50, 0.56), xytext=(1.78, 0.92),
                     arrowprops=dict(arrowstyle="-[", lw=0.9, color="#8b0000", connectionstyle="arc3,rad=-0.15"),
                     fontsize=7.5, ha="center", color="#8b0000")
    axes[1].annotate("P is lifted by C's fee", xy=(0.50, 0.64), xytext=(0.46, 0.90),
                     arrowprops=dict(arrowstyle="->", lw=0.9, color="#1f4e79"), fontsize=7.5, ha="center", color="#1f4e79")
    fig.text(0.5, 0.012, "Illustrative example: both selectors yield waiting-time multiset {1, 1, 2, 2}; only the identities assigned to each block change.",
             ha="center", va="bottom", fontsize=7.5, color="#444444")
    fig.tight_layout(rect=(0, 0.07, 1, 1), h_pad=0.35)
    save(fig, "confirmation_order_mechanism")


def heatmap(ax, values, title, cmap, vmin, vmax, fmt="{:.2f}"):
    im = ax.imshow(values, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_xticks(range(4), ["2", "4", "6", "12"])
    ax.set_yticks(range(4), ["CPFP", "Chain", "Star", "Mixed"])
    ax.set_xlabel("Transaction slots per block")
    ax.set_title(title, pad=7)
    for r in range(values.shape[0]):
        for c in range(values.shape[1]):
            value = values[r, c]
            color = "white" if abs(value) > (vmax - vmin) * 0.42 else "#222222"
            ax.text(c, r, fmt.format(value), ha="center", va="center", fontsize=8.3, color=color)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.5)
    return im


def result_surface() -> None:
    """Show the complete reported Core-minus-Scalar result surface."""
    delay = np.array([[0.650, 0.258, 0.163, 0.000],
                      [0.667, 0.583, 0.283, 0.000],
                      [0.196, 0.546, 0.600, 0.000],
                      [0.667, 0.583, 0.283, 0.000]])
    fee = np.array([[0.00, 0.03, 0.09, 0.00],
                    [0.01, 0.07, 0.18, 0.00],
                    [0.00, 0.12, 0.35, 0.00],
                    [0.01, 0.07, 0.18, 0.00]])
    gini = np.array([[0.000, 0.000, 0.000, 0.000],
                     [0.026, 0.000, 0.000, 0.000],
                     [0.036, 0.018, 0.000, 0.000],
                     [0.026, 0.000, 0.000, 0.000]])
    fig, axes = plt.subplots(1, 3, figsize=(9.2, 3.25), constrained_layout=True)
    heatmap(axes[0], delay, "Unrelated burden change\n(blocks)", "RdPu", 0, 0.70)
    heatmap(axes[1], fee, "First-block fee-capture\nratio change", "Oranges", 0, 0.36)
    heatmap(axes[2], gini, "Confirmation-burden\nGini change", "Reds", 0, 0.04, "{:.3f}")
    fig.text(0.5, -0.025, "Core minus scalar selection; positive values mean more burden, fee capture, or Gini under Core. White 0.00 cells at 12 slots are uncongested controls.",
             ha="center", va="top", fontsize=7.4, color="#444444")
    save(fig, "result_surface")


def comparator_effects() -> None:
    """Compare the two reported baselines using one sign convention."""
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.1), gridspec_kw={"width_ratios": [1.05, 1.25]})
    # These are the reported four-slot-chain paired intervals.
    labels = ["Core − Scalar", "Core − Ancestor-package"]
    means = np.array([0.583, -0.042])
    lo = np.array([0.508, -0.079])
    hi = np.array([0.658, -0.008])
    y = np.arange(2)
    axes[0].axvline(0, color="#333333", lw=0.8)
    axes[0].errorbar(means, y, xerr=[means - lo, hi - means], fmt="o", color="#1f4e79", ecolor="#1f4e79", capsize=3, ms=5)
    axes[0].set_yticks(y, labels)
    axes[0].set_xlabel("Unrelated restricted burden change (blocks)")
    axes[0].set_title("Same four-slot chain", pad=7)
    axes[0].set_xlim(-0.17, 0.74)
    axes[0].grid(axis="x", color="#dddddd", lw=0.5)
    axes[0].text(0.02, -0.40, "95% paired-bootstrap intervals", transform=axes[0].transAxes, fontsize=7.4, color="#555555")

    # Precision extension: same sign convention, scalar baseline only.
    plabels = ["40 seeds", "100 seeds"]
    pmeans = np.array([0.583, 0.552])
    plo = np.array([0.508, 0.508])
    phi = np.array([0.658, 0.593])
    py = np.arange(2)
    axes[1].axvline(0, color="#333333", lw=0.8)
    axes[1].errorbar(pmeans, py, xerr=[pmeans - plo, phi - pmeans], fmt="o", color="#8b3a3a", ecolor="#8b3a3a", capsize=3, ms=5)
    axes[1].set_yticks(py, plabels)
    axes[1].set_xlabel("Core − Scalar (blocks)")
    axes[1].set_title("Precision check: four-slot chain", pad=7)
    axes[1].set_xlim(0.42, 0.73)
    axes[1].grid(axis="x", color="#dddddd", lw=0.5)
    fig.text(0.5, -0.04, "Positive means Core assigns more unrelated restricted waiting. The ancestor estimate is reported only for the four-slot chain; no unreported cells are inferred.",
             ha="center", va="top", fontsize=7.4, color="#444444")
    fig.tight_layout(rect=(0, 0.06, 1, 1), w_pad=1.2)
    save(fig, "comparator_effects")


if __name__ == "__main__":
    confirmation_order()
    result_surface()
    comparator_effects()
    print(f"Wrote figures to {OUT}")
