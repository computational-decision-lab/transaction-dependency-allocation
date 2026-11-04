# Reproducibility

The baseline design crosses 40 fee seeds, five dependency topologies, and four block-space budgets, producing 800 signed fixture sets and three selector trajectories per set. An independent optimizer cross-check and official Bitcoin Core v31.0 replay are archived with the data. The version comparison uses official Core v30.2 and v31.0 binaries on the same 800 fixtures. An additional 780 fixtures vary size and arrival timing; five precision cells use 100 seeds, while the size/arrival diagnostics use 10 seeds and are exploratory.

Large artifacts include valid signed Regtest transaction fixtures, setup blocks, raw RPC captures, extension fixtures, and v30.2 candidate templates. They are not included in this compact public release. This repository contains code, the paper, and compact analysis outputs; the Hugging Face bucket linked from the README mirrors these same published files.

No Bitcoin Core binary, node data directory, wallet, private key, or debug log is included. The recorded checksums establish integrity relative to the supplied digests; they do not establish artifact provenance or scientific validity by themselves. The independent reconstruction checks archived inputs against declared outputs, not full Mainnet protocol behavior.

The experiments use synthetic fee assignments and constrained Regtest block budgets. A local template is not a mined Mainnet block, and the paper does not infer welfare from delay or fee capture.
