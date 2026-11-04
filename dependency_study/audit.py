"""Validate saved artifacts and regenerate results without a running simulator."""
import argparse
from collections import defaultdict
import copy
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .independent import (cumulative_incidence, exact_max_fees, outcomes, parse_block,
                          parse_transaction, reconstruct, require, validate_selection)
from .reference_selectors import reference_select


def sat(v):
    return int(Decimal(str(v)) * 100000000)


def validate_clusters(data, rows):
    by_id = {t["txid"]: t for t in rows if t["txid"] in data["raw_mempool"]}
    adjacency = {x: set() for x in by_id}
    for x, t in by_id.items():
        for p in t["parents"]:
            if p in by_id:
                adjacency[x].add(p)
                adjacency[p].add(x)
    seen = set()
    for cluster in data["clusters"]:
        order = [x for c in cluster["chunks"] for x in c["txs"]]
        require(cluster["txcount"] == len(order), "cluster count mismatch")
        component, stack = set(), [order[0]]
        while stack:
            x = stack.pop()
            if x not in component:
                component.add(x)
                stack.extend(adjacency[x] - component)
        require(component == set(order), "cluster is not a graph connected component")
        validate_selection(list(by_id.values()), order, 2**40)
        require(not seen.intersection(order), "duplicate cluster membership")
        seen.update(order)
        require(cluster["clusterweight"] == sum(by_id[x]["weight"] for x in order), "cluster weight mismatch")
        previous = None
        for chunk in cluster["chunks"]:
            weight = sum(by_id[x]["weight"] for x in chunk["txs"])
            fee = sum(by_id[x]["fee_sat"] for x in chunk["txs"])
            require(weight == chunk["chunkweight"] and fee == sat(chunk["chunkfee"]), "chunk totals mismatch")
            rate = Fraction(fee, weight)
            require(previous is None or rate <= previous, "chunk rates not nonincreasing")
            previous = rate
    require(seen == set(by_id), "cluster coverage mismatch")
    last_w = last_f = 0
    previous = None
    for point in data["feerate_diagram"]:
        weight, fee = point["weight"], sat(point["fee"])
        if weight == 0:
            require(fee == 0, "invalid diagram origin")
            continue
        require(weight > last_w, "nonincreasing cumulative diagram weight")
        rate = Fraction(fee - last_f, weight - last_w)
        require(previous is None or rate <= previous, "nonconcave fee diagram")
        last_w, last_f, previous = weight, fee, rate
    require(last_w == sum(t["weight"] for t in by_id.values()), "diagram weight coverage")
    require(last_f == sum(t["fee_sat"] for t in by_id.values()), "diagram fee coverage")
    for txid, t in by_id.items():
        entry = data["raw_mempool"][txid]
        require(entry["weight"] == t["weight"] and entry["vsize"] == t["vsize"] and
                sat(entry["fees"]["base"]) == t["fee_sat"] and set(entry["depends"]) == set(t["parents"]),
                "mempool entry/raw transaction mismatch")


