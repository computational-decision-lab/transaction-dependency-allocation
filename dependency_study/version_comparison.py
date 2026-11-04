"""Compare actual Core v30.2 initial block templates with archived Core v31.0.

Uses identical archived signed bytes, reconstructed funding chain and inclusive
transaction-weight budget. This is a version comparison, not isolated attribution
to the cluster-mempool patch. No original experiment runner is imported.
"""
import argparse
import base64
import gzip
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import time

from .independent import hash256, parse_block, require, reconstruct, validate_selection
from .analyze import interval
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("directory", type=Path)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    require(not args.output.exists(), "replay requires a fresh directory")
    args.output.mkdir(parents=True)
    setup = json.loads((args.directory / "setup_blocks.json").read_text())
    by_parent = {}
    for path in (args.directory / "fixtures").glob("*.json"):
        d = json.loads(path.read_text())
        raw = bytes.fromhex(d["cleanup_block"])
        previous = raw[4:36][::-1].hex()
        require(previous not in by_parent, "ambiguous funding chain")
        by_parent[previous] = (path, d)
    order = []
    tip = hash256(bytes.fromhex(setup[-1])[:80])[::-1].hex()
    while tip in by_parent:
        path, data = by_parent[tip]
        order.append((path, data))
        tip = hash256(bytes.fromhex(data["cleanup_block"])[:80])[::-1].hex()
    require(len(order) == len(by_parent), "disconnected archived funding chain")
    mocktime = int.from_bytes(bytes.fromhex(setup[-1])[68:72], "little") + 10000
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ledger = gzip.open(args.output / "replay_rpc.jsonl.gz", "wt")
    process, conn, log = None, None, None
    sequence = 0

    def rpc(method, *params):
        nonlocal sequence
        sequence += 1
        request = dict(jsonrpc="2.0", id=sequence, method=method, params=list(params))
        conn.request("POST", "/", json.dumps(request), {"Authorization": auth, "Content-Type": "application/json"})
        response = json.loads(conn.getresponse().read())
        ledger.write(json.dumps(dict(request=request, response=response), separators=(",", ":")) + "\n")
        require(not response.get("error"), str(response.get("error")))
        return response["result"]

    def stop():
        nonlocal process
        if process is not None and process.poll() is None:
            rpc("stop")
            process.wait(timeout=30)
        if conn:
            conn.close()
        if log:
            log.close()
        process = None

    capacity, report = None, []
    try:
        for path, data in order:
            cap = data["matching"]["capacity"]
            if cap != capacity:
                stop()
                directory = Path("/dev/shm") / ("pow_version_" + args.output.name)
                directory.mkdir(exist_ok=True)
                log = (args.output / "process.log").open("a")
                process = subprocess.Popen([str(args.binary.resolve()), "-regtest", f"-datadir={directory.resolve()}",
                    "-listen=0", "-connect=0", "-discover=0", "-networkactive=0", f"-rpcport={port}",
                    "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-persistmempool=0", "-disablewallet",
                    "-blockreservedweight=2000", f"-blockmaxweight={2001 + cap*1304}", "-blockmintxfee=0",
                    f"-mocktime={mocktime}", "-dbcache=64", "-par=1"], stdout=log, stderr=subprocess.STDOUT)
                for _ in range(200):
                    require(process.poll() is None, "replay Core failed startup")
                    cookie = directory / "regtest" / ".cookie"
                    if cookie.exists():
                        auth = "Basic " + base64.b64encode(cookie.read_bytes().strip()).decode()
                        try:
                            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
                            rpc("getblockcount")
                            break
                        except (OSError, ValueError, http.client.HTTPException):
                            conn.close()
                    time.sleep(.05)
                else:
                    raise RuntimeError("replay startup timeout")
                if capacity is None:
                    for raw in setup:
                        require(rpc("submitblock", raw) is None, "setup block rejected")
                capacity = cap
            mocktime = int.from_bytes(bytes.fromhex(data["cleanup_block"])[68:72], "little") + 1
            rpc("setmocktime", mocktime)
            require(not rpc("getrawmempool"), "replay fixture starts with leftover transactions")
            for tx in data["transactions"]:
                require(rpc("sendrawtransaction", tx["raw"], 0) == tx["txid"], "raw transaction rejected")
            template = rpc("getblocktemplate", {"rules": ["segwit"]})
            got = [x["txid"] for x in template["transactions"]]
            expected = next(t for t in data["trajectories"] if t["policy"] == "cluster")["blocks"][0]["txids"]
            rows=reconstruct(data)
            validate_selection(rows,got,cap*1304)
            byid={r['txid']:r for r in rows}
            first=next(t for t in data['trajectories'] if t['policy']=='cluster')['blocks'][0]
            fees=sum(byid[x]['fee_sat'] for x in got)
            weight=sum(byid[x]['weight'] for x in got)
            (args.output / 'templates').mkdir(exist_ok=True)
            (args.output/'templates'/path.name).write_text(json.dumps(dict(template=template,
                selected_labels=[byid[x]['label'] for x in got],fee_sat=fees,weight=weight),indent=2)+'\n')
            require(rpc("submitblock", data["cleanup_block"]) is None, "funding continuation block rejected")
            report.append(dict(fixture=path.name,seed=data['seed'],topology=data['topology'],capacity=cap,
                selected_set_equal=set(got)==set(expected),order_equal=got==expected,
                v30_fee=fees,v31_fee=first['fee_sat'],v30_weight=weight,v31_weight=first['weight'],
                optimum_fee=first['exact_optimum']['fee_sat']))
            if len(report) % 100 == 0:
                print(json.dumps(dict(checked=len(report))), flush=True)
        groups=defaultdict(list)
        for r in report:groups[(r['capacity'],r['topology'])].append(r)
        cells=[]
        for (cap,topology),values in sorted(groups.items()):
            cells.append(dict(capacity=cap,topology=topology,n=len(values),
                identical_sets=sum(r['selected_set_equal'] for r in values),
                fee_difference_v31_minus_v30=interval([r['v31_fee']-r['v30_fee'] for r in values]),
                gap_v30=interval([1-r['v30_fee']/r['optimum_fee'] for r in values]),
                gap_v31=interval([1-r['v31_fee']/r['optimum_fee'] for r in values])))
        summary = dict(status="passed", fixtures=len(report), raw_transaction_acceptances=12*len(report),
            matching_selected_sets=sum(r['selected_set_equal'] for r in report),
            exact_order_matches=sum(r["order_equal"] for r in report),cells=cells,
            binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(), cases=report,
            scope="Official Core v30.2 versus archived source-build Core v31.0; same signed transactions and inclusive weight budgets; first candidate block only; differences not exclusively attributed to cluster mempool")
        (args.output / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({k:v for k,v in summary.items() if k not in ("cases","cells")}, indent=2))
    finally:
        stop()
        ledger.close()


if __name__ == "__main__":
    main()
