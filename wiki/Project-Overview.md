# Project Overview

This page states what Recluse is, why it is built from two models rather than
one, and which constraints the build is not allowed to trade away. It is the
page to read first — before [Architecture](Architecture) or
[Getting-Started](Getting-Started) — and it is aimed at anyone evaluating
the project: a reviewer deciding whether the claims hold up, or an engineer
about to change something and needing to know which properties are
load-bearing. The repository's `README.md` carries a short version of
everything below — the claim, the phase status, the constraints, the
arithmetic, the limitations — for someone who lands on the repository first.
This page is the long form; where they disagree, the code named on this page
settles it.

**Status:** Phase 0 of 9 is complete. The scaffold runs end to end; no model
has been trained. Every number on this page that describes a *design input*
(flow volume, analyst capacity, shift length) is real and committed to
`.env.example`, and the alert budget and target false-positive rate derived
from them are computed in `backend/app/config.py`. Every number that would
describe *measured detection performance* is marked as not measured yet,
because it is.

---

## What Recluse is

Recluse is a network intrusion detection system that scores flow records with
machine learning and presents the results to a security analyst through a
triage queue. It runs two models in sequence: a supervised classifier that
names attack families it was trained on, and a benign-only autoencoder that
flags traffic which does not look like normal regardless of whether anyone has
ever labelled it. It alerts, ranks and explains; it does not block.

The one technical claim the project must defend:

> **It detects attack traffic it was never trained on.**

Everything else in the repository exists to make that claim *measurable*
rather than asserted. The measurement is the leave-one-attack-out evaluation:
entire attack families are removed from supervised training, the model is
retrained without them, and recall on those families is recorded per stage.
See [Roadmap](Roadmap) and [ML-Models](ML-Models).

**Not measured yet — Phase 4 produces the leave-one-attack-out table.** It
will be committed as `reports/loao.md`. Until then `reports/` contains only a
`.gitkeep`.

---

## Why two models

One model cannot answer both questions. A supervised classifier can only name
what it has seen; an anomaly detector can only say "this is unusual" and
cannot tell you what it is. Collapsing them into one model throws away
whichever half you collapsed.

|                         | Stage 1 — supervised classifier | Stage 2 — anomaly detector |
| ----------------------- | ------------------------------- | -------------------------- |
| **Library**             | scikit-learn `RandomForestClassifier` (baseline) then LightGBM (upgrade) | PyTorch autoencoder |
| **Training data**       | Labelled flows: benign **and** known attack families | Benign traffic **only** — no attack labels enter the set at all |
| **Question it answers** | "Which named attack is this?"   | "How unlike normal traffic is this?" |
| **Artifact filename**   | `supervised_model.pkl`          | `autoencoder.pt` (state dict) |
| **Score**               | Max attack-class probability, compared against `tau_sup` | Per-row mean squared reconstruction error, compared against `tau_anom` |
| **Explanation**         | TreeSHAP, top-5 contributing features | Per-feature reconstruction error, top-5 |
| **What it cannot do**   | Name an attack family it never saw in training. It has no vocabulary for one, so it will either force it into a known class or return low confidence across the board. | Name anything. It produces a distance from normal, not a label. It also cannot distinguish *unusual* from *malicious* — a new backup job scores high. |

The artifact filenames are constants in `backend/app/inference.py`
(`SUPERVISED_FILE`, `AUTOENCODER_FILE`, alongside `PREPROCESSING_FILE` and
`MODEL_CARD_FILE`). The loader is written; the files do not exist yet.

Both models are trained in this repository. Neither is a pretrained download
and neither is a call to a hosted model — `supervised_model.pkl` and
`autoencoder.pt` are written by `backend/training/train_supervised.py` and
`backend/training/train_autoencoder.py` and by nothing else.
`ModelBundle._load_models` reads only those locally produced files, and its
docstring states the trust boundary: everything under `artifacts_dir` is
produced locally by `backend/training/` and is gitignored, the API has no
artifact-upload path, and the Stage 2 weights are loaded with
`weights_only=True` so the file is data rather than code. There is no way for
a third-party weight file to enter the serving process.

