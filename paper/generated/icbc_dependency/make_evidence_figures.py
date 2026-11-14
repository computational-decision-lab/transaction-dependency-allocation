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
import subprocess


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
    """Compile the editable standalone TikZ mechanism illustration."""
    source = OUT / "icbc_confirmation_order_mechanism.tex"
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", source.name],
        cwd=OUT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    subprocess.run(
        ["pdftoppm", "-png", "-r", "180", "-singlefile", source.with_suffix(".pdf").name,
         source.with_suffix("").name],
        cwd=OUT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


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
    save(fig, "icbc_result_surface")


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
    save(fig, "icbc_comparator_effects")


if __name__ == "__main__":
    confirmation_order()
    result_surface()
    comparator_effects()
    print(f"Wrote figures to {OUT}")
