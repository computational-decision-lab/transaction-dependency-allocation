# Who Pays for the Parent?

Research materials for *Who Pays for the Parent? Transaction Dependency, Blockspace Allocation, and Confirmation-Delay Redistribution in Bitcoin*.

The experiments measure how transaction dependencies change the identities selected for scarce block space and how results depend on the comparison policy. The paper reports 800 matched baseline fixtures and 2,400 selector trajectories, a v30.2-to-v31.0 initial-template comparison, and a 780-fixture extension varying transaction size and arrival timing. The extension is exploratory and shows that the equal-size, simultaneous-arrival contrast can change direction under heterogeneous transaction sizes.

## Contents

- `paper/main.pdf`: public manuscript with release links.
- `paper/main_anonymous.pdf`: link-free anonymous submission manuscript.
- `paper/`: editable LaTeX source, bibliography, generated tables, figure assets, and the editable TikZ source for the confirmation-order diagram.
- `dependency_study/`, `schemas/`: selectors, fixture generation, Core replay, independent reconstruction, analysis, and validation.
- `data/results/`: compact result summaries and verification artifacts.
- Compact results and verification files are included here. Full signed transaction fixtures, captured RPC data, and v30.2 templates are not currently included in the public release.

Code, paper source, and compact results: [GitHub repository](https://github.com/computational-decision-lab/transaction-dependency-allocation). The [Hugging Face bucket](https://huggingface.co/buckets/computational-decision-lab/transaction-dependency-allocation) currently hosts a data index only; the complete raw artifact archive is not part of the public release.

The experiments run on local Bitcoin Core Regtest nodes with synthetic fee assignments and a twelve-transaction cohort. They do not estimate Mainnet delays, miner behavior, or user welfare. Confirmation Gini is a dispersion statistic over transaction identifiers, not a measure of inequality among identified users.

No license is granted here for author-created code or manuscript text. Bitcoin Core itself is not redistributed in this repository; see `DATA_LICENSE.md` for data and upstream-material boundaries.