def verify_fixture(data):
    rows = reconstruct(data)
    validate_clusters(data, rows)
    if "trajectories" not in data:
        return []
    require(sorted(t["policy"] for t in data["trajectories"]) == ["ancestor", "cluster", "scalar"],
            "missing or duplicate selector")
    arrival_times = {entry["time"] for entry in data["raw_mempool"].values()}
    require(len(arrival_times) == 1, "arrival times not matched")
    origin = next(iter(arrival_times))
    results = []
    for trial in data["trajectories"]:
        require(trial["budget"] == data["matching"]["capacity"] * 1304 and
                trial["horizon_seconds"] == 4800, "trial budget or horizon differs from design")
        require(1 <= len(trial["blocks"]) <= 8, "invalid follow-up block count")
        remaining = {t["txid"]: t for t in rows}
        by_id = dict(remaining)
        expected_wtxids = {t["txid"]: parse_transaction(t["raw"])["wtxid"] for t in data["transactions"]}
        events = []
        for step, b in enumerate(trial["blocks"], 1):
            selected = b["txids"]
            validate_selection(list(remaining.values()), selected, trial["budget"])
            block = parse_block(b["raw_block"])
            require(b["step"] == step and b["time"] == 600 * step and
                    block["time"] == origin + 600 * step, "block schedule/raw timestamp mismatch")
            require(block["hash"] == b["hash"] and [t["txid"] for t in block["transactions"]][1:] == selected,
                    "raw block/template selection mismatch")
            require(all(t["wtxid"] == expected_wtxids[t["txid"]] for t in block["transactions"][1:]),
                    "mined witness differs from submitted transaction")
            if trial["policy"] == "cluster":
                require([t["txid"] for t in b["template"]["transactions"]] == selected, "Core template mismatch")
                for tx in b["template"]["transactions"]:
                    raw = parse_transaction(tx["data"])
                    require(raw["txid"] == tx["txid"] and tx["fee"] == by_id[tx["txid"]]["fee_sat"] and
                            tx["weight"] == raw["weight"] and raw["wtxid"] == expected_wtxids[tx["txid"]],
                            "GBT raw transaction mismatch")
            else:
                require(reference_select(list(remaining.values()), trial["budget"], trial["policy"]) == selected,
                        "counterfactual selection cannot be replayed")
            require(set(b["before"]["txids"]) == set(remaining), "snapshot before block mismatch")
            for x in selected:
                events.append(dict(txid=x, label=remaining[x]["label"], cause="confirmed", time=b["time"]))
                del remaining[x]
            require(set(b["after"]["txids"]) == set(remaining), "unclassified mempool disappearance")
            require(b["weight"] == sum(by_id[x]["weight"] for x in selected), "selected weight mismatch")
            require(b["fee_sat"] == sum(by_id[x]["fee_sat"] for x in selected), "selected fee mismatch")
        require(not remaining or len(trial["blocks"]) == 8, "incomplete follow-up")
        events.extend(dict(txid=x, label=t["label"], cause="censored", time=trial["horizon_seconds"])
                      for x, t in remaining.items())
        events.sort(key=lambda e: e["label"])
        require(events == trial["events"], "events cannot be regenerated from raw blocks")
        out = outcomes(events, trial["horizon_seconds"])
        require(out == trial["outcomes"], "outcome mismatch")
        burdens = [e["time"] if e["cause"] == "confirmed" else trial["horizon_seconds"] for e in events]
        direct_mean = sum(burdens) / len(burdens)
        pairwise_gini = sum(abs(x-y) for x in burdens for y in burdens) / (2 * len(burdens) * sum(burdens))
        direct_incidence = sum(e["cause"] == "confirmed" for e in events) / len(events)
        require(abs(out["mean_burden"] - direct_mean) < 1e-9 and
                abs(out["gini"] - pairwise_gini) < 1e-12 and
                abs(out["confirmation_incidence"] - direct_incidence) < 1e-12,
                "direct empirical-distribution crosscheck failed")
        first = trial["blocks"][0]
        opt = exact_max_fees(rows, trial["budget"])
        require(opt["fee_sat"] == first["exact_optimum"]["fee_sat"], "fee optimum mismatch")
        unrelated = outcomes([e for e in events if e["label"] >= 6], trial["horizon_seconds"])
        require(unrelated == trial["unrelated_outcomes"], "unrelated cohort mismatch")
        result = dict(seed=data["seed"], topology=data["topology"], capacity=data["matching"]["capacity"],
            policy=trial["policy"], mean_burden=out["mean_burden"], gini=out["gini"],
            confirmation_incidence=out["confirmation_incidence"], unrelated_burden=unrelated["mean_burden"],
            first_fee_sat=first["fee_sat"], first_utilization=first["weight"] / trial["budget"],
            first_fee_capture=first["fee_sat"] / opt["fee_sat"],
            block_counts=[len(b["txids"]) for b in trial["blocks"]],
            parent_burden=next(e["time"] for e in events if e["label"] == 0))
        dp = None
        for c in data["clusters"]:
            for chunk in c["chunks"]:
                if rows[0]["txid"] in chunk["txs"]:
                    dp = 4 * sat(chunk["chunkfee"]) / chunk["chunkweight"] - rows[0]["fee_sat"] / rows[0]["vsize"]
        result["parent_dependency_premium"] = dp
        results.append(result)
    for left in results:
        for right in results:
            lcounts = left["block_counts"] + [0] * (8 - len(left["block_counts"]))
            rcounts = right["block_counts"] + [0] * (8 - len(right["block_counts"]))
            if lcounts == rcounts:
                require(all(abs(left[m]-right[m]) < 1e-10 for m in ("mean_burden", "gini", "confirmation_incidence")),
                        "confirmation-count invariance violated")
    return results


