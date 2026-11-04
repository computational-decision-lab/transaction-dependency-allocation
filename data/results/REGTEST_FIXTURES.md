# Executed Regtest fixture and matching contract — v2

This contract supersedes the six-transaction design blueprint. The actual allocation
matrix is defined by `regtest_20261103_n40/design.json` and structurally described by
`schemas/dependency_study_fixture_v2.json`. The historical blueprint remains an
archive; it is not a machine schema for these executed records.

## Controlled allocation matrix

Each fee seed (0–39) is used for five topologies and four capacities. Every fixture
contains 12 transactions. Labels 6–11 are unrelated singletons in every graph.

| Graph | Dependencies among labels 0–5 | Role |
| --- | --- | --- |
| independent | None | Feasible scalar and no-dependency control |
| cpfp | 0 → 1 | Low-fee parent / high-fee child |
| chain | 0 → 1 → 2 → 3 | Four-transaction dependency chain |
| star | 0 → each of 1, 2, 3, 4, 5 | Shared low-fee ancestor |
| mixed | 0 → 1; 0 → 2 → 3 | Branching dependency component |

Each transaction uses one input, six P2TR outputs, a 64-byte Schnorr witness,
version 2, locktime 0, and sequence 4294967293. Each is exactly 326 vB / 1,304 WU.
It is generated and signed anew for each topology. Inputs spend matured funding
UTXOs or the specified parent output. Raw input bytes are never rewired. Funding
values and identifiers change and are retained. All twelve arrive at one mock time
in label order; inserted-rank equality and all observed weights/fees are audited.

Seeded individual fee assignments are exact by label, not merely matched in
aggregate. The generator uses Python `random.Random(seed)`, draws distinct rates
from 2–120, sets label 0 to 1 sat/vB, label 1 to `160 + seed % 19`, and sets the fee
to `326 * rate + label` satoshis. This is synthetic demand, not a fitted Mainnet
fee distribution. Each exact rule and frozen source is retained.

Capacities are 2, 4, 6 and 12 transaction slots (2,608, 5,216, 7,824 and 15,648 WU).
Core reserves 2,000 WU and rejects at equality, so the configured maximum is
`2000 + budget + 1`. Follow-up is eight scheduled blocks, 600 seconds per block.
At least one block is generated; a path stops early only after all twelve confirm.
An unconfirmed transaction at 4,800 seconds remains administratively censored.

The two-slot budget, and some four-slot star chunks, deliberately stress the case
where a chunk can exceed the entire budget. This is not representative of the
normal Mainnet ratio between full-block capacity and the default cluster limit.
The twelve-slot control fits all demand immediately.

## Three selection policies

1. **Scalar:** repeatedly choose the highest individual fee-rate transaction among
   those whose remaining parents are already selected. Require capacity to fit;
   break ties by lexicographic transaction identifier.
2. **Ancestor package:** rank each candidate's unselected ancestor package by its
   aggregate fee / aggregate virtual size; choose the highest-rate fitting
   package, recompute, and never count selected ancestors twice. Ties use the
   candidate identifier. Package insertion is parent-before-child.
3. **Core cluster:** use the actual `getblocktemplate` list. Archive cluster/chunk
   RPC outputs and the fee-rate diagram as separate observations. Do not infer
   template selection from a cluster total or a guessed chunk sequence.

The first two are declared research policies. Neither is called historical Core,
BIP125, or the policy used by all miners. Core generates and validates every
selected block. Invalidating the trial branch restores the same signed input set
for the next selector. Distinct equal-size coinbase scripts prevent reuse of a
previously invalidated block hash.

## RBF is a separate experiment

Three contexts are recorded in `rbf_20261103_v2`: a single 10,000-sat conflict,
that conflict with a 30,000-sat child, and a conflict with a retained 326-sat
ancestor that also has a 20,000-sat sibling child. Candidate shape is fixed at
326 vB. Actual Core acceptance is compared with two research predicates.

Let D be direct conflicts, R the full removed conflict/descendant set, and A
retained unconfirmed ancestors. The scalar comparator requires explicit
replaceability, no new unconfirmed parents, at most 100 evictions, a new individual
fee rate no lower than each direct conflict, and a fee at least `sum_R(f) +
ceil(0.1 * candidate_vsize)`. The ancestor comparator adds the strict inequality
`(new_fee + sum_A(f)) / (new_vsize + sum_A(v)) > sum_R(f) / sum_R(v)`.

These are defined comparator policies, not implementations of BIP125 or a prior
Core version. The experiment pins Core's own incremental relay fee at 0.1 sat/vB.
Integer-satoshi bisection, adjacent reject/accept probes and a monotonicity grid are
retained, followed by actual submission, diagrams, eviction sets and a mined
successor. The analysis distinguishes the original identifier from payment intent.

## Separate count-boundary fixtures

`boundary_20261103` records a 64-transaction chain and an attempted 65-transaction
chain. The observed last rejection is a policy response, not a censored confirmation.
These fixtures are excluded from the matched 12-transaction treatment matrix.

## Estimands and inference

- Initial dependency premium uses each transaction's Core-returned chunk rate
  minus individual rate. It is a snapshot measure, not a guaranteed priority.
- The primary unrelated cohort is fixed at labels 6–11. Policy contrasts compare
  the same signed transactions; topology contrasts compare regenerated legal
  sets with exact fee/size/rank matching. These are different interventions.
- First-block fee capture uses the maximum feasible parent-closed fee value.
  Exhaustive subsets and a separate HiGHS binary MILP cross-check this optimum.
- Restricted nonconfirmation time integrates one minus confirmation cumulative
  incidence over the horizon. Gini uses the whole complete-follow-up cohort,
  assigning full horizon burden to any nonconfirmed original transaction.
- Confirmation, replacement, expiry, eviction, other/unknown removal and
  administrative censoring are distinct. Actual selection trajectories observe
  confirmation/censoring; the RBF cases observe replacement/confirmation. No
  expiry or eviction treatment effect is claimed.
- The resampling unit is the fee seed, paired across conditions: 40 seeds and
  10,000 bootstrap draws, seed 73103. No transaction-level pseudoreplication,
  multiple-testing-adjusted discovery or Mainnet population inference is claimed.
- If two policies have the same per-block confirmation counts for this simultaneous
  cohort, their aggregate burden multiset, mean and Gini must agree. The verifier
  checks this falsifiable invariant.

## Execution provenance and acceptance gates

The source pin is Core v31.0 commit `6574cb40869b96b9ffc79c19dc8f4e467d60f321`.
`design.json` was locally written before execution; its “registered” status does
not mean a public third-party preregistration. Source snapshots identify both the
initial runner and a restart fix carrying mock time across processes. Completed
fixtures were preserved. Later verification hardening adds witness-ID and raw
block timestamp checks and a separate comparator implementation without changing
the recorded generator outputs.

The controlled gate requires the full 800-fixture / 2,400-trajectory matrix, legal
matching, raw reconstruction, explicit policies, outcome accounting, boundary and
RBF checks, and official-release replay. Verification JSONs carry their actual
status. Mainnet remains a separate optional gate: no node history, ZMQ stream,
miner-adoption result or observed Mainnet confirmation-time distribution exists.
Neither a fee gain nor a waiting-time change identifies welfare without user
values and transaction-purpose evidence.
