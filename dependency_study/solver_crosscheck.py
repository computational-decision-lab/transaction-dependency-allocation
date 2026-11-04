"""Cross-check enumerated feasible fee maxima with SciPy/HiGHS binary MILP."""
import argparse
import json
from pathlib import Path

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp

from .independent import reconstruct, require


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    checked = []
    for path in sorted((args.directory / "fixtures").glob("*.json")):
        data = json.loads(path.read_text())
        rows = reconstruct(data)
        n = len(rows)
        index = {r["txid"]: i for i, r in enumerate(rows)}
        matrix = [np.array([r["weight"] for r in rows], dtype=float)]
        upper = [data["matching"]["capacity"] * 1304]
        for i, row in enumerate(rows):
            for p in row["parents"]:
                constraint = np.zeros(n)
                constraint[i], constraint[index[p]] = 1, -1
                matrix.append(constraint)
                upper.append(0)
        fees = np.array([r["fee_sat"] for r in rows], dtype=float)
        result = milp(-fees, integrality=np.ones(n), bounds=Bounds(np.zeros(n), np.ones(n)),
                      constraints=LinearConstraint(np.array(matrix), -np.inf, np.array(upper)),
                      options={"mip_rel_gap": 0.0})
        require(result.status == 0 and result.success, "MILP optimum not established")
        decision = np.rint(result.x)
        require(np.max(np.abs(result.x - decision)) < 1e-7, "MILP solution not integral")
        require(np.all(np.array(matrix) @ decision <= np.array(upper)), "MILP solution not feasible")
        expected = data["trajectories"][0]["blocks"][0]["exact_optimum"]["fee_sat"]
        got = int(fees @ decision)
        require(got == expected and abs(result.mip_dual_bound + got) < 1e-6, "solver/enumeration disagreement")
        checked.append(dict(fixture=path.name, exact_fee_sat=got))
    report = dict(status="passed", fixtures=len(checked), scipy_version=scipy.__version__,
                  method="HiGHS binary MILP, zero relative gap; independent of subset enumeration", cases=checked)
    (args.directory / "analysis/solver_verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k:v for k,v in report.items() if k != "cases"}))


if __name__ == "__main__":
    main()
