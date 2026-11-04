"""Execute legal, matched, signed Taproot fixtures against a pinned Core node."""
from __future__ import annotations

import argparse
import base64
from decimal import Decimal
import gzip
from hashlib import sha256
import http.client
import json
from pathlib import Path
import random
import socket
import subprocess
import time

from .independent import (exact_max_fees, outcomes, parse_block, parse_transaction, reconstruct,
                          require, select, validate_selection)

COMMIT = "6574cb40869b96b9ffc79c19dc8f4e467d60f321"
ROOT = Path(__file__).resolve().parents[1]
TOPOLOGIES = {
    "independent": {},
    "cpfp": {1: (0, 0)},
    "chain": {1: (0, 0), 2: (1, 0), 3: (2, 0)},
    "star": {i: (0, i - 1) for i in range(1, 6)},
    "mixed": {1: (0, 0), 2: (0, 1), 3: (2, 0)},
}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sat(value):
    return int(Decimal(str(value)) * 100000000)


class RPCError(RuntimeError):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


class Node:
    def __init__(self, binary, directory, ledger, port):
        self.binary, self.directory, self.ledger = binary, directory, ledger
        self.port, self.process, self.connection = port, None, None
        self.counter = 0
        self.wallet = None

    def start(self, budget, mocktime=None):
        self.directory.mkdir(parents=True, exist_ok=True)
        flags = ["-regtest", f"-datadir={self.directory}", "-listen=0", "-connect=0",
                 "-discover=0", "-networkactive=0", f"-rpcport={self.port}",
                 "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-persistmempool=0",
                 "-fallbackfee=0.00001", "-txindex=1", "-blockreservedweight=2000",
                 f"-blockmaxweight={2000 + budget + 1}", "-blockmintxfee=0",
                 "-keypool=100", "-dbcache=64", "-par=1"]
        clock = mocktime if mocktime is not None else getattr(self, "last_mocktime", None)
        if clock is not None:
            flags.append(f"-mocktime={clock}")
        self.stdout = (self.directory / "process.log").open("a")
        self.process = subprocess.Popen([str(self.binary), *flags], stdout=self.stdout, stderr=subprocess.STDOUT)
        for _ in range(200):
            if self.process.poll() is not None:
                raise RuntimeError((self.directory / "process.log").read_text()[-4000:])
            cookie = self.directory / "regtest" / ".cookie"
            if cookie.exists():
                self.auth = "Basic " + base64.b64encode(cookie.read_bytes().strip()).decode()
                try:
                    self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
                    self.call("getblockcount")
                    break
                except (OSError, RPCError, http.client.HTTPException):
                    self.connection.close()
            time.sleep(.05)
        else:
            raise RuntimeError("Core startup timed out")
        self.call("setnetworkactive", False)
        self.call("getnetworkinfo")
        self.call("getmempoolinfo")
        self.flags = flags
        if (self.directory / "regtest" / "wallets" / "study").exists():
            self.call("loadwallet", "study")
        else:
            self.call("createwallet", "study")
        self.wallet = "study"

    def call(self, method, *params, wallet=False, log=True, allow_error=False):
        if method == "setmocktime":
            self.last_mocktime = params[0]
        self.counter += 1
        request = dict(jsonrpc="2.0", id=self.counter, method=method, params=list(params))
        self.connection.request("POST", "/wallet/study" if wallet else "/",
                                json.dumps(request), {"Authorization": self.auth, "Content-Type": "application/json"})
        response = self.connection.getresponse()
        parsed = json.loads(response.read())
        if log:
            self.ledger.write(json.dumps(dict(request=request, response=parsed), separators=(",", ":")) + "\n")
        if parsed.get("error") and not allow_error:
            raise RPCError(parsed["error"])
        return parsed if allow_error else parsed["result"]

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.call("stop")
            finally:
                self.process.wait(timeout=30)
        if self.connection:
            self.connection.close()
        if hasattr(self, "stdout"):
            self.stdout.close()