**Status today:** `ModelBundle.is_loaded` is false, `stage1_ready` and
`stage2_ready` are false, and `GET /api/v1/health` returns
`model_version: "unloaded"`. That is the honest state of a scaffold, not a
failure — `backend/tests/test_health.py` asserts it explicitly. On the route
surface, `GET /api/v1/health` is the only operation with behaviour. The other
fifteen registered operations answer `501` with a machine-readable body naming
the phase that fills them in, and
`test_unimplemented_routes_answer_501_with_a_phase` in
`backend/tests/test_api_surface.py` asserts that for every one of them.

The fusion between the two stages — confident attack goes out as `KNOWN`,
low-confidence-but-anomalous goes out as `UNCLASSIFIED_ANOMALY`, everything
else is dropped — is the point of the architecture:

```
                 flow record
                      |
                      v
        +-----------------------------+
        |  Stage 1 supervised         |
        |  attack_conf = max p(attack)|
        +-------------+---------------+
                      |
      attack_conf >= tau_sup  -->  Alert(kind=KNOWN, family=argmax)
                      |
                 low confidence
                      |
                      v
        +-----------------------------+
        |  Stage 2 autoencoder        |
        |  anom = mean sq recon error |
        +-------------+---------------+
                      |
      anom >= tau_anom  -->  Alert(kind=UNCLASSIFIED_ANOMALY, family=None)
                      |
                    else  -->  no alert
```

`UNCLASSIFIED_ANOMALY` is the project's thesis rendered as an enum value. It is
already in the database vocabulary (`ALERT_KINDS` in `backend/app/models.py`,
mirrored as the `AlertKind` literal in `backend/app/schemas.py` so the wire
contract and the table cannot drift), and the `family_matches_kind` check
constraint makes the rule symmetrical: an `UNCLASSIFIED_ANOMALY` may not carry
a family label, and a `KNOWN` alert may not be missing one. Both halves are
asserted in `backend/tests/test_schema_portability.py`. See
[Architecture](Architecture) and [Database-Schema](Database-Schema).

---

## The non-negotiables

These are properties of the build, not preferences. Violating any one of them
means the build has failed its brief regardless of what the numbers say. Each
is stated as a rule, then why it exists. Where a mechanism already enforces the
rule in code, the mechanism is named; where the rule applies to work that has
not been done yet, that is said plainly.

### No auto-block, anywhere

**Rule.** No endpoint, button, or configuration flag drops traffic.
Containment actions, if they exist at all, are manual, confirmed by a human,
and audited.

**Why.** An intrusion detector that acts on its own predictions inherits the
blast radius of its own false positives. The arithmetic is in the next section
and it is not close: at realistic volume, a false-positive rate good enough to
be proud of still takes production down if it is wired to a firewall.
Detection quality does not fix this, because the failure is not "the model is
bad" — it is "the model is right 99.9 percent of the time and the other 0.1
percent is a thousand severed connections a day."

**Enforced today.** `IDS_ALLOW_AUTO_BLOCK` exists in `.env.example` set to
`false`, and exists in `Settings` only so the constraint is greppable rather
than merely absent. Setting it to `true` aborts the process on import of
`backend/app/config.py`, before the FastAPI lifespan runs — the module builds
its `settings` singleton at import time, and the validator `_reject_auto_block`
rejects the value as a `ValidationError` carrying the message "never drops
traffic". `test_auto_block_cannot_be_enabled` in
`backend/tests/test_config.py` asserts the refusal. Separately,
`test_no_route_mentions_blocking` in
`backend/tests/test_api_surface.py` walks the served OpenAPI schema and
asserts no path contains `block`, `drop` or `quarantine`.

### Temporal splits only

**Rule.** `train_test_split(shuffle=True)` is never used on this data. Splits
follow the dataset's day structure: Monday to Wednesday train, Thursday
validation, Friday test.

**Why.** CICIDS2017 contains a very large number of exact-duplicate flow rows.
Shuffling puts near-identical rows on both sides of the split, so the test set
is partly a copy of the training set and the model is scored on rows it has
memorised. The result is a fabricated 99.9 percent that survives review because
it looks like success rather than like a bug.

**Status.** `backend/training/split.py` documents the day structure and the
rule in its module docstring; its `main()` raises `NotImplementedError` naming
Phase 1. There is no `train_test_split` *call* anywhere in first-party code:
`grep -rn "train_test_split" backend/app backend/training` returns exactly one
line, the sentence in `split.py`'s docstring that forbids it. (Grepping
`backend/` as a whole also matches `backend/.venv/`, where scikit-learn and
shap call it for their own purposes; that is library code, not this
project's.) The absence is currently true because no splitting code exists
yet, not because a guard prevents it. The guard is the rule and the review,
until Phase 1 writes the splitter.

