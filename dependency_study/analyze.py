"""Paired seed-level contrasts and figures from independently recomputed rows."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np


def interval(values, seed=73103):
    x = np.array(values, dtype=float)
    rng = np.random.default_rng(seed)
    draws = x[rng.integers(0, len(x), (10000, len(x)))].mean(axis=1)
    return dict(n=len(x), mean=float(x.mean()), low=float(np.quantile(draws, .025)),
                high=float(np.quantile(draws, .975)),
                positive=int(np.sum(x > 1e-9)), zero=int(np.sum(abs(x) <= 1e-9)),
                negative=int(np.sum(x < -1e-9)))


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--figures", type=Path, default=Path("paper/generated/dependency_study"))
    args = p.parse_args()
    source = args.directory / "analysis" / "recomputed.json"
    rows = json.loads(source.read_text())
    by_key = {(r["capacity"], r["topology"], r["policy"], r["seed"]): r for r in rows}
    capacities = sorted({r["capacity"] for r in rows})
    topologies = ["independent", "cpfp", "chain", "star", "mixed"]
    metrics = ["mean_burden", "unrelated_burden", "parent_burden", "gini", "confirmation_incidence",
               "first_fee_sat", "first_utilization", "first_fee_capture", "parent_dependency_premium"]
    cells, contrasts, topology_contrasts = [], [], []
    for capacity in capacities:
        for topology in topologies:
            seeds = sorted({r["seed"] for r in rows if r["capacity"] == capacity and r["topology"] == topology})
            for policy in ("scalar", "ancestor", "cluster"):
                selected = [by_key[(capacity, topology, policy, seed)] for seed in seeds]
                cells.append(dict(capacity=capacity, topology=topology, policy=policy,
                    metrics={m: interval([r[m] for r in selected]) for m in metrics}))
            for comparator in ("scalar", "ancestor"):
                contrasts.append(dict(capacity=capacity, topology=topology, comparator=comparator,
                    metrics={m: interval([by_key[(capacity, topology, "cluster", seed)][m] -
                                           by_key[(capacity, topology, comparator, seed)][m] for seed in seeds])
                             for m in metrics}))
            if topology != "independent":
                topology_contrasts.append(dict(capacity=capacity, topology=topology, selector="cluster",
                    metrics={m: interval([by_key[(capacity, topology, "cluster", seed)][m] -
                                           by_key[(capacity, "independent", "cluster", seed)][m] for seed in seeds])
                             for m in metrics}))
    result = dict(repeated_unit="40 fee-assignment seeds, paired across selectors and regenerated legal topology sets",
        bootstrap_replicates=10000, bootstrap_seed=73103,
        inference_scope="Exploratory finite synthetic fee distribution; no multiple-testing-adjusted confirmatory or Mainnet claims",
        cells=cells, contrasts=contrasts, topology_contrasts=topology_contrasts,
        invariance="In an equal-weight, simultaneous-arrival cohort, identical per-block confirmation counts imply identical aggregate delay multiset, mean and Gini; priority redistribution alone changes identities, not this distribution.")
    (source.parent / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    with (source.parent / "contrasts.csv").open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(["capacity", "topology", "comparator", "metric", "n", "mean", "low", "high", "positive", "zero", "negative"])
        for row in contrasts:
            for metric, value in row["metrics"].items():
                writer.writerow([row["capacity"], row["topology"], row["comparator"], metric, *[value[k] for k in ("n", "mean", "low", "high", "positive", "zero", "negative")]])
    args.figures.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "font.family": "DejaVu Sans", "pdf.fonttype": 42,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.6), layout="constrained")
    panels = [("unrelated_burden", "Unrelated delay change (blocks)", 600),
              ("first_fee_capture", "First-block fee-capture ratio change", 1),
              ("gini", "Confirmation-burden Gini change", 1)]
    for ax, (metric, title, divisor) in zip(axes, panels):
        a = np.array([[next(c for c in contrasts if c["capacity"] == cap and c["topology"] == topo and c["comparator"] == "scalar")["metrics"][metric]["mean"] / divisor
                       for cap in capacities] for topo in topologies[1:]])
        limit = max(abs(a).max(), .01)
        im = ax.imshow(a, cmap="RdBu_r", vmin=-limit, vmax=limit, aspect="auto")
        for i in range(a.shape[0]):
            for j in range(a.shape[1]):
                ax.text(j, i, f"{a[i,j]:.2f}", ha="center", va="center", fontsize=8,
                        color="white" if abs(a[i,j]) > .6*limit else "black")
        ax.set_xticks(range(len(capacities)), capacities)
        ax.set_yticks(range(4), ["CPFP", "Chain", "Star", "Mixed"])
        ax.set_xlabel("Transaction slots per block")
        ax.set_title(title, fontsize=9)
    fig.savefig(args.figures / "allocation_tradeoffs.pdf", bbox_inches="tight")
    fig.savefig(args.figures / "allocation_tradeoffs.png", bbox_inches="tight", dpi=180)
    plt.close(fig)
    table = []
    for cap in capacities:
        if table:
            table.append(r"\addlinespace[3pt]")
        for topo in topologies[1:]:
            c = next(c for c in contrasts if c["capacity"]==cap and c["topology"]==topo and c["comparator"]=="scalar")
            m = c["metrics"]
            delay = m["unrelated_burden"]
            ci = r"\CI{" + f"{delay['low']/600:.3f}" + "}{" + f"{delay['high']/600:.3f}" + "}"
            table.append(f"{cap} & {topo.upper() if topo=='cpfp' else topo.title()} & {delay['mean']/600:.3f} & {ci} & {m['first_utilization']['mean']:.3f} & {m['gini']['mean']:.3f} \\\\")
    (args.figures / "contrast_table.tex").write_text("\n".join(table) + "\n\\bottomrule\n")
    print(json.dumps(dict(cells=len(cells), paired_policy_contrasts=len(contrasts), topology_contrasts=len(topology_contrasts))))


if __name__ == "__main__":
    main()
