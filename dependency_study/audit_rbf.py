"""Independently rederive RBF fees and comparator boundaries from raw transactions."""
import argparse
import json
from pathlib import Path

from .independent import outcomes, parse_block, parse_transaction, rbf_accept, reconstruct, require


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("directory", type=Path)
    args = ap.parse_args()
    verified = []
    for case in ("single_conflict", "conflict_with_child", "unconfirmed_ancestor"):
        path = args.directory / f"{case}.json"
        d = json.loads(path.read_text())
        original = d["original_fixture"]
        rows = reconstruct(original)
        by_id = {r["txid"]: r for r in rows}
        values = {}
        for raw in list(original["funding_transactions"].values()) + [r["raw"] for r in original["transactions"]]:
            tx = parse_transaction(raw)
            values.update({(tx["txid"], i): v["value_sat"] for i, v in enumerate(tx["outputs"])})
        removed = [by_id[x] for x in d["evicted"]]
        require(set(d["before"]) - set(d["after"]) == set(d["evicted"]), "replacement set mismatch")
        core_min = d["thresholds"]["cluster"]
        trial_map = {t["fee_sat"]: t for t in d["trials"]}
        require(trial_map[core_min]["response"]["allowed"] and not trial_map[core_min-1]["response"]["allowed"],
                "Core adjacent boundary not observed")
        for t in d["trials"]:
            tx = parse_transaction(t["candidate"]["raw"])
            fee = sum(values[(v["txid"], v["vout"])] for v in tx["inputs"]) - sum(v["value_sat"] for v in tx["outputs"])
            require(fee == t["fee_sat"] == t["candidate"]["fee_sat"] and tx["vsize"] == 326,
                    "candidate raw fee or shape mismatch")
            require(tx["txid"] == t["response"]["txid"] and tx["wtxid"] == t["response"]["wtxid"],
                    "candidate/response binding mismatch")
            require(t["response"].get("allowed", False) == (fee >= core_min), "Core fee probes inconsistent")
        candidate = parse_transaction(trial_map[core_min]["candidate"]["raw"])
        candidate_prevouts = {(v["txid"], v["vout"]) for v in candidate["inputs"]}
        conflicts = [by_id[r["txid"]] for r in original["transactions"] if
                     candidate_prevouts.intersection((v["txid"],v["vout"]) for v in parse_transaction(r["raw"])["inputs"])]
        ancestor_ids = {v["txid"] for v in candidate["inputs"] if v["txid"] in by_id and v["txid"] not in d["evicted"]}
        ancestors = [by_id[x] for x in ancestor_ids]
        for policy in ("scalar", "ancestor"):
            for fee in (d["thresholds"][policy] - 1, d["thresholds"][policy]):
                allowed = rbf_accept(policy, dict(fee_sat=fee, vsize=326, replaceable=True,
                    new_unconfirmed_parents=False), conflicts, removed, ancestors)
                require(allowed == (fee == d["thresholds"][policy]), "independent comparator boundary mismatch")
        mined = parse_block(d["final_block"])
        ids = {t["txid"] for t in mined["transactions"][1:]}
        require(ids == set(d["after"]), "post-replacement block mismatch")
        events = [dict(txid=t["txid"], cause="replaced" if t["txid"] in d["evicted"] else "confirmed",
                       time=300 if t["txid"] in d["evicted"] else 600) for t in original["transactions"]]
        require(events == d["original_cohort_events"], "RBF cause attribution mismatch")
        require(outcomes(events,1200) == d["original_cohort_outcomes"], "RBF competing outcomes mismatch")
        verified.append(dict(case=case, thresholds=d["thresholds"], raw_candidate_probes=len(d["trials"])))
    report = dict(status="passed", cases=verified, scope="Raw signed candidate fees, fixed shape, saved Core responses, independent comparator boundaries and replacement events")
    (args.directory / "verification.json").write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