### The anomaly model never sees attack labels

**Rule.** Stage 2 trains on benign rows exclusively — Monday in full, plus the
benign rows of Tuesday and Wednesday. The attack-free property is asserted in
code, not intended.

**Why.** This is the difference between a real novel-attack claim and a
relabelled supervised model. If any attack rows reach the autoencoder's
training set, then "it caught something it was never trained on" becomes
unfalsifiable — you can no longer say what it was or was not trained on. An
assertion in the training script is cheap; discovering the contamination after
publishing the recall numbers is not.

**Status.** `backend/training/train_autoencoder.py` states the requirement in
its module docstring and raises `NotImplementedError` naming Phase 3. The
assertion lands with the implementation.

### Leave-one-attack-out is mandatory

**Rule.** For each attack family, remove every row of that family from
supervised training, retrain Stage 1, leave Stage 2 untouched (it never saw
attacks anyway), run the full fusion pipeline over a test set containing the
family, and record what fraction was caught and by which stage — including a
**Missed** column.

**Why.** It is the only evaluation in the project that measures the headline
claim instead of assuming it. A high overall recall number tells you the model
is good at attacks it was trained on, which nobody doubted. LOAO tells you what
happens when the family is genuinely absent from training, which is the
situation the system is sold for. The misses are part of the result: a table
with a real Missed column reads as engineering; a table of 99s reads as a
leak.

**Status.** `backend/training/loao.py` defines the procedure and the output
table shape and raises `NotImplementedError` naming Phase 4. **Not measured
yet — Phase 4 produces this.**

### Report PR-AUC, per-class recall, FPR and alerts/analyst/hour, with accuracy never as a headline

**Rule.** The reported metrics are PR-AUC (headline), per-class precision,
recall and F1 with support, false-positive rate at the chosen threshold, and
projected alerts per analyst per hour. Accuracy may appear inside a table. It
may never be a headline, and there is no accuracy tile in the UI.

**Why.** On traffic that is roughly 99 percent benign, a model that answers
"benign" unconditionally scores 99 percent accuracy and detects nothing.
ROC-AUC has a milder version of the same problem: a large true-negative pool
flatters the curve, so an imbalanced classifier looks better than it is. PR-AUC
conditions on the positives, which is the population anyone cares about here.
Alerts per analyst hour is the metric that decides whether the system is usable
at all, because an analyst who cannot work the queue is the real failure mode.

**Stated in code, asserted on the frontend.** `backend/training/evaluate.py`
fixes the metric list in its module docstring and `backend/app/routes/metrics.py`
repeats the rule at the route layer; both are statements of the rule rather
than guards on it. The one mechanical check is on the frontend:
`frontend/src/pages/SystemHealth.test.tsx` contains the case
`renders no accuracy figure anywhere`, which asserts the rendered DOM text
matches neither `/accura/i` nor a bare `NN.N%` pattern. See
[Testing](Testing).

### Train and serve share one feature module

**Rule.** `backend/training/features.py` is imported by both the training
scripts and the serving path. The API never reimplements a transform.

**Why.** Train/serve skew is the silent killer of this kind of project. If the
API assembles columns in a different order than training did, the model
receives a valid-looking matrix of the right shape and returns numbers that are
meaningless — no exception, no traceback, no log line, just quietly wrong
scores. Sharing the module removes the opportunity, and hashing the column
order makes any remaining divergence fatal instead of silent.

**Enforced today.** `backend/app/inference.py` imports `compute_schema_hash`
from `training.features` — the serving path takes a hard dependency on the
training module rather than the other way round. `compute_schema_hash` is
order-sensitive by construction (it joins the feature order with newlines,
takes a SHA-256, and returns `sha256:<hex>`), and
`ModelBundle._verify_schema_hash` recomputes it at startup and raises
`SchemaHashMismatch` on disagreement, or on a bundle carrying no hash at all.
There is a second check on the same invariant:
`ModelBundle._load_model_card` raises `SchemaHashMismatch` when
`model_card.json` carries a `schema_hash` that disagrees with
`preprocessing.pkl`'s, which is what a directory holding artifacts from two
different training runs looks like. Either exception is raised inside the
FastAPI `lifespan` and is deliberately fatal.
`test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` in
`backend/tests/test_api_surface.py` writes a deliberately inconsistent bundle
and asserts the refusal. See [Data-Pipeline](Data-Pipeline).

