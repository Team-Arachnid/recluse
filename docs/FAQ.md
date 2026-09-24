# FAQ

Short answers to the questions this project actually gets asked. Each one links
to the page that covers it properly. Where an answer would require a number that
has not been measured yet, it says so instead of guessing.

Questions are grouped: [the project and its claim](#the-project-and-its-claim),
[first run](#first-run), [the data and the evidence](#the-data-and-the-evidence),
[models and thresholds](#models-and-thresholds),
[running and configuring it](#running-and-configuring-it), and
[contributing](#contributing).

---

## The project and its claim

### What is Recluse, in one sentence?

A two-stage machine-learning intrusion detection system that scores network flow
records and presents the results to a security analyst as a ranked, explained
triage queue. See [Project Overview](Project-Overview.md).

### What is the one claim it has to defend?

That it detects attack traffic it was never trained on. Everything else in the
repository exists to make that claim measurable rather than asserted. The
measurement is leave-one-attack-out, and it has **not been run yet** — it arrives
in Phase 4. See [Roadmap](Roadmap.md).

### Why two models instead of one good one?

They answer different questions, and neither answer substitutes for the other.

| | Stage 1 | Stage 2 |
| - | ------- | ------- |
| Kind | Supervised classifier | Autoencoder trained on benign traffic only |
| Question it answers | "Which named attack is this?" | "How unlike normal traffic is this?" |
| Sees attack labels in training | Yes | No — never |
| Fails on | Anything absent from its label set | Benign novelty, which it calls anomalous |

A supervised classifier cannot name a family nobody labelled for it. An anomaly
detector cannot tell you *which* attack it found, and cheerfully flags a new
backup job. Running both and fusing their outputs is what lets the system name
what it knows while still reacting to what it does not. See
[Architecture](Architecture.md) and [Models and Evaluation](ML-Models.md).

### Does this replace Snort or Suricata?

No. Signature-based IDS is precise about the attacks someone has already written
a rule for. Recluse is aimed at the complement of that set — behaviourally
distinguishable traffic that has no rule yet. The two are layered, not
substituted.

### Why does it only alert? Why is there no block button?

A false positive from an alerting system costs an analyst a few minutes. A false
positive from a blocking system takes production traffic offline. At the
false-positive rates any flow-level detector realistically achieves, automatic
blocking is the more expensive failure by a wide margin.

The constraint is enforced in code rather than merely documented.
`IDS_ALLOW_AUTO_BLOCK` exists as a setting so it is greppable, and setting it to
true raises at startup:

```python
raise ValueError(
    "IDS_ALLOW_AUTO_BLOCK cannot be enabled. The system alerts, "
    "ranks and explains; it never drops traffic. Containment "
    "actions are manual and confirmed by an analyst."
)
```

See `backend/app/config.py` and the "Auto-block button" entry in
[Anti-Patterns](Anti-Patterns.md).

### Is there an LLM in this?

No. The models are a scikit-learn classifier and a PyTorch autoencoder, both
trained in this repository from `backend/training/`. Alert explanations come from
feature attribution over a fixed template, not from generated prose — see the
"Generative/freeform remediation text per alert" entry in
[Anti-Patterns](Anti-Patterns.md) for why.

---

## First run

### Why does `/api/v1/health` report `model_version: "unloaded"`?

Because that is the truth. Phase 0 ships the scaffolding — the API, the
migrations and the dashboard shell — and no model has been trained yet. The
health endpoint reports what is actually resident on `app.state`, not a
hardcoded string.

`ModelBundle.load()` finds no `preprocessing.pkl` under `backend/artifacts/`,
logs that this is expected, and returns an unpopulated bundle whose `version`
stays at `UNLOADED_VERSION`. Status is still `ok`, because a scaffold with no
artifacts is working as designed. `degraded` is reserved for the different case:
a bundle was found and could not be made usable. See
[Code: Backend Core](Code-Backend-Core.md).

### So `status: "ok"` with no model loaded is not a bug?

Correct. `ok` means the process is healthy. `model_version` is the separate
question of what it is serving, and the two are reported separately so neither
one hides the other.

### Why do most endpoints return 501?

The full v1 route surface is registered in Phase 0 so the OpenAPI schema — and
therefore the generated frontend types in `frontend/src/types/api.d.ts` — exists
from the start. Endpoints that later phases implement answer `501` with a
machine-readable body naming the phase that fills them in:

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "POST /replay/start"
}
```

The alternative — returning plausible-looking placeholder data — makes "not built
yet" indistinguishable from "built and broken", and tends to survive into the
final build. See [API Reference](API-Reference.md) for the per-route phase map,
and the "Mock data in the final build" entry in [Anti-Patterns](Anti-Patterns.md).

### The dashboard is almost empty. Is it broken?

Not unless it says so. Phase 0's dashboard is a shell with one working screen,
System Health, which fetches `/api/v1/health` and renders the real response. The
other six screens arrive in Phase 6. If the health panel says the backend is
unreachable, that is a genuine failure — see the proxy and CORS rows in
[Getting Started](Getting-Started.md#troubleshooting).

### Where are the trained model files? `backend/artifacts/` is empty.

Nothing has been trained yet, and when it has, the artifacts still will not be in
git. `backend/artifacts/*` is gitignored, along with `*.pkl` and `*.pt` anywhere
in the tree, because they are reproducible output rather than source. Only
`.gitkeep` and the directory's `README.md` are tracked. The same applies to
`data/raw/`, `data/interim/` and `data/processed/`, and to `*.csv`, `*.parquet`,
`*.pcap` and `*.pcapng` anywhere — a capture file dropped in the wrong directory
does not get committed by accident. See
[Repository Layout](Repository-Layout.md).

### `sqlite3.OperationalError: no such table: alerts`

The database file exists but migrations were never applied — usually after
`make clean` or a manual delete. Run `make migrate` (`./make.ps1 migrate` on
Windows). Both `make dev` and the backend container run `alembic upgrade head`
automatically, so this only shows up when the API is started some other way.

### The API refuses to start with a schema hash error. What did I break?

Probably nothing — an artifact bundle and the feature contract in
`training/features.py` have drifted apart. `SchemaHashMismatch` at startup is
deliberate and fatal:

> Feeding columns to a model in a different order than it was trained on
> produces garbage scores without raising anything — train/serve skew is silent,
> so the check is loud.

The fix is to retrain rather than to serve the inconsistent bundle. See
[Code: Backend Core](Code-Backend-Core.md) and
[Code: Training](Code-Backend-Training.md).

---

## The data and the evidence

### Which dataset, and why that one?

CICIDS2017 — labelled flow records covering benign traffic plus several attack
families, which is what leave-one-attack-out needs. It also has well-known
defects, and [Data Pipeline](Data-Pipeline.md) documents each one and how it is
handled rather than leaving them to be rediscovered.

### Why is the dataset not in the repository?

It is large, it is redistributable from its original source, and a repository
carrying hundreds of megabytes of CSV is unclonable for anyone who only wants the
code. `IDS_DATA_DIR` points at wherever you put it.

### What is leave-one-attack-out, concretely?

For each attack family: remove every row of that family from supervised training,
retrain Stage 1, leave the autoencoder untouched — it never saw attacks anyway —
then run the full fusion pipeline on a test set that *does* contain the family,
and record what fraction was caught and by which stage.

That is the difference between "catches attacks it was never trained on" as an
assertion and as a measurement. Phase 4 produces the table as `reports/loao.md`.
**Not measured yet.**

### Is LOAO proof that it detects novel attacks?

No, and the project does not claim it is. A held-out family still comes from the
same 2017 capture, the same lab topology and the same generation tooling as the
training data. LOAO is the strongest available evidence and it remains evidence.
This is stated as limitation 5 in
[Project Overview](Project-Overview.md#limitations-stated-up-front).

### Why is accuracy not the headline metric?

Because on traffic that is overwhelmingly benign, a model that predicts "benign"
for everything scores extremely well on accuracy and detects nothing. The
reported metrics are per-class recall, precision at the operating threshold,
alerts per analyst hour, and the LOAO table. See the "Accuracy as headline
metric" and "ROC-AUC alone" entries in [Anti-Patterns](Anti-Patterns.md).

### Why does dropping IP addresses matter?

Because a model given `source_ip` learns which hosts were attackers in 2017
rather than what attacks look like, and scores brilliantly on a test set drawn
from the same capture. `flow_id`, `source_ip`, `destination_ip` and `source_port`
are on the `LEAKAGE_COLUMNS` denylist in `training/features.py`.
`destination_port` is deliberately *not* on it, so Phase 1 has to make an
explicit decision about it rather than inheriting one.

### What are the known limitations?

Six, stated up front in
[Project Overview](Project-Overview.md#limitations-stated-up-front): flow
features cannot see encrypted payload content; CICIDS2017 is synthesised lab
traffic; "unusual" is not "malicious"; an adaptive adversary can pace under the
threshold; LOAO measures held-out known families rather than genuinely novel
ones; and a 2017-trained model pointed at today's mostly-TLS traffic will
misfire until it is recalibrated locally.

---

## Models and thresholds

### How is the decision threshold chosen?

From analyst capacity, not from the default 0.5. The settings are the inputs to
a false-positive budget:

| Setting | Default | Meaning |
| ------- | ------- | ------- |
| `IDS_ANALYST_CAPACITY_PER_HOUR` | 40 | Alerts one analyst can triage in an hour |
| `IDS_ANALYST_SHIFT_HOURS` | 8 | Length of a shift |
| `IDS_EXPECTED_DAILY_FLOW_VOLUME` | 1,000,000 | Flows the deployment sees per day |

40 × 8 = 320 alerts/day over 1,000,000 flows/day gives a target FPR of
3.2 × 10⁻⁴. `tau_sup` is then the smallest threshold whose *measured* FPR stays
under that number. The backend logs the budget at startup. See
[Configuration](Configuration.md).

### Why is the score explained at all? Is it not just a number?

An alert an analyst cannot act on is noise with extra steps. Every alert carries
the features that drove its score, so the analyst can agree or disagree with the
model instead of trusting it. See the "Score with no explanation" entry in
[Anti-Patterns](Anti-Patterns.md) and
[Dashboard Screens](Frontend-Screens.md).

### Why are duplicate alerts collapsed?

A port scan is thousands of flows and one event. Without deduplication the queue
fills with the same finding and the analyst stops reading it.
`IDS_DEDUPE_WINDOW_SECONDS` defaults to 300.

### Will this work on my live network as-is?

It will run, and it will over-fire. A model trained on 2017 lab traffic and
pointed at today's mostly-TLS traffic is facing domain shift: the thresholds were
calibrated against a different traffic distribution, so expect Stage 1 to
under-fire and Stage 2 to carry more of the weight than it did on the dataset.
Phase 9 handles this with a shadow-mode burn-in against a local baseline rather
than treating it as a defect. See the "Trusting the CICIDS2017 threshold on live
traffic" entry in [Anti-Patterns](Anti-Patterns.md).

### Can I point the capture at any network?

Only one you own or have written authorisation to test. This is a legal
boundary, not a style preference, and it is covered in
[Project Overview](Project-Overview.md) and in the "Capturing or attack-testing a
network you don't own" entry in [Anti-Patterns](Anti-Patterns.md).

---

## Running and configuring it

### `make` is not recognised on Windows.

Use `./make.ps1 <target>` — it mirrors every `Makefile` target. If PowerShell
refuses to run it, `Set-ExecutionPolicy -Scope Process -ExecutionPolicy
RemoteSigned` for the current session. [Getting Started](Getting-Started.md)
has the full troubleshooting table.

### How do I start everything?

`make dev` (or `./make.ps1 dev`) starts the API on <http://localhost:8000> and
the dashboard on <http://localhost:5173>, applying migrations first.
`make up` runs the containerised stack instead.

### How do I change a port, a path or a threshold?

Through the environment, never in code. Everything tunable arrives through
`backend/app/config.py` with the `IDS_` prefix, and the frontend reads `VITE_`
variables. Copy `.env.example` to `.env` and edit it. If you change `IDS_PORT`,
change `VITE_DEV_PROXY_TARGET` to match or the dashboard proxies to the old one.
[Configuration](Configuration.md) lists every setting.

### Does it need Postgres?

No. It defaults to SQLite at `data/ids.db` and runs against Postgres by changing
`IDS_DATABASE_URL` alone — the migrations and models are written to work on
both, and `test_schema_portability.py` exists to keep it that way.

### Why is `frontend/src/types/api.d.ts` generated?

So the dashboard cannot drift from the API's actual contract. It is produced
from the live OpenAPI schema by `make gen-types`, which requires the backend to
be running. Do not hand-edit it; its banner says so. If TypeScript complains
about response fields that plainly exist, the file is stale — regenerate it.

---

## Contributing

### Where does new code go?

Backend modules under `backend/app/`, training code under `backend/training/`,
dashboard code under `frontend/src/`. [Repository Layout](Repository-Layout.md)
covers the whole tree and where each kind of change belongs.

### Should feature engineering live in the API?

No — it lives in `backend/training/features.py` and the serving path imports it.
Two implementations of the same feature logic drift, and the drift shows up as
quietly wrong scores rather than as an error. See the "Duplicated feature logic"
entry in [Anti-Patterns](Anti-Patterns.md).

### Can an endpoint train or refit a model?

No. Training is offline batch. Models are loaded exactly once in the FastAPI
lifespan and parked on `app.state`; no request handler calls `.fit()`. See the
"`.fit()` in an endpoint" entry in [Anti-Patterns](Anti-Patterns.md).

### How do I edit these docs?

Edit the Markdown in `docs/` in this repository and commit it. Pushing to `main`
rebuilds and republishes the site automatically. There is no web editor and no
second copy to keep in step. [Docs Publishing](Docs-Publishing.md) covers the
build, local preview, and how to add or rename a page.

### How do I run the tests?

`make test` runs both suites; `make test-backend` and `make test-frontend` run
one each. [Testing](Testing.md) covers what is protected today and what is not
covered yet.
