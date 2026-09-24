# Anti-Patterns

This page lists the mistakes that would invalidate this project's results, what
each one looks like in code, how to detect it in this repository, and which
mechanism already defends against it. It is for anyone about to write or review
code here — particularly in `backend/training/` and `backend/app/`, where the
damage is invisible from the outside.

These are listed rather than left to ordinary review for one reason: **they do
not produce crashes, they produce impressive numbers.** A leaked test set does
not raise. A reimplemented transform does not raise. A threshold picked by
`argmax` does not raise. Every failure mode below exits zero, prints a metric
that looks like success, and survives a code review that is looking for bugs
instead of looking for these. By the time anyone notices, the number is already
in a README.

**Status:** Phase 0 of 9 is complete. Several of these anti-patterns are
already structurally impossible in this repository; several relate to code that
does not exist yet. Both cases are marked explicitly in
[How this repo defends against them](#how-this-repo-defends-against-them).

---

## The table

| Anti-pattern | Why it fails |
| ------------ | ------------ |
| `train_test_split(shuffle=True)` | Leaks duplicated flows; fabricates 99.9% scores |
| SMOTE before splitting | Synthetic rows appear in test; scores meaningless |
| Accuracy as headline metric | 99% benign makes it uninformative |
| ROC-AUC alone | Flatters imbalanced classifiers; PR-AUC is the honest view |
| `.fit()` in an endpoint | Not a serving architecture |
| Duplicated feature logic | Train/serve skew, silent and fatal |
| Per-row `predict()` in the replay loop | ~50x slowdown, stuttering demo |
| No dedup | 5,000 alerts from one host; queue unusable |
| Score with no explanation | Analysts ignore unexplained alerts |
| Auto-block button | Directly violates the brief |
| Unpersisted threshold / feature order | Backend and frontend silently disagree |
| Mock data in the final build | Every number must trace to a real model run |
| Trusting the CICIDS2017 threshold on live traffic | Domain shift; false-positive rate spikes, not a bug |
| Capturing or attack-testing a network you don't own | Illegal regardless of intent — lab-only, always |
| Generative/freeform remediation text per alert | Unreviewable advice a SOC can't trust; use the static playbook |

Each row is expanded below.

---

## `train_test_split(shuffle=True)`

**Why it fails.** Leaks duplicated flows; fabricates 99.9% scores.

CICIDS2017 contains a very large number of exact-duplicate rows. Shuffling puts
copies of the same record on both sides of the split, so the test set is
partially a memorised copy of the training set. The model is then scored on
rows it has already seen, and returns a number close to perfect.

**What it looks like in code**

Wrong:

```python
from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42          # shuffle=True is the default
)
```

Right — split on time, using the dataset's own day structure:

```python
train = frame[frame["day"].isin(["monday", "tuesday", "wednesday"])]
val   = frame[frame["day"] == "thursday"]
test  = frame[frame["day"] == "friday"]
```

The `random_state=42` in the wrong version is the tell. It looks like rigour —
a fixed seed, reproducible — and it is reproducing a leak.

**How to detect it in this repo**

```bash
grep -rn "train_test_split\|shuffle=True" backend/ --include=*.py
```

Anything outside a docstring is a finding. As of Phase 0 the only match is the
warning in `backend/training/split.py`'s docstring. A second check, which
catches the leak even if it was introduced some other way: after splitting,
intersect the splits on row content and assert the intersection is empty. That
is a Phase 1 acceptance criterion.

**Correct approach:** [Data-Pipeline](Data-Pipeline.md).

---

## SMOTE before splitting

**Why it fails.** Synthetic rows appear in test; scores meaningless.

There are two separate problems. First, oversampling before the split puts
synthetic rows interpolated *from training rows* into the test set, so the test
set is partly a function of the training set. Second, and specific to this
domain: interpolating between two flow records produces a record that could not
exist on a real network — a fractional packet count, a byte total inconsistent
with its own duration. The model learns a region of feature space that has no
physical referent.

**What it looks like in code**

Wrong:

```python
X_res, y_res = SMOTE().fit_resample(X, y)
X_train, X_test, y_train, y_test = split(X_res, y_res)   # already contaminated
```

Right — handle imbalance with weights, which requires no synthetic data at all:

```python
model = RandomForestClassifier(n_estimators=300, class_weight="balanced")
```

If SMOTE must be demonstrated, it is an ablation run strictly after the split,
reported alongside the weighted baseline, and it will underperform.

**How to detect it in this repo**

```bash
grep -rn "SMOTE\|imblearn\|fit_resample" backend/ --include=*.py
```

`imblearn` is not a dependency in `backend/pyproject.toml`, so an import would
also fail on a clean install. As of Phase 0 the only match is the prohibition
in `backend/training/train_supervised.py`'s docstring.

**Correct approach:** [ML-Models](ML-Models.md).

---

## Accuracy as headline metric

**Why it fails.** 99% benign makes it uninformative.

On traffic that is roughly 99% benign, the constant predictor `benign` scores
99% accuracy while detecting nothing at all. Any reported accuracy near that
number is indistinguishable from total failure, which makes it worse than no
metric — it is a metric that cannot fail.

**What it looks like in code**

Wrong:

```python
print(f"Accuracy: {accuracy_score(y_test, y_pred):.1%}")   # the headline
```

Right — accuracy may sit inside a table; the headline is PR-AUC, and per-class
recall is what tells you whether anything is actually being caught:

```python
report = classification_report(y_test, y_pred, output_dict=True)   # per class
pr_auc = average_precision_score(y_true_binary, y_score)           # headline
```

It shows up in UIs as much as in scripts: a hero tile reading `99.8% ACCURATE`
is the canonical version of this failure.

**How to detect it in this repo**

```bash
grep -rni "accuracy\|accuracy_score" backend/ frontend/src/
```

On the frontend this is asserted rather than reviewed:
`frontend/src/pages/SystemHealth.test.tsx` contains the case
`renders no accuracy figure anywhere`, which renders the whole app and asserts
the DOM text matches neither `/accura/i` nor a bare `NN.N%` pattern. Adding an
accuracy tile breaks the test suite.

**Correct approach:** [ML-Models](ML-Models.md),
[Frontend-Screens](Frontend-Screens.md), [Testing](Testing.md).

---

## ROC-AUC alone

**Why it fails.** Flatters imbalanced classifiers; PR-AUC is the honest view.

ROC plots true-positive rate against false-positive rate. The false-positive
rate has the true-negative count in its denominator, and on 99% benign traffic
that count is enormous — so thousands of false positives barely move the x
axis. The curve hugs the corner and the AUC looks excellent while the alert
queue is unworkable. Precision-recall conditions on the predicted positives
instead, so those same false positives are visible immediately.

**What it looks like in code**

Wrong:

```python
print("ROC-AUC:", roc_auc_score(y_true, y_score))     # and nothing else
```

Right — compute both, render them side by side, and say in the caption why they
disagree:

```python
pr_auc  = average_precision_score(y_true, y_score)    # headline
roc_auc = roc_auc_score(y_true, y_score)              # reported, not headline
# plot the two curves adjacent; the gap between them IS the explanation
```

**How to detect it in this repo**

```bash
grep -rn "roc_auc_score\|average_precision_score\|precision_recall_curve" backend/
```

Finding `roc_auc_score` without `average_precision_score` nearby is the
finding. `backend/training/evaluate.py` fixes the required metric set in its
module docstring — per-class precision, recall, F1 and support, the confusion
matrix, PR and ROC side by side, PR-AUC as headline, FPR at the chosen
threshold, and projected alerts per analyst per hour — and
`backend/app/routes/metrics.py` repeats the rule at the route layer.

**Correct approach:** [ML-Models](ML-Models.md),
[Frontend-Screens](Frontend-Screens.md).

---

## `.fit()` in an endpoint

**Why it fails.** Not a serving architecture.

A request handler that trains makes latency unbounded, makes the model a
function of request ordering, and destroys the audit trail: you can no longer
say which model version produced which alert, because the model changed halfway
through the day. For anything security-adjacent, that provenance is not
optional.

**What it looks like in code**

Wrong:

```python
@router.post("/score")
def score(flows: list[FlowRecord]):
    model = RandomForestClassifier().fit(X_train, y_train)   # per request
    return model.predict_proba(to_matrix(flows))
```

Right — load once in the lifespan, read from `app.state` in the handler:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.bundle = load_bundle(settings.artifacts_path)
    yield

@router.post("/score")
def score(request: Request, flows: list[FlowRecord]):
    return request.app.state.bundle.score_batch([f.model_dump() for f in flows])
```

**How to detect it in this repo**

```bash
grep -rn "\.fit(\|fit_transform\|fit_resample" backend/app/
```

The only current match is the line in `backend/app/inference.py`'s docstring
saying there is none. The right shape is already in place: `lifespan` in
`backend/app/main.py` calls `load_bundle` once and parks the result on
`app.state.bundle`, and `health()` reads it back off `request.app.state`.

**Correct approach:** [Architecture](Architecture.md),
[Code-Backend-Core](Code-Backend-Core.md).

---

## Duplicated feature logic

**Why it fails.** Train/serve skew, silent and fatal.

If the API reimplements a transform — even correctly, even in the same order —
the two implementations will diverge eventually, and the divergence produces no
error. The model receives a matrix of the right shape and dtype and returns
numbers that are simply wrong. No exception, no log line, nothing to grep for.
This is the single most dangerous item on this page.

**What it looks like in code**

Wrong — the API grows its own copy:

```python
# app/inference.py
def prepare(flows):
    frame = pd.DataFrame(flows)
    frame.columns = [c.strip().lower().replace(" ", "_") for c in frame.columns]
    return scaler.transform(frame[FEATURE_COLUMNS])       # a second source of truth
```

Right — one module, imported by both sides:

```python
# app/inference.py
from training.features import build_feature_matrix
matrix = build_feature_matrix(frame, bundle)
```

Note the subtlety in the wrong version: `.replace(" ", "_")` is not the same
function as `normalise_column_name`, which collapses every run of non-alphanumeric
characters. `Flow Bytes/s` becomes `flow_bytes/s` in one and `flow_bytes_s` in
the other. One column silently goes missing and its position is filled by
whatever `reindex` puts there.

**How to detect it in this repo**

```bash
grep -rn "import" backend/app/inference.py | grep features
grep -rn "RobustScaler\|StandardScaler\|\.transform(" backend/app/
```

The first command should show `from training.features import ...` — the
serving path taking a hard dependency on the training module. The second should
find scaling logic only where it belongs. Phase 8 adds a feature-parity test
asserting `features.py` produces identical output on both paths.

**Correct approach:** [Data-Pipeline](Data-Pipeline.md),
[Code-Backend-Training](Code-Backend-Training.md).

---

## Per-row `predict()` in the replay loop

**Why it fails.** Roughly 50x slowdown, stuttering demo.

scikit-learn's per-call overhead — validation, dtype checks, array setup —
dominates the actual arithmetic on a single row. Calling `predict()` once per
flow inside a 100x replay makes the loop the bottleneck instead of the clock,
so the stream stutters and the demo looks broken even though nothing is wrong.

**What it looks like in code**

Wrong:

```python
for flow in batch:
    proba = model.predict_proba(to_matrix([flow]))[0]   # one call per row
    await push(build_alert(flow, proba))
```

Right — one call, whole matrix:

```python
matrix = build_feature_matrix(pd.DataFrame(batch), bundle)
probas = model.predict_proba(matrix)                    # one call per batch
for flow, proba in zip(batch, probas, strict=True):
    await push(build_alert(flow, proba))
```

**How to detect it in this repo**

```bash
grep -rn -B3 "predict_proba\|\.predict(" backend/app/
```

A `predict` call with a `for` above it is the finding. The batch shape is
already fixed by the signature: `ModelBundle.score_batch` in
`backend/app/inference.py` takes `list[dict[str, Any]]` and returns
`list[dict[str, Any]]`, so there is no single-row entry point to reach for.
Phase 5 implements the replay loop against that signature.

**Correct approach:** [Architecture](Architecture.md),
[Code-Backend-Pipeline](Code-Backend-Pipeline.md).

---

## No dedup

**Why it fails.** 5,000 alerts from one host; queue unusable.

A single compromised host emits thousands of flows. Without collapsing them,
the triage queue fills with thousands of rows describing one incident, and it
becomes unusable within about thirty seconds of starting a replay. The
information content of alert 4,000 is zero, but an analyst still has to
dismiss it.

**What it looks like in code**

Wrong:

```python
for alert in alerts:
    session.add(Alert(**alert))     # one row per flow, forever
```

Right — key on source, class and a floored time bucket; on a hit, increment
rather than insert:

```python
key = dedupe_key(alert["src_ip"], alert["family"], alert["detected_at"])
existing = session.query(Alert).filter_by(dedupe_key=key).one_or_none()
if existing:
    existing.occurrence_count += 1
    existing.last_seen = alert["detected_at"]
else:
    session.add(Alert(dedupe_key=key, occurrence_count=1, **alert))
```

**How to detect it in this repo**

Run a replay and watch the queue. Structurally, the pieces are already in
place: `dedupe_key(src_host, alert_class, timestamp)` is implemented in
`backend/app/dedupe.py` — it floors the epoch second to
`settings.dedupe_window_seconds` (default 300, from
`IDS_DEDUPE_WINDOW_SECONDS`) and returns `"{src_host}|{alert_class}|{bucket}"`.
The `alerts` table carries `dedupe_key`, `occurrence_count` (with a
`occurrence_count >= 1` check constraint), `first_seen` and `last_seen`, and
the index `ix_alerts_dedupe_key_last_seen` exists for the lookup. An insert
path that ignores all of that is the finding.

**Correct approach:** [Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Database-Schema](Database-Schema.md).

---

## Score with no explanation

**Why it fails.** Analysts ignore unexplained alerts.

A number between 0 and 1 gives an analyst nothing to act on and nothing to
disagree with, so it gets closed unread. It also poisons the feedback loop: a
verdict on an unexplained alert is a guess, and guesses fed back into training
are worse than collecting no labels at all.

**What it looks like in code**

Wrong:

```python
Alert(risk_score=0.93, family="botnet")     # and nothing else
```

Right — the reason travels with the score, in the form the stage can actually
produce:

```python
Alert(
    risk_score=0.93,
    family="botnet",
    explanation=explain_supervised(model, row),   # TreeSHAP top-5
    narrative=narrate(explanation),               # English sentence
    recommended_actions=playbook_for("botnet"),   # static playbook
)
```

For Stage 2 the explanation is free — the per-feature squared reconstruction
error is already computed, and the features the model failed hardest to
reconstruct are precisely why the row looks anomalous. KernelSHAP on a neural
net is slower, approximate, and buys nothing:

```python
per_feature_error = (x - x_hat) ** 2
top_contributors  = argsort(per_feature_error)[-5:]
```

**How to detect it in this repo**

```bash
grep -rn "explanation\|narrative\|recommended_actions" backend/app/
```

The columns exist on `Alert` in `backend/app/models.py` and are nullable at the
database level, so the guarantee is a property of the pipeline rather than of
the schema. `backend/app/explain.py` defines `explain_supervised`,
`explain_anomaly` and `narrate`; all three raise `NotImplementedError` naming
Phase 5. An alert constructed without them once Phase 5 lands is the finding.

**Correct approach:** [Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Frontend-Screens](Frontend-Screens.md).

---

## Auto-block button

**Why it fails.** Directly violates the brief.

The arithmetic: at a million flows a day, a 0.1% false-positive rate is a
thousand false alerts a day. Wire that to a firewall and it is a thousand
severed legitimate connections a day, with no human in the path. The system
does not have to be bad to take production down — it has to be slightly wrong
at scale, which is the normal condition of every statistical detector.

**What it looks like in code**

Wrong, in any of these shapes:

```python
@router.post("/alerts/{alert_id}/block")       # an endpoint
def block_source(alert_id: int): ...

if settings.auto_block_enabled:                 # a flag
    firewall.drop(alert.src_ip)
```

```tsx
<Button variant="destructive" onClick={blockSource}>Block source</Button>
```

Right — containment, if present at all, is manual, confirmed and audited, and
the default build has none.

**How to detect it in this repo**

```bash
grep -rni "block\|quarantine\|iptables\|firewall" backend/app/ frontend/src/
```

This one is asserted rather than reviewed, in two places.
`test_no_route_mentions_blocking` in `backend/tests/test_api_surface.py` reads
the served OpenAPI schema and asserts no path contains `block`, `drop` or
`quarantine`. `test_auto_block_cannot_be_enabled` in
`backend/tests/test_config.py` asserts that `Settings(allow_auto_block=True)`
raises, because the `_reject_auto_block` field validator in
`backend/app/config.py` refuses the value at startup with the message "never
drops traffic". `IDS_ALLOW_AUTO_BLOCK=false` exists in `.env.example` so the
constraint is greppable rather than merely absent.

**Correct approach:** [Project-Overview](Project-Overview.md),
[Configuration](Configuration.md).

---

## Unpersisted threshold / feature order

**Why it fails.** Backend and frontend silently disagree.

If `tau_sup` lives as a literal in a training script and a different literal in
a route handler, they drift and nothing reports it. If the feature order is not
persisted with the scaler, the serving path reconstructs it from whatever
`DataFrame` it happens to receive, and column order becomes a function of JSON
key order. Both failures produce plausible numbers.

**What it looks like in code**

Wrong — three separate sources of truth:

```python
# train_supervised.py
TAU_SUP = 0.87
joblib.dump(model, "supervised_model.pkl")      # threshold not saved

# app/routes/score.py
THRESHOLD = 0.9                                  # drifted, silently
```

Right — scaler, order, hash and thresholds travel together as one bundle,
because any one of them alone is insufficient to reproduce the training-time
matrix:

```python
bundle = build_preprocessing_bundle(
    scaler=scaler,
    feature_order=feature_order,
    dropped_columns=dropped,
    port_encoding=port_encoding,
)                                      # schema_hash derived from feature_order
save_preprocessing_bundle(bundle, artifacts_dir / "preprocessing.pkl")
```

**How to detect it in this repo**

```bash
grep -rn "0\.5\|tau_sup\|tau_anom\|threshold" backend/app/ backend/training/
```

A numeric threshold literal outside a docstring is the finding. Structurally,
the bundle contract is a `TypedDict` — `PreprocessingBundle` in
`backend/training/features.py` requires `scaler`, `feature_order`,
`dropped_columns`, `port_encoding` and `schema_hash` together — and
`build_preprocessing_bundle` derives the hash from the order rather than
accepting one. On the serving side, `ModelBundle` holds `tau_sup` and
`tau_anom` read out of `model_card.json`, and `ModelVersion` in
`backend/app/models.py` records both thresholds per version so an old alert can
be re-read against the exact threshold that produced it.
`test_bundle_round_trips_with_a_matching_hash` in
`backend/tests/test_features.py` pins the round trip.

**Correct approach:** [Data-Pipeline](Data-Pipeline.md),
[Configuration](Configuration.md), [Database-Schema](Database-Schema.md).

---

## Mock data in the final build

**Why it fails.** Every number must trace to a real model run.

A hardcoded sample response is indistinguishable from a real one at the UI
layer. Once one exists, nobody can tell which figures on screen came from a
model and which came from a fixture, and the whole evaluation becomes
unauditable.

**What it looks like in code**

Wrong:

```python
@router.get("/metrics/model")
def model_metrics():
    return {"pr_auc": 0.94, "recall": {"botnet": 0.88}}   # invented
```

Right — say what is true, in a form a caller can branch on:

```python
@router.get("/metrics/model", responses={501: {"model": NotImplementedResponse}})
def model_metrics():
    return not_implemented("GET /metrics/model", "Phase 5 (backend API)")
```

**How to detect it in this repo**

```bash
grep -rn "mock\|fixture\|sample_data\|faker\|lorem" backend/app/ frontend/src/
```

Every unimplemented v1 route in this repository returns `501` through the
`not_implemented(endpoint, phase)` helper in `backend/app/routes/__init__.py`,
whose body is a `NotImplementedResponse` carrying `detail`, `phase` and
`endpoint`. `test_unimplemented_routes_answer_501_with_a_phase` in
`backend/tests/test_api_surface.py` asserts the status and both fields for
every route. `GET /health` reports `model_version: "unloaded"` because that is
what is loaded, and `test_health_status_is_ok_without_artifacts` asserts it.
Test fixtures under `backend/tests/` and stubbed `fetch` in
`frontend/src/pages/SystemHealth.test.tsx` are not this anti-pattern — they are
test scope, and they never reach the served build.

**Correct approach:** [API-Reference](API-Reference.md),
[Code-Backend-Routes](Code-Backend-Routes.md).

---

## Trusting the CICIDS2017 threshold on live traffic

**Why it fails.** Domain shift; the false-positive rate spikes, and that is not
a bug.

`tau_anom` is a percentile of reconstruction error measured on 2017 lab
traffic. Today's traffic is different applications, mostly TLS, an entirely
different baseline. Relative to a five-year-old notion of normal, nearly
everything looks anomalous. The failure is not that the number is wrong for the
dataset — it is that it was never a property of the model, only of the
distribution it was measured against.

**What it looks like in code**

Wrong:

```python
if reconstruction_error >= bundle.tau_anom:      # the CICIDS2017 percentile
    emit(alert)                                   # on live capture, day one
```

Right — burn in first, in log-only mode, then recompute locally and keep both:

```python
# shadow mode: score everything, alert no one
errors = [score(row) for row in local_burn_in_flows]
tau_anom_local = numpy.percentile(errors, 99.5)
# report tau_anom_dataset AND tau_anom_local, and explain the gap
```

**How to detect it in this repo**

```bash
grep -rn "tau_anom" backend/app/
```

A live-capture path that reads `bundle.tau_anom` without a local recalibration
step is the finding. `backend/app/live_capture.py` states the expected
behaviour in its module docstring — a false-positive rate well above what the
validation numbers promised, handled with a shadow-mode burn-in rather than
treated as a defect — and `start_ingest` raises `NotImplementedError` naming
Phase 9. `POST /api/v1/ingest/start` currently answers `501` naming the same
phase. The gap between the two thresholds is a Phase 9 deliverable in its own
right, not something to suppress.

**Correct approach:** [Roadmap](Roadmap.md), [ML-Models](ML-Models.md).

---

## Capturing or attack-testing a network you don't own

**Why it fails.** Illegal regardless of intent — lab-only, always.

Packet capture on a network you do not control or lack authorisation to
monitor is illegal in most jurisdictions whatever the motive, and pointing
`nmap`, `hydra` or `hping3` at a host you do not own is unauthorised access on
the same terms. Good intentions are not a defence, and neither is "it was for a
project".

**What it looks like in practice**

Wrong: capturing on a shared office, campus or café network; scanning a public
host to "check the detector works"; brute-forcing a service you do not
administer.

Right: an isolated lab of two to four VMs on one virtual switch — full control,
fully reproducible, and it doubles as the place the test attacks run; your own
router's mirror or SPAN port; or your own machine's interface. Attack traffic
generated by you, against your own hosts, with a throwaway credential you do
not use anywhere else.

This costs nothing in evaluation quality. It gains something: because you
triggered the attack, you know the exact ground truth, which makes Phase 9 a
second independent version of the leave-one-attack-out test.

**How to detect it in this repo**

```bash
grep -rn "interface\|pcap\|tcpdump\|sniff" backend/app/
```

A capture path with no authorisation precondition is the finding.
`backend/app/live_capture.py` states authorisation as a hard precondition in
its module docstring, and `README.md` carries an Authorisation section scoping
both capture and self-run attacks to owned or explicitly authorised targets.

**Correct approach:** [Project-Overview](Project-Overview.md),
[Roadmap](Roadmap.md).

---

## Generative/freeform remediation text per alert

**Why it fails.** Unreviewable advice a SOC can't trust; use the static
playbook.

A fixed playbook is something a security team reviews once and then trusts. A
playbook improvised per alert has to be re-verified every single time, which
removes the only reason to have one. Worse, it will confidently produce a fix
for an alert the system does not actually recognise, and a wrong playbook does
more damage than an honest shrug.

**What it looks like in code**

Wrong:

```python
alert.recommended_actions = generate_remediation(alert)   # freeform, per alert
```

Right — a static, reviewed lookup keyed on family, with an honest entry for the
case that has no answer:

```python
PLAYBOOKS = {
    "brute_force": {
        "technique": "T1110",
        "means": "Repeated login attempts against one service, guessing credentials",
        "actions": [
            "Lock or rotate the targeted account",
            "Enforce MFA on the service",
            "Rate-limit or geo-fence the service",
        ],
    },
    # ...
}

def playbook_for(family: str | None) -> dict:
    if family is None:                      # UNCLASSIFIED_ANOMALY
        return {
            "technique": None,
            "means": "Does not match a known technique — that is what Stage 2 is for",
            "actions": ["No playbook exists yet. Escalate for manual investigation."],
        }
    return PLAYBOOKS[family]
```

The `family is None` branch is the important one. It is the difference between
a system that admits what it does not know and one that invents a fix.

**How to detect it in this repo**

```bash
grep -rn "llm\|openai\|anthropic\|generate_\|prompt" backend/
```

`backend/app/remediation.py` states in its docstring that the table is static
and reviewed, that unclassified anomalies get the honest entry, and that a
wrong playbook does more damage than an honest shrug. `playbook_for` raises
`NotImplementedError` naming Phase 5. `backend/app/mitre.py` carries the
matching technique lookup on the same terms. No text-generation dependency
appears in `backend/pyproject.toml`.

**Correct approach:** [Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Frontend-Screens](Frontend-Screens.md).

---

## How this repo defends against them

Each row below was checked against the source before being written. "Enforced"
means a test fails or the process refuses to start. "Structural" means the code
is shaped so the mistake has nowhere to go. "Documented" means the rule is
written where the implementing code will be, and nothing mechanical prevents it
yet — those are the ones to watch in review.

| Anti-pattern | Mechanism in this repository | Kind |
| ------------ | ---------------------------- | ---- |
| `train_test_split(shuffle=True)` | `backend/training/split.py` exists as the single splitting module and its docstring fixes the day structure and the prohibition. No `train_test_split` call exists anywhere in `backend/`. | Documented — Phase 1 adds the duplicate-overlap assertion |
| SMOTE before splitting | `backend/training/train_supervised.py` docstring prohibits it and prescribes `class_weight` instead. `imblearn` is not a dependency. | Documented |
| Accuracy as headline | `renders no accuracy figure anywhere` in `frontend/src/pages/SystemHealth.test.tsx` asserts the rendered DOM matches neither `/accura/i` nor `\d{2}\.\d%`. `backend/training/evaluate.py` and `backend/app/routes/metrics.py` fix the metric set. | Enforced (frontend) |
| ROC-AUC alone | `evaluate.py` requires PR and ROC rendered side by side with PR-AUC as headline; the caption explaining the gap is a Phase 6 acceptance criterion. | Documented |
| `.fit()` in an endpoint | `lifespan` in `backend/app/main.py` calls `load_bundle` once and stores the result on `app.state.bundle`; handlers read `request.app.state`. No `.fit(` call exists under `backend/app/`. | Structural |
| Duplicated feature logic | `backend/app/inference.py` imports `compute_schema_hash` from `training.features` — serving depends on the training module. `PreprocessingBundle` is the only transform contract. Phase 8 adds the parity test. | Structural |
| Per-row `predict()` | `ModelBundle.score_batch(flows: list[dict]) -> list[dict]` is the only scoring entry point; there is no single-row signature to call. | Structural |
| No dedup | `dedupe_key()` is implemented in `backend/app/dedupe.py` (Phase 0, because it is pure and cheap to test). The `alerts` table carries `dedupe_key`, `occurrence_count` with a `>= 1` check, `first_seen`, `last_seen`, and the `ix_alerts_dedupe_key_last_seen` index. | Structural |
| Score with no explanation | `explanation`, `narrative` and `recommended_actions` columns exist on `Alert`; `backend/app/explain.py` defines the two stage-specific explainers and the narrator. Columns are nullable, so the guarantee is the pipeline's. | Documented |
| Auto-block button | `_reject_auto_block` validator in `backend/app/config.py` raises on `IDS_ALLOW_AUTO_BLOCK=true`; `test_auto_block_cannot_be_enabled` asserts it. `test_no_route_mentions_blocking` asserts no served path contains `block`, `drop` or `quarantine`. Confirmed: no such path exists in `backend/app/routes/`. | Enforced (twice) |
| Unpersisted threshold / feature order | `PreprocessingBundle` is a `TypedDict` requiring scaler, order, dropped columns, port encoding and hash together. `build_preprocessing_bundle` derives the hash from the order. `_verify_schema_hash` raises `SchemaHashMismatch` on a missing or mismatched hash, fatally, inside `lifespan`. `ModelVersion` records `tau_sup` and `tau_anom` per version. | Enforced |
| Mock data in the final build | Every unimplemented route returns `not_implemented(endpoint, phase)` → `501` with a `NotImplementedResponse`. `test_unimplemented_routes_answer_501_with_a_phase` asserts it for all fifteen. `/health` reports `"unloaded"`, asserted by `test_health_status_is_ok_without_artifacts`. | Enforced |
| Trusting the CICIDS2017 threshold live | `backend/app/live_capture.py` prescribes a shadow-mode burn-in and a locally recomputed `tau_anom`; `POST /ingest/start` answers `501` naming Phase 9, so there is no live path to misuse yet. | Documented |
| Capturing a network you don't own | Authorisation stated as a hard precondition in `backend/app/live_capture.py` and in the README's Authorisation section. No capture code exists. | Documented |
| Generative remediation | `backend/app/remediation.py` and `backend/app/mitre.py` are specified as static reviewed lookups with an explicit honest entry for `UNCLASSIFIED_ANOMALY`. No text-generation dependency in `backend/pyproject.toml`. | Documented |

One more structural defence that does not map to a single row but backs several
of them: the `family_matches_kind` check constraint on the `alerts` table.

```sql
(kind = 'UNCLASSIFIED_ANOMALY' AND family IS NULL)
OR (kind = 'KNOWN' AND family IS NOT NULL)
```

The database refuses to store an unclassified anomaly that carries a family
label, or a known alert that carries none. That closes off the most tempting
shortcut in the whole project — quietly assigning the nearest family to a
Stage 2 hit so the UI looks tidier — which would erase the distinction the
entire thesis rests on.
`test_unclassified_anomaly_cannot_carry_a_family` and
`test_a_known_alert_must_carry_a_family` in
`backend/tests/test_schema_portability.py` assert both directions.
