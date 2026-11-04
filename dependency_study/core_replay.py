"""Replay archived signed transactions into a fresh Core node, without a wallet.

This verifier does not import the experiment runner or transaction generator.
It rebuilds the funding chain from raw blocks and compares candidate selections
against the source-build experiment using the independently signed release.
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

from .independent import hash256, parse_block, require


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
                directory = args.output / "node"
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
            require(set(got) == set(expected), f"Core candidate selection differs for {path.name}")
            require(rpc("submitblock", data["cleanup_block"]) is None, "funding continuation block rejected")
            report.append(dict(fixture=path.name, selected_set_equal=True, order_equal=got==expected))
            if len(report) % 100 == 0:
                print(json.dumps(dict(checked=len(report))), flush=True)
        summary = dict(status="passed", fixtures=len(report), raw_transaction_acceptances=12*len(report),
            all_first_block_selected_sets_match=True, exact_order_matches=sum(r["order_equal"] for r in report),
            binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(), cases=report,
            scope="Wallet-free raw-chain replay; independent release versus local source build; first candidate block per fixture")
        (args.output / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({k:v for k,v in summary.items() if k != "cases"}, indent=2))
    finally:
        stop()
        ledger.close()


if __name__ == "__main__":
    main()