### No training in a request handler

**Rule.** Training is offline batch, run from `backend/training/`. The API
loads artifacts once, at startup, and never calls `.fit()`.

**Why.** A `.fit()` inside an endpoint is not a serving architecture. It makes
request latency unbounded, makes the model a function of request order, gives
you no way to say which model version produced which alert, and makes the audit
trail that security work requires impossible to reconstruct after the fact.

**Enforced today.** Artifact loading happens in the `lifespan` context manager
in `backend/app/main.py` and the result is parked on `app.state.bundle`; the
health endpoint reads it back from `request.app.state`. There is no `.fit(`
call anywhere under `backend/app/` — the only occurrence of the string is the
docstring in `inference.py` stating that there is not one.

### Every alert is explainable

**Rule.** Every alert carries a reason: TreeSHAP top-5 contributors for
Stage 1, top-5 per-feature reconstruction errors for Stage 2, plus a templated
English narrative.

**Why.** An alert with a score and no reason is an alert an analyst ignores.
Triage is a time-constrained activity; a number between 0 and 1 gives an
analyst nothing to act on and nothing to disagree with, so it gets closed
unread. The explanation is also what makes analyst verdicts worth collecting —
a verdict on an unexplained alert is a coin flip, and feeding coin flips back
into training is worse than collecting nothing.

**Status.** The columns exist: `explanation` (JSON), `narrative` (Text) and
`recommended_actions` (JSON) on the `alerts` table in `backend/app/models.py`.
They are nullable at the database level; the guarantee is a property of the
alert pipeline, which lands in Phase 5. `backend/app/explain.py` defines
`explain_supervised`, `explain_anomaly` and `narrate`, all currently raising
`NotImplementedError` naming Phase 5. The other two columns have owners of
their own, both Phase 5 stubs: `backend/app/mitre.py` defines `technique_for`,
which fills `mitre_technique`, and `backend/app/remediation.py` defines
`playbook_for`, which fills `recommended_actions` from a static, reviewed
lookup rather than a generated one. See
[Code-Backend-Pipeline](Code-Backend-Pipeline) and
[Frontend-Screens](Frontend-Screens).

---

## The alert-not-block argument

The case against auto-blocking is arithmetic, not philosophy. Work it through
with the numbers actually committed to this repository's `.env.example`:

```
EXPECTED_DAILY_FLOW_VOLUME   V = 1,000,000 flows/day
ANALYST_CAPACITY_PER_HOUR    C = 40 alerts/hour
ANALYST_SHIFT_HOURS              8 hours

max_alerts_per_day  = C * 8            =        320 alerts/day
target_FPR          = 320 / 1,000,000  = 3.2e-4  (0.032%)
```

Now the argument. At **one million flows a day**, a false-positive rate of
**0.1 percent** — a tenth of one percent, a rate most people would call
excellent — produces:

```
1,000,000 flows/day  x  0.001  =  1,000 false alerts per day
```

**A thousand false alerts a day.** If those alerts are wired to a firewall,
that is a thousand legitimate connections severed per day, every day, with no
human in the path. One of them is the payroll run; one of them is the
monitoring agent; one of them is a customer. The system does not have to be bad
to cause an outage — it has to be *slightly* wrong at scale, which is the
normal condition of every statistical detector ever deployed.

The same arithmetic run backwards is where `tau_sup` comes from. Instead of
defaulting the decision threshold to 0.5 and reporting whatever false-positive
rate falls out, the threshold is chosen as the smallest value whose measured
FPR stays inside the analyst budget:

```
tau_sup = smallest threshold where FPR(tau) <= target_FPR
```

At the committed defaults that budget is 320 alerts per day against a target
FPR of 3.2e-4 — an order of magnitude tighter than the 0.1 percent above, and
it is tight precisely because a human has to read every one of those alerts.
All three inputs are configuration (`IDS_EXPECTED_DAILY_FLOW_VOLUME`,
`IDS_ANALYST_CAPACITY_PER_HOUR`, `IDS_ANALYST_SHIFT_HOURS`); the derived values
are the computed fields `Settings.max_alerts_per_day` and `Settings.target_fpr`
in `backend/app/config.py`, asserted by `test_false_positive_budget_arithmetic`
in `backend/tests/test_config.py`, and logged at startup by
`backend/app/main.py`. See [Configuration](Configuration).

