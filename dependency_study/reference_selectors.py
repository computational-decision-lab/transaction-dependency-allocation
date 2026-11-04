"""Independent set-closure reference for the two declared research selectors.

The experiment runner does not import this module. Unlike its recursive package
builder, this implementation obtains transitive ancestor sets by fixed-point
closure, then orders the winning set by a separate topological elimination.
"""
from fractions import Fraction


def reference_select(transactions, budget, policy):
    if policy not in ("scalar", "ancestor"):
        raise ValueError("unknown reference policy")
    rows = {row["txid"]: row for row in transactions}
    pending = set(rows)
    ancestors = {key: set(row["parents"]) & pending for key, row in rows.items()}
    changed = True
    while changed:
        changed = False
        for key in sorted(rows):
            expanded = ancestors[key] | set().union(*(ancestors[p] for p in ancestors[key]))
            if key in expanded:
                raise ValueError("cyclic reference graph")
            if expanded != ancestors[key]:
                ancestors[key] = expanded
                changed = True
    output = []
    while pending:
        choices = []
        for key in sorted(pending):
            required = ancestors[key] & pending
            if policy == "scalar" and required:
                continue
            package = required | {key} if policy == "ancestor" else {key}
            weight = sum(rows[p]["weight"] for p in package)
            if weight > budget:
                continue
            rate = Fraction(sum(rows[p]["fee_sat"] for p in package),
                            sum(rows[p]["vsize"] for p in package))
            choices.append((-rate, key, package, weight))
        if not choices:
            break
        _, winner, package, weight = min(choices, key=lambda x: (x[0], x[1]))
        # Fixtures have one input, so each ancestor package is a chain.
        # More general graphs may have multiple valid parent-before-child orders;
        # compare selected sets in that case rather than asserting a unique order.
        rest = set(package)
        while rest:
            ready = sorted(p for p in rest if not (set(rows[p]["parents"]) & rest))
            if not ready:
                raise ValueError("unorderable reference package")
            chosen = ready[0]
            output.append(chosen)
            rest.remove(chosen)
        pending.difference_update(package)
        budget -= weight
    return output