def fault_tests(data):
    checks = {}
    def rejected(name, f):
        try:
            f()
        except (ValueError, KeyError):
            checks[name] = "rejected"
        else:
            raise ValueError("mutation survived: " + name)
    changed = copy.deepcopy(data)
    changed["transactions"][0]["fee_sat"] += 1
    rejected("altered_fee", lambda: reconstruct(changed))
    changed2 = copy.deepcopy(data)
    changed2["transactions"][0]["raw"] = changed2["transactions"][0]["raw"][:-2]
    rejected("truncated_raw_transaction", lambda: reconstruct(changed2))
    changed_witness = copy.deepcopy(data)
    raw = changed_witness["transactions"][0]["raw"]
    changed_witness["transactions"][0]["raw"] = raw[:-10] + ("ff" if raw[-10:-8] != "ff" else "00") + raw[-8:]
    rejected("changed_signature_witness", lambda: reconstruct(changed_witness))
    changed3 = copy.deepcopy(data)
    changed3["clusters"][0]["chunks"][0]["chunkweight"] += 4
    rejected("altered_chunk_weight", lambda: validate_clusters(changed3, reconstruct(changed3)))
    changed_clock = copy.deepcopy(data)
    changed_clock["trajectories"][0]["blocks"][0]["time"] += 600
    rejected("altered_scheduled_delay", lambda: verify_fixture(changed_clock))
    events = data["trajectories"][0]["events"]
    rejected("duplicate_terminal_event", lambda: outcomes(events + [events[0]], 4800))
    rejected("early_censoring_for_full_cohort_gini", lambda: outcomes([dict(txid="a", cause="censored", time=600)], 4800))
    raw = data["trajectories"][0]["blocks"][0]["raw_block"]
    rejected("corrupted_block_merkle_root", lambda: parse_block(raw[:72] + ("ff" if raw[72:74] != "ff" else "00") + raw[74:]))
    known = outcomes([dict(txid="a", cause="confirmed", time=600), dict(txid="b", cause="replaced", time=300),
                      dict(txid="c", cause="censored", time=1200)], 1200)
    require(abs(known["confirmation_incidence"] - 1/3) < 1e-12 and known["mean_burden"] == 1000,
            "competing risk closed-form benchmark failed")
    require(abs(known["gini"] - 2/15) < 1e-12, "closed-form Gini failed")
    checks["closed_form_competing_risk_and_gini"] = "passed"
    return checks


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("directory", type=Path)
    args = ap.parse_args()
    files = sorted((args.directory / "fixtures").glob("*.json"))
    design = json.loads((args.directory / "design.json").read_text())
    expected = len(design["seeds"]) * len(design["capacities"]) * len(design["topologies"])
    require(len(files) == expected, "incomplete experimental matrix")
    results, matching, checks, keys = [], {}, {}, set()
    for path in files:
        data = json.loads(path.read_text())
        rows = reconstruct(data)
        identity = (data["seed"], data["matching"]["capacity"], data["topology"])
        require(identity not in keys, "duplicate fixture identity")
        keys.add(identity)
        require(data["graph"] == design["topologies"][data["topology"]], "topology differs from design")
        require([t["label"] for t in rows] == list(range(design["n_transactions"])), "label cohort mismatch")
        by_id = {r["txid"]: r["label"] for r in rows}
        for row in rows:
            expected_edge = data["graph"].get(str(row["label"]))
            require([by_id[p] for p in row["parents"]] == ([] if expected_edge is None else [expected_edge[0]]),
                    "raw graph differs from declared treatment")
        arrivals = [t["arrival_index"] for t in data["transactions"]]
        require(arrivals == list(range(design["n_transactions"])), "arrival order differs from design")
        signature = [(t["label"], t["fee_sat"], t["weight"], t["vsize"], a) for t, a in zip(rows, arrivals)]
        key = data["seed"]
        require(key not in matching or matching[key] == signature, "cross-topology matching failed")
        matching[key] = signature
        results.extend(verify_fixture(data))
        if not checks:
            checks = fault_tests(data)
    require(keys == {(s, c, t) for s in design["seeds"] for c in design["capacities"] for t in design["topologies"]},
            "design matrix identities differ")
    output = args.directory / "analysis"
    output.mkdir(exist_ok=True)
    (output / "recomputed.json").write_text(json.dumps(results, indent=2) + "\n")
    report = dict(status="passed", fixtures=len(files), selector_trajectories=len(results),
                  matched_seed_groups=len(matching), fault_tests=checks,
                  counterfactual_reference="Independent fixed-point ancestor closure; not imported by generator",
                  outcome_crosscheck="Direct arithmetic mean, event fraction and pairwise-distance Gini",
                  verification_code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in Path(__file__).parent.glob("*.py")},
                  scope="Independent bytes/prevouts/block parsing, accounting and selector replay; Core validates scripts.",
                  raw_files={str(p.relative_to(args.directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k:v for k,v in report.items() if k != "raw_files"}, indent=2))


if __name__ == "__main__":
    main()