So the system alerts, ranks and explains. The threshold is a dial the SOC lead
controls, with the projected alert volume shown next to it, and the decision to
cut traffic stays with a person who can be asked why.

**Not measured yet — Phase 2 produces the actual `tau_sup` and the measured FPR
at that threshold.** The 0.1 percent figure above is an illustrative rate used
to make the argument, not a claim about this model.

---

## Limitations stated up front

Stating these makes the work more credible, not less. Each is followed by what
it means for someone evaluating the project.

| # | Limitation | Consequence for a reader evaluating this |
| - | ---------- | ---------------------------------------- |
| 1 | **Flow-level features cannot see encrypted payload content.** | Every detection here is inferred from timing, volume and connection shape. Anything whose signal lives in the payload — a specific exploit string, a malicious file — is out of scope by construction, and no amount of model quality changes that. Read the recall numbers as recall on behaviourally distinguishable attacks, not recall on attacks. |
| 2 | **CICIDS2017 is synthesised lab traffic; a real enterprise baseline is messier and drifts faster.** | The numbers Phases 2 to 4 will produce will describe performance on a clean, scripted 2017 network. They are an upper bound on what the same model does on live traffic, not a prediction of it. Phase 9 exists to test that gap rather than to argue it away. |
| 3 | **The autoencoder flags *unusual*, which is not synonymous with *malicious* — a new backup job will fire alerts.** | Stage 2's recall number and its false-positive number have to be read together. High novel-attack recall bought with an alert stream full of benign novelty is not a win, and it is exactly why alerts/analyst/hour is a reported metric and accuracy is not. |
| 4 | **An adaptive adversary can shape traffic to stay under the threshold.** | The evaluation measures detection of attacks generated without knowledge of the detector. An attacker who knows `tau_anom` and can pace their traffic beneath it is a different threat model, and this project does not claim to address it. |
| 5 | **Leave-one-attack-out measures generalisation to held-out *known* attacks, which is a proxy for genuinely novel ones, not proof.** | This is the honest boundary of the headline claim. A held-out family still comes from the same 2017 capture, the same lab topology and the same generation tooling as the training data. LOAO is the strongest available evidence, and it remains evidence rather than proof. |

A sixth, specific to deployment rather than to the models, belongs with them: a
model trained on 2017 lab traffic and pointed at today's mostly TLS-encrypted
traffic will over-fire until it is recalibrated against a local baseline. That
is domain shift, it is expected, and [Roadmap](Roadmap) handles it in
Phase 9 with a shadow-mode burn-in rather than treating it as a defect.

---

## Legal and ethical scope

Packet capture and attack testing are scoped to networks, devices and hosts
that are **owned by the operator or explicitly authorised for testing**. This
is not a stylistic preference:

- Capturing traffic on a network you do not control, or lack authorisation to
  monitor, is illegal in most jurisdictions regardless of intent.
- Pointing an attack tool — `nmap`, `hydra`, `hping3` or anything else — at a
  host you do not own is unauthorised access, again regardless of intent.

In practice the rule costs nothing. An isolated lab of two to four VMs on one
virtual switch, your own router's mirror or SPAN port, or your own machine's
interface all satisfy it and all produce usable traffic. Self-generated attacks
inside your own lab have the additional advantage of giving you exact ground
truth, which is what makes Phase 9 a second, independent version of the
leave-one-attack-out test.

`backend/app/live_capture.py` states this as a hard precondition in its module
docstring, and `POST /api/v1/ingest/start` — the endpoint that would begin a
capture — currently answers `501` naming Phase 9. See
[Roadmap](Roadmap) and [API-Reference](API-Reference).

---

## Where to go next

| If you want to | Read |
| -------------- | ---- |
| See how the two stages and the alert pipeline fit together | [Architecture](Architecture) |
| Run the stack locally | [Getting-Started](Getting-Started) |
| Know what is built and what is not, phase by phase | [Roadmap](Roadmap) |
| Know which mistakes would invalidate the results | [Anti-Patterns](Anti-Patterns) |
| Find a specific file | [Repository-Layout](Repository-Layout) |
| Look up a term | [Glossary](Glossary) |
| Get a specific question answered | [FAQ](FAQ) |
| Start the next phase of work | [Data-Pipeline](Data-Pipeline) |
