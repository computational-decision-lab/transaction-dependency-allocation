"""Standalone transaction parsing, feasible selectors and outcome accounting.

Uses the Python standard library only. Does not import Bitcoin Core helpers.
Script/signature validity is checked by the external Core binary, not this parser.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
from math import ceil


def require(condition, message):
    if not condition:
        raise ValueError(message)


def hash256(data):
    return sha256(sha256(data).digest()).digest()


def parse_transaction(raw_hex, _allow_trailing=False):
    data = bytes.fromhex(raw_hex)
    stream = BytesIO(data)

    def read(n):
        value = stream.read(n)
        require(len(value) == n, "truncated transaction")
        return value

    def compact():
        first = read(1)[0]
        if first < 253:
            return first
        n = {253: 2, 254: 4, 255: 8}[first]
        value = int.from_bytes(read(n), "little")
        require(value >= {253: 253, 254: 65536, 255: 2**32}[first], "noncanonical CompactSize")
        return value

    version = read(4)
    witness = data[4:6] == b"\x00\x01"
    if witness:
        read(2)
    start = stream.tell()
    vin_count = compact()
    require(0 < vin_count <= 100000, "invalid input count")
    inputs = []
    for _ in range(vin_count):
        txid = read(32)[::-1].hex()
        vout = int.from_bytes(read(4), "little")
        script = read(compact()).hex()
        seq = int.from_bytes(read(4), "little")
        inputs.append(dict(txid=txid, vout=vout, script=script, sequence=seq))
    outputs = []
    vout_count = compact()
    require(0 < vout_count <= 100000, "invalid output count")
    for _ in range(vout_count):
        value = int.from_bytes(read(8), "little")
        require(value <= 21000000 * 100000000, "output value outside money range")
        outputs.append(dict(value_sat=value, script=read(compact()).hex()))
    end = stream.tell()
    if witness:
        for inp in inputs:
            count = compact()
            require(count <= 100000, "invalid witness count")
            inp["witness"] = [read(compact()).hex() for _ in range(count)]
    locktime = read(4)
    size = stream.tell()
    require(_allow_trailing or size == len(data), "trailing transaction bytes")
    data = data[:size]
    stripped = version + data[start:end] + locktime
    weight = 3 * len(stripped) + len(data)
    return dict(txid=hash256(stripped)[::-1].hex(), wtxid=hash256(data)[::-1].hex(),
                weight=weight, vsize=(weight + 3) // 4, inputs=inputs, outputs=outputs,
                size=size, version=int.from_bytes(version, "little"), locktime=int.from_bytes(locktime, "little"))


def parse_block(raw_hex):
    data = bytes.fromhex(raw_hex)
    require(len(data) >= 81, "truncated block")
    offset, first = 81, data[80]
    if first >= 253:
        width = {253: 2, 254: 4, 255: 8}[first]
        count = int.from_bytes(data[offset:offset+width], "little")
        offset += width
    else:
        count = first
    require(0 < count < 100000, "invalid block transaction count")
    txs = []
    for _ in range(count):
        tx = parse_transaction(data[offset:].hex(), _allow_trailing=True)
        offset += tx["size"]
        txs.append(tx)
    require(offset == len(data), "trailing block bytes")
    level = [bytes.fromhex(t["txid"])[::-1] for t in txs]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hash256(level[i] + level[i+1]) for i in range(0, len(level), 2)]
    require(level[0] == data[36:68], "block merkle root mismatch")
    return dict(hash=hash256(data[:80])[::-1].hex(), transactions=txs,
                time=int.from_bytes(data[68:72], "little"))


def reconstruct(fixture):
    """Reconstruct fee/weight/dependencies from bytes and raw funding prevouts."""
    funding = {}
    for raw in fixture["funding_transactions"].values():
        tx = parse_transaction(raw)
        for i, out in enumerate(tx["outputs"]):
            funding[(tx["txid"], i)] = out["value_sat"]
    labels = {t["txid"]: t["label"] for t in fixture["transactions"]}
    require(len(labels) == len(fixture["transactions"]), "duplicate transaction")
    rows, spent = [], set()
    for expected in fixture["transactions"]:
        tx = parse_transaction(expected["raw"])
        require(tx["txid"] == expected["txid"], "transaction hash mismatch")
        if "acceptance" in expected:
            require(tx["wtxid"] == expected["acceptance"]["wtxid"], "witness hash mismatch")
        require(tx["weight"] == expected["weight"], "transaction weight mismatch")
        require(tx["vsize"] == expected["vsize"], "transaction vsize mismatch")
        parents, value = [], 0
        for inp in tx["inputs"]:
            key = (inp["txid"], inp["vout"])
            require(key in funding, "missing or non-topological prevout")
            require(key not in spent, "double spent prevout")
            spent.add(key)
            value += funding[key]
            if inp["txid"] in labels:
                parents.append(inp["txid"])
        fee = value - sum(out["value_sat"] for out in tx["outputs"])
        require(fee >= 0 and fee == expected["fee_sat"], "fee mismatch")
        require(set(parents) == set(expected["parents"]), "dependency mismatch")
        for i, out in enumerate(tx["outputs"]):
            funding[(tx["txid"], i)] = out["value_sat"]
        rows.append(dict(txid=tx["txid"], label=expected["label"], fee_sat=fee,
                         weight=tx["weight"], vsize=tx["vsize"], parents=parents))
    return rows


def select(transactions, budget, policy):
    require(policy in ("scalar", "ancestor"), "unknown selector")
    pending = {t["txid"]: t for t in transactions}
    require(len(pending) == len(transactions), "duplicate transaction")
    chosen = []
    while pending:
        candidates = []
        for txid, tx in pending.items():
            if policy == "scalar":
                if any(p in pending for p in tx["parents"]):
                    continue
                package = [txid]
            else:
                package, visiting = [], set()

                def visit(x):
                    if x not in pending or x in package:
                        return
                    require(x not in visiting, "cyclic dependency")
                    visiting.add(x)
                    for p in sorted(pending[x]["parents"]):
                        visit(p)
                    visiting.remove(x)
                    package.append(x)

                visit(txid)
            weight = sum(pending[x]["weight"] for x in package)
            fee = sum(pending[x]["fee_sat"] for x in package)
            vsize = sum(pending[x]["vsize"] for x in package)
            if weight <= budget:
                candidates.append((-Fraction(fee, vsize), txid, package, weight))
        if not candidates:
            break
        _, _, package, weight = min(candidates)
        for txid in package:
            chosen.append(txid)
            del pending[txid]
        budget -= weight
    return chosen


def exact_max_fees(transactions, budget):
    """Enumerate all subsets for <= 20 transactions. Certifies exact optimum."""
    require(len(transactions) <= 20, "exact enumeration restricted to small fixtures")
    best, best_ids = 0, []
    by_id = {t["txid"]: i for i, t in enumerate(transactions)}
    parents = [sum(1 << by_id[p] for p in t["parents"] if p in by_id) for t in transactions]
    for mask in range(1 << len(transactions)):
        weight = fee = 0
        chosen = []
        for i, tx in enumerate(transactions):
            if mask & (1 << i):
                if parents[i] & mask != parents[i]:
                    break
                weight += tx["weight"]
                if weight > budget:
                    break
                fee += tx["fee_sat"]
                chosen.append(tx["txid"])
        else:
            if fee > best:
                best, best_ids = fee, chosen
    return dict(fee_sat=best, txids=best_ids, method="exhaustive_subsets", proven_optimal=True)


def validate_selection(transactions, chosen, budget):
    by_id = {t["txid"]: t for t in transactions}
    seen = set()
    for txid in chosen:
        require(txid in by_id and txid not in seen, "unknown or duplicate selected transaction")
        require(all(p not in by_id or p in seen for p in by_id[txid]["parents"]), "child selected before parent")
        seen.add(txid)
    require(sum(by_id[x]["weight"] for x in chosen) <= budget, "overweight selection")


def gini(values):
    values = sorted(values)
    require(values and all(v >= 0 for v in values), "invalid Gini cohort")
    if not sum(values):
        return 0.0
    n = len(values)
    return sum((2*i - n - 1)*v for i, v in enumerate(values, 1)) / (n * sum(values))


CAUSES = ("confirmed", "replaced", "expired", "evicted", "removed_other", "removed_unknown")


def cumulative_incidence(events, horizon):
    """Aalen--Johansen from first terminal outcome, common time-zero cohort.

    Administrative censoring is allowed; restricted burden/Gini require full
    horizon follow-up (enforced by outcomes()). Simultaneous events precede censoring.
    """
    require(events and horizon > 0, "empty cohort or invalid horizon")
    ids = [x["txid"] for x in events]
    require(len(set(ids)) == len(ids), "duplicate terminal event")
    for e in events:
        require(e["cause"] in (*CAUSES, "censored") and 0 <= e["time"] <= horizon, "invalid event")
    risk, survival = len(events), 1.0
    cif = dict.fromkeys(CAUSES, 0.0)
    curve = [dict(time=0, at_risk=risk, survival=survival, **cif)]
    for t in sorted({e["time"] for e in events}):
        at_time = [e for e in events if e["time"] == t]
        counts = Counter(e["cause"] for e in at_time)
        if risk:
            before = survival
            for cause in CAUSES:
                cif[cause] += before * counts[cause] / risk
            survival *= 1 - sum(counts[c] for c in CAUSES) / risk
        curve.append(dict(time=t, at_risk=risk, survival=survival, **cif))
        risk -= len(at_time)
    restricted, last_t, last_f = 0.0, 0.0, 0.0
    for row in curve[1:]:
        restricted += (row["time"] - last_t) * (1 - last_f)
        last_t, last_f = row["time"], row["confirmed"]
    restricted += (horizon - last_t) * (1 - last_f)
    return dict(curve=curve, final=cif, restricted_nonconfirmation=restricted)


def outcomes(events, horizon):
    require(all(e["cause"] != "censored" or e["time"] == horizon for e in events),
            "early censoring invalidates complete-cohort burden/Gini")
    aj = cumulative_incidence(events, horizon)
    burdens = [e["time"] if e["cause"] == "confirmed" else horizon for e in events]
    mean = sum(burdens) / len(burdens)
    require(abs(mean - aj["restricted_nonconfirmation"]) < 1e-7, "AJ/complete-cohort burden mismatch")
    return dict(confirmation_incidence=aj["final"]["confirmed"], mean_burden=mean,
                gini=gini(burdens), competing_counts=dict(Counter(e["cause"] for e in events)),
                aj=aj, burdens=burdens)


def rbf_accept(policy, candidate, conflicts, removed, ancestors, incremental_sat_per_kvb=100, cap=100):
    require(policy in ("scalar", "ancestor"), "unknown RBF comparator")
    if not conflicts or not candidate["replaceable"] or candidate["new_unconfirmed_parents"] or len(removed) > cap:
        return False
    rate = Fraction(candidate["fee_sat"], candidate["vsize"])
    if rate < max(Fraction(x["fee_sat"], x["vsize"]) for x in conflicts):
        return False
    # CFeeRate::GetFee uses ceiling for a positive rate.
    increment = ceil(Fraction(incremental_sat_per_kvb * candidate["vsize"], 1000))
    if candidate["fee_sat"] < sum(x["fee_sat"] for x in removed) + increment:
        return False
    if policy == "ancestor":
        rate = Fraction(candidate["fee_sat"] + sum(x["fee_sat"] for x in ancestors),
                        candidate["vsize"] + sum(x["vsize"] for x in ancestors))
        if rate <= Fraction(sum(x["fee_sat"] for x in removed), sum(x["vsize"] for x in removed)):
            return False
    return True