def sign(node, prev, addresses, fee, tiny_outputs=False):
    total = sat(prev["amount"]) - fee
    require(total >= 6 * 1000, "funding insufficient for fixed transaction shape")
    values = [total - 5000, 1000, 1000, 1000, 1000, 1000] if tiny_outputs else [total // 6] * 6
    values[0] += total - sum(values)
    inp = [{"txid": prev["txid"], "vout": prev["vout"], "sequence": 4294967293}]
    outs = [{addr: format(Decimal(value) / 100000000, ".8f")} for addr, value in zip(addresses, values)]
    raw = node.call("createrawtransaction", inp, outs, 0, True)
    signed = node.call("signrawtransactionwithwallet", raw, wallet=True)
    require(signed["complete"], "incomplete signature")
    tx = parse_transaction(signed["hex"])
    decoded = node.call("decoderawtransaction", signed["hex"])
    require(all(tx[k] == decoded[k] for k in ("txid", "weight", "vsize")), "Core/parser disagreement")
    return dict(txid=tx["txid"], raw=signed["hex"], weight=tx["weight"],
                vsize=tx["vsize"], fee_sat=fee, outputs=tx["outputs"], prevout=prev)


def fixture(node, topology, seed, n=12, boundary=False):
    require(not node.call("getrawmempool"), "fixture requires empty mempool")
    graph = {i: (i-1, 0) for i in range(1, n)} if boundary else TOPOLOGIES[topology]
    rng = random.Random(seed)
    # Unique rates eliminate txid/arrival tie breaking from matched contrasts.
    rates = rng.sample(range(2, 121), n)
    rates[0] = 1
    rates[1] = 160 + seed % 19
    fees = [(rate * 326 + i) for i, rate in enumerate(rates)] if not boundary else [1000] * n
    coins = node.call("listunspent", 1, 9999999, [], True, {"minimumAmount": "0.20000000"}, wallet=True, log=False)
    coins.sort(key=lambda x: -sat(x["amount"]))
    require(len(coins) >= n - len(graph), "not enough confirmed template inputs")
    addresses = [node.call("getnewaddress", "", "bech32m", wallet=True) for _ in range(6)]
    rows, funding, rejections, root = [], {}, [], 0
    for i in range(n):
        if i in graph:
            parent, vout = graph[i]
            prev = dict(txid=rows[parent]["txid"], vout=vout,
                        amount=float(Decimal(rows[parent]["outputs"][vout]["value_sat"]) / 100000000))
            parents = [prev["txid"]]
        else:
            prev = {k: coins[root][k] for k in ("txid", "vout", "amount")}
            root += 1
            funding[prev["txid"]] = node.call("getrawtransaction", prev["txid"])
            parents = []
        row = sign(node, prev, addresses, fees[i], tiny_outputs=boundary)
        row.update(label=i, parents=parents, arrival_index=i)
        acceptance = node.call("testmempoolaccept", [row["raw"]], 0)[0]
        row["acceptance"] = acceptance
        rows.append(row)
        if acceptance.get("allowed"):
            node.call("sendrawtransaction", row["raw"], 0)
        else:
            rejections.append(dict(label=i, response=acceptance))
            require(boundary and i == 64, "unexpected fixture rejection")
            break
    result = dict(topology=topology, seed=seed, transactions=rows, funding_transactions=funding,
                  rejections=rejections, graph={str(k): list(v) for k, v in graph.items()},
                  fee_assignment=fees, node_flags=node.flags)
    reconstructed = reconstruct(result)
    require(len({t["weight"] for t in reconstructed}) == 1, "template weights differ")
    result["raw_mempool"] = node.call("getrawmempool", True)
    result["mempool_sequence"] = node.call("getrawmempool", False, True)
    seen, clusters = set(), []
    for txid in result["raw_mempool"]:
        if txid not in seen:
            cluster = node.call("getmempoolcluster", txid)
            clusters.append(cluster)
            seen.update(x for c in cluster["chunks"] for x in c["txs"])
    result["clusters"] = clusters
    result["feerate_diagram"] = node.call("getmempoolfeeratediagram")
    return result


def mine_trajectory(node, data, policy, budget, mine_address, start, horizon=8):
    rows = reconstruct(data)
    by_id = {t["txid"]: t for t in rows}
    remaining = dict(by_id)
    events, blocks = [], []
    for step in range(1, horizon + 1):
        node.call("setmocktime", start + 600 * step)
        before = node.call("getrawmempool", False, True)
        require(set(before["txids"]) == set(remaining), "unexpected mempool removal")
        if policy == "cluster":
            gbt = node.call("getblocktemplate", {"rules": ["segwit"]})
            chosen = [t["txid"] for t in gbt["transactions"]]
        else:
            gbt = None
            chosen = select(list(remaining.values()), budget, policy)
        validate_selection(list(remaining.values()), chosen, budget)
        optimum = exact_max_fees(list(remaining.values()), budget) if step == 1 else None
        generated = node.call("generateblock", mine_address, chosen)
        block = node.call("getblock", generated["hash"], 1)
        require(block["tx"][1:] == chosen, "mined selection differs")
        raw_block = node.call("getblock", generated["hash"], 0)
        entry = dict(step=step, time=600*step, hash=generated["hash"], block=block, raw_block=raw_block,
                     txids=chosen, weight=sum(by_id[x]["weight"] for x in chosen),
                     fee_sat=sum(by_id[x]["fee_sat"] for x in chosen),
                     exact_optimum=optimum, template=gbt, before=before,
                     after=node.call("getrawmempool", False, True))
        blocks.append(entry)
        for txid in chosen:
            events.append(dict(txid=txid, label=by_id[txid]["label"], time=600*step, cause="confirmed"))
            del remaining[txid]
        if not remaining:
            break
    events.extend(dict(txid=x, label=remaining[x]["label"], time=600*horizon, cause="censored") for x in remaining)
    events.sort(key=lambda x: x["label"])
    unrelated = [e for e in events if e["label"] >= 6]
    return dict(policy=policy, budget=budget, horizon_seconds=600*horizon,
                blocks=blocks, events=events, outcomes=outcomes(events, 600*horizon),
                unrelated_outcomes=outcomes(unrelated, 600*horizon))


def restore(node, trajectory, ids):
    node.call("invalidateblock", trajectory["blocks"][0]["hash"])
    actual = node.call("getrawmempool")
    require(set(actual) == set(ids), "branch reset did not restore identical transaction set")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=40)
    parser.add_argument("--capacities", nargs="+", type=int, default=[2, 4, 6, 12])
    parser.add_argument("--boundary-only", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Continue a preserved run after an orchestration failure")
    args = parser.parse_args()
    require(not args.output.exists() or args.resume, "use a new output directory to preserve prior evidence")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    binary = args.binary.resolve()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    preregistration = dict(source_commit=COMMIT, seeds=list(range(args.seeds)),
        capacities=args.capacities, topologies=TOPOLOGIES, n_transactions=12,
        fixed_unrelated_labels=list(range(6, 12)), horizon_blocks=8, block_interval_seconds=600,
        type="controlled_regtest_synthetic_demand", cost_function="not specified; no welfare inference",
        binary_sha256=sha256(binary.read_bytes()).hexdigest(),
        binary_version=subprocess.check_output([str(binary), "--version"], text=True),
        generator_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        independent_sha256=sha256((ROOT / "dependency_study/independent.py").read_bytes()).hexdigest(),
        funding="random wallet keys; seeded fees and topology; full signed bytes retained for exact replay",
        matching="label-level fees, weight, vsize, zero arrival time with identical insertion rank, block budget",
        status="registered_before_execution")
    if args.resume:
        original = json.loads((args.output / "design.json").read_text())
        require(original["seeds"] == preregistration["seeds"] and original["capacities"] == args.capacities,
                "resume design differs")
        require(original["binary_sha256"] == preregistration["binary_sha256"], "resume binary differs")
        dump(args.output / "resumption.json", dict(current=preregistration,
             reason="Carry mock clock across node restarts; preserve completed fixtures and original source snapshot"))
        (args.output / "source_snapshot" / "resumed_runner.py").write_bytes(Path(__file__).read_bytes())
    else:
        dump(args.output / "design.json", preregistration)
        (args.output / "source_snapshot").mkdir()
        for name in ("runner.py", "independent.py", "__init__.py"):
            (args.output / "source_snapshot" / name).write_bytes((ROOT / "dependency_study" / name).read_bytes())
    # 309 stripped bytes plus 68 witness/marker bytes = 1304 WU / 326 vB.
    expected_weight, mocktime = 1304, int(time.time()) + 10000
    if args.resume:
        for path in (args.output / "fixtures").glob("*.json"):
            data = json.loads(path.read_text())
            mocktime = max(mocktime, parse_block(data["cleanup_block"])["time"] + 6000)
    ledger = gzip.open(args.output / "rpc.jsonl.gz", "at" if args.resume else "wt", encoding="utf-8")
    node = Node(binary, args.output / "node", ledger, port)
    try:
        for capacity_index, capacity in enumerate(args.capacities):
            if not args.boundary_only and all((args.output / "fixtures" / f"cap{capacity}_seed{seed:03d}_{topology}.json").exists()
                                              for seed in range(args.seeds) for topology in TOPOLOGIES):
                continue
            budget = expected_weight * capacity
            node.start(budget, mocktime)
            node.call("setmocktime", mocktime)
            mine_address = node.call("getnewaddress", "", "bech32m", wallet=True)
            if node.call("getblockcount") == 0:
                hashes = node.call("generatetoaddress", 201, mine_address)
                dump(args.output / "setup_blocks.json", [node.call("getblock", h, 0) for h in hashes])
            if args.boundary_only:
                for n in (64, 65):
                    data = fixture(node, f"cluster_{n}", 0, n=n, boundary=True)
                    dump(args.output / f"cluster_{n}.json", data)
                    valid = [t["txid"] for t in data["transactions"] if t["acceptance"].get("allowed")]
                    node.call("generateblock", mine_address, valid)
                break
            for seed in range(args.seeds):
                matching = None
                for topology in TOPOLOGIES:
                    saved = args.output / "fixtures" / f"cap{capacity}_seed{seed:03d}_{topology}.json"
                    if args.resume and saved.exists():
                        continue
                    mocktime += 6000
                    node.call("setmocktime", mocktime)
                    data = fixture(node, topology, seed)
                    require(all(t["weight"] == expected_weight for t in data["transactions"]), "unexpected fixed weight")
                    signature = [(t["label"], t["fee_sat"], t["weight"], t["vsize"], t["arrival_index"]) for t in data["transactions"]]
                    if matching is None:
                        matching = signature
                    require(signature == matching, "label-level matching failed")
                    data["matching"] = dict(passed=True, label_fee_weight_vsize_arrival_equal=True, capacity=capacity)
                    trajectories = []
                    for policy in ("scalar", "ancestor", "cluster"):
                        # Distinct equal-size coinbase scripts avoid reusing a
                        # previously invalidated block hash across fork trials.
                        trial_address = node.call("getnewaddress", "", "bech32m", wallet=True)
                        trajectory = mine_trajectory(node, data, policy, budget, trial_address, mocktime)
                        trajectories.append(trajectory)
                        restore(node, trajectory, [t["txid"] for t in data["transactions"]])
                    # Final cleanup confirms a valid topological transaction list on a fresh branch.
                    node.call("setmocktime", mocktime + 5400)
                    cleanup = node.call("generateblock", mine_address, [t["txid"] for t in data["transactions"]])
                    data["cleanup_block"] = node.call("getblock", cleanup["hash"], 0)
                    data["trajectories"] = trajectories
                    dump(saved, data)
                print(json.dumps(dict(capacity=capacity, seed=seed, completed_topologies=len(TOPOLOGIES))), flush=True)
            node.close()
        dump(args.output / "execution.json", dict(status="completed", source_commit=COMMIT,
            fixtures=len(list((args.output / "fixtures").glob("*.json"))) if not args.boundary_only else 2,
            note="Controlled synthetic demand on local Regtest. No Mainnet observation or welfare estimate."))
    finally:
        node.close()
        ledger.close()


if __name__ == "__main__":
    main()
