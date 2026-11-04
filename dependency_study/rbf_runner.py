"""Fixed-shape replacement thresholds, separated from block selection."""
import argparse
from decimal import Decimal
import gzip
import json
from pathlib import Path
import socket
import time

from .independent import outcomes, rbf_accept, require
from .runner import COMMIT, Node, dump, sign


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "use a fresh directory")
    args.output.mkdir(parents=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ledger = gzip.open(args.output / "rpc.jsonl.gz", "wt")
    node = Node(args.binary.resolve(), args.output / "node", ledger, port)
    cases = []
    try:
        node.start(1304 * 12)
        clock = int(time.time()) + 10000
        node.call("setmocktime", clock)
        mine_addr = node.call("getnewaddress", "", "bech32m", wallet=True)
        initial = node.call("generatetoaddress", 201, mine_addr)
        dump(args.output / "setup_blocks.json", [node.call("getblock", h, 0) for h in initial])
        for case in ("single_conflict", "conflict_with_child", "unconfirmed_ancestor"):
            require(not node.call("getrawmempool"), "RBF trial starts empty")
            coins = node.call("listunspent", 1, wallet=True)
            coin = max(coins, key=lambda c: c["amount"])
            prev = {k: coin[k] for k in ("txid", "vout", "amount")}
            funding = {prev["txid"]: node.call("getrawtransaction", prev["txid"])}
            addresses = [node.call("getnewaddress", "", "bech32m", wallet=True) for _ in range(6)]
            originals, ancestors = [], []

            def publish(previous, fee, parents):
                tx = sign(node, previous, addresses, fee)
                tx.update(parents=parents, label=len(originals), arrival_index=len(originals))
                require(node.call("testmempoolaccept", [tx["raw"]], 0)[0]["allowed"], "original invalid")
                node.call("sendrawtransaction", tx["raw"], 0)
                originals.append(tx)
                return tx

            def output(tx, i=0):
                return dict(txid=tx["txid"], vout=i, amount=float(Decimal(tx["outputs"][i]["value_sat"]) / 100000000))

            if case == "unconfirmed_ancestor":
                ancestor = publish(prev, 326, [])
                ancestors = [ancestor]
                prev = output(ancestor)
            conflict = publish(prev, 10000, [a["txid"] for a in ancestors])
            removed = [conflict]
            if case == "conflict_with_child":
                removed.append(publish(output(conflict), 30000, [conflict["txid"]]))
            if case == "unconfirmed_ancestor":
                publish(output(ancestor, 1), 20000, [ancestor["txid"]])
            before = node.call("getrawmempool", True)
            diagram_before = node.call("getmempoolfeeratediagram")
            trials = {}

            def test(fee):
                if fee not in trials:
                    candidate = sign(node, prev, addresses, fee)
                    response = node.call("testmempoolaccept", [candidate["raw"]], 0)[0]
                    trials[fee] = dict(candidate=candidate, response=response)
                return trials[fee]["response"].get("allowed", False)

            low, high = 0, sum(t["fee_sat"] for t in removed) + 1000
            require(not test(low), "unexpected zero-fee replacement")
            while not test(high):
                high *= 2
                require(high < 10000000, "unbounded fee search")
            while high - low > 1:
                middle = (low + high) // 2
                if test(middle):
                    high = middle
                else:
                    low = middle
            require(not test(high - 1) and test(high), "one-satoshi boundary failed")
            # Explicitly probe a broad grid and nearby values under the fixed pool.
            for fee in set(range(0, high, max(1, high // 20))) | set(range(max(0, high-5), high+6)):
                require(test(fee) == (fee >= high), "nonmonotone Core fee acceptance")
            thresholds = {"cluster": high}
            for policy in ("scalar", "ancestor"):
                for fee in range(0, 1000000):
                    candidate = dict(fee_sat=fee, vsize=326, replaceable=True, new_unconfirmed_parents=False)
                    if rbf_accept(policy, candidate, [conflict], removed, ancestors):
                        thresholds[policy] = fee
                        break
                require(policy in thresholds, "comparator threshold missing")
            accepted = trials[high]["candidate"]
            node.call("setmocktime", clock + 300)
            submitted = node.call("submitpackage", [accepted["raw"]], 0)
            after = node.call("getrawmempool", True)
            evicted = set(before) - set(after)
            require(evicted == {t["txid"] for t in removed}, "unexpected RBF eviction set")
            diagram_after = node.call("getmempoolfeeratediagram")
            remaining_order = [t["txid"] for t in originals if t["txid"] not in evicted] + [accepted["txid"]]
            clock += 600
            node.call("setmocktime", clock)
            mined = node.call("generateblock", mine_addr, remaining_order)
            events = [dict(txid=t["txid"], cause="replaced" if t["txid"] in evicted else "confirmed",
                           time=300 if t["txid"] in evicted else 600) for t in originals]
            result = dict(case=case, source_commit=COMMIT, node_flags=node.flags,
                          original_fixture=dict(transactions=originals, funding_transactions=funding),
                          thresholds=thresholds, trials=[dict(fee_sat=f, **trials[f]) for f in sorted(trials)],
                          before=before, after=after, diagram_before=diagram_before, diagram_after=diagram_after,
                          replacement_result=submitted, evicted=sorted(evicted),
                          final_block=node.call("getblock", mined["hash"], 0),
                          original_cohort_events=events, original_cohort_outcomes=outcomes(events, 1200),
                          search="integer bisection; fixed shape; adjacent boundary and monotonicity grid checked")
            dump(args.output / f"{case}.json", result)
            cases.append(dict(case=case, thresholds=thresholds))
            print(json.dumps(cases[-1]), flush=True)
        dump(args.output / "execution.json", dict(status="completed", cases=cases,
             limitations="Three specified fixtures; comparator policies are not Core or BIP125 implementations."))
    finally:
        node.close()
        ledger.close()


if __name__ == "__main__":
    main()
