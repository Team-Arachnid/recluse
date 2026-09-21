Build Prompt — ML Network Intrusion Detection System with SOC Triage Dashboard
How to use this. Drop this file into an empty repo as BUILD_PROMPT.md and point a coding agent at it (Claude Code, Cursor, Copilot Workspace). Do not paste it all at once and say "build it." Say:

"Read BUILD_PROMPT.md. Execute Phase 0 only. Stop at the checkpoint and report."

Then advance one phase at a time. Every phase has acceptance criteria the agent can verify itself. Agents that are handed ten phases at once will stub nine of them and tell you it's done.

PART 1 — Mission and non-negotiables
Mission
Build a production-shaped network intrusion detection system that surfaces attacks signature-based IDS cannot catch, and present its output to a security analyst through a triage dashboard. The system alerts; it never auto-blocks.

The core technical claim this project must defend: it detects attack traffic it was never trained on. Everything below exists to make that claim measurable rather than asserted.

The AI/ML model(s) you will train
This is not an app wrapped around someone else's model — the ML is yours, trained by you, on the datasets above, producing real artifact files on disk. Two models come out of this project:

Model	Library	Trained on	Learns to answer	Artifact
Model A	Supervised classifier	scikit-learn RandomForestClassifier (MVP) → LightGBM (upgrade)	Labelled flows — benign + known attack families	"Which named attack is this?"	supervised_model.pkl
Model B	Anomaly detector	PyTorch autoencoder	Benign traffic only — no attack labels at all	"How unlike normal traffic is this?"	autoencoder.pt
Model A is the assignment's own suggested tool — a scikit-learn classifier. Get it trained and scoring on the validation day before touching the dashboard, backend routes, or anything else. That is your first real milestone: a working, honestly-evaluated model, achievable in well under a day. LightGBM is a same-day upgrade once that baseline runs end to end, not a prerequisite to start.

Model B is what actually answers the challenge's title — catch the attacks the signatures miss. It is not a stretch goal bolted on for style points; it is Phase 3, and the leave-one-attack-out table in Part 7 is the evaluation that proves it does what it claims.

Neither model is optional and neither is a pretrained download or an API call to somebody else's weights. If the final submission doesn't contain both a .pkl and a .pt file produced by your own training scripts on your own machine, the "ML" in "Network Intrusion Detector using ML" hasn't happened yet — everything else in this document is scaffolding around that fact, not a substitute for it.

Non-negotiable constraints
These are not preferences. Violating any of them means the build has failed its brief.

No auto-block, anywhere. No endpoint, button, or config flag that drops traffic. Containment actions, if present at all, are manual with a confirmation dialog. The README must justify this with alert-volume arithmetic.
Temporal splits only. Never train_test_split(shuffle=True) on this data. Flow records are heavily duplicated and correlated; random splitting leaks near-identical rows across train and test and manufactures fake 99.9% scores.
The anomaly model never sees attack labels. It trains on benign traffic exclusively. This is what makes novel-attack detection a real claim instead of a relabelled supervised model.
Leave-one-attack-out evaluation is mandatory. Hold out entire attack families from training and measure recall on them. This is the project's headline result.
Report PR-AUC, per-class recall, false-positive rate, and alerts/analyst/hour. Accuracy may appear in a table but must never be a headline number. On 99% benign traffic a model that predicts "benign" always scores 99%.
Train and serve share one feature module. features.py is imported by both. Never reimplement transforms in the API.
No training inside a request handler. Training is offline batch. The API loads artifacts at startup.
Every alert is explainable. An alert with a score and no reason is an alert an analyst ignores.
PART 2 — Architecture
                 ┌─────────────────────────────────────┐
  pcap / CSV ──▶ │  Feature extraction (features.py)   │
                 └──────────────┬──────────────────────┘
                                │
                 ┌──────────────▼──────────────────────┐
                 │  STAGE 1 — Supervised classifier    │
                 │  RandomForest → LightGBM, multi-class│
                 │  benign + known attack families     │
                 └──────────────┬──────────────────────┘
                                │
              confident attack ─┤─ confident benign ──▶ drop
                                │
                        low confidence
                                │
                 ┌──────────────▼──────────────────────┐
                 │  STAGE 2 — Anomaly detector         │
                 │  Autoencoder, benign-only training  │
                 │  reconstruction error > threshold   │
                 └──────────────┬──────────────────────┘
                                │
                 ┌──────────────▼──────────────────────┐
                 │  Alert pipeline                     │
                 │  explain → map + recommend → dedupe │
                 │  → enrich → persist → SSE push      │
                 └──────────────┬──────────────────────┘
                                │
                 ┌──────────────▼──────────────────────┐
                 │  React triage dashboard             │
                 │  analyst verdict ──▶ active learning│
                 └─────────────────────────────────────┘
Stage 1 names what it knows. Stage 2 catches what nobody named. The fusion layer is the entire point of the architecture — do not collapse it into one model.

Repository layout
ids-project/
├── BUILD_PROMPT.md
├── README.md
├── docker-compose.yml
├── Makefile
├── data/
│   ├── raw/                  # gitignored — downloaded CSVs
│   ├── interim/              # cleaned parquet
│   └── processed/            # train/val/test splits
├── backend/
│   ├── pyproject.toml
│   ├── training/
│   │   ├── features.py       # ← SHARED WITH SERVING
│   │   ├── clean.py
│   │   ├── split.py
│   │   ├── train_supervised.py   # RandomForest MVP → LightGBM upgrade
│   │   ├── train_autoencoder.py
│   │   ├── evaluate.py
│   │   └── loao.py           # leave-one-attack-out
│   ├── artifacts/            # models + scaler + feature_order + thresholds
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── models.py         # SQLAlchemy
│   │   ├── schemas.py        # Pydantic
│   │   ├── inference.py
│   │   ├── explain.py
│   │   ├── mitre.py
│   │   ├── remediation.py    # static family → response playbook
│   │   ├── dedupe.py
│   │   ├── drift.py
│   │   ├── replay.py
│   │   ├── live_capture.py   # Phase 9 — real traffic ingestion
│   │   └── routes/
│   │       ├── alerts.py
│   │       ├── score.py
│   │       ├── metrics.py
│   │       ├── analytics.py
│   │       ├── replay.py
│   │       └── stream.py
│   └── tests/
└── frontend/
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── api/              # typed client + TanStack Query hooks
        ├── components/
        ├── pages/
        └── types/
PART 3 — Phase 0: Scaffolding
Goal: a repo that runs end to end with fake data before any ML exists.

Build:

Python 3.11+, uv or Poetry. FastAPI, SQLAlchemy, pandas, numpy, scikit-learn, lightgbm, torch, shap, pydantic-settings.
Vite + React 18 + TypeScript. TanStack Query, TanStack Table, Tailwind, shadcn/ui, Recharts, lucide-react.
SQLite via SQLAlchemy with Alembic migrations. Postgres-compatible types only — no SQLite-specific columns — so swapping later is a config change.
GET /api/v1/health returning {status, model_version, uptime_s}.
A React page that calls /health through TanStack Query and renders the result.
docker-compose.yml with backend and frontend services. make dev brings both up.
.env.example and pydantic-settings config. No hardcoded paths or ports.
Checkpoint: make dev, open the browser, see live health data fetched from FastAPI. Stop and report.

PART 4 — Phase 1: Data and features
Dataset
Use CICIDS2017. It has real labelled attack traffic with a usable day structure:

Day	Content	Role
Monday	Benign only	Autoencoder training
Tuesday	FTP-Patator, SSH-Patator	Train
Wednesday	DoS Hulk/GoldenEye/Slowloris/Slowhttptest, Heartbleed	Train
Thursday	Web attacks (AM), Infiltration (PM)	Validation
Friday	Botnet, Port Scan, DDoS	Test
Monday being benign-only is a gift — it is a clean autoencoder training set with zero label contamination.

Note in the README that the original CICIDS2017 labels contain documented errors and that corrected re-releases exist. Acknowledging this signals you read past the abstract. Avoid NSL-KDD as a primary dataset; it derives from 1999 traffic and reviewers know it.

Cleaning — CICIDS2017 has specific, known defects
Handle every one of these explicitly in clean.py:

Column names have leading/trailing whitespace. " Flow Duration" is not "Flow Duration". Strip and snake_case everything first.
Flow Bytes/s and Flow Packets/s contain Inf and NaN from zero-duration flows. Replace Inf → NaN, then decide: drop rows or impute. Document the choice.
Massive exact-duplicate rows. Drop them before splitting. Failing to do this is the single largest source of inflated scores in published work on this dataset.
Zero-variance columns (Bwd PSH Flags, Fwd URG Flags, and others are all-zero). Drop programmatically, log which ones.
Negative values in some duration and IAT columns. Clip at zero or drop; log the count.
Write cleaned output to Parquet, not CSV. Ten times faster to reload and it preserves dtypes.
Leakage control
Drop before training: Flow ID, Source IP, Destination IP, Source Port. Keep Timestamp only until splitting, then drop.

Destination Port needs a decision, not a default. It is genuinely predictive and also a memorisation trap — the model learns the lab's port assignments rather than attack behaviour. Do this: train twice, once with raw destination port and once with it bucketed into service groups (well-known / registered / ephemeral, plus one-hot for the top 20 ports). Report both. If raw port gives a large gain, that gain is suspect and you should say so.

Scaling
RobustScaler, not StandardScaler. Network flow features are extremely heavy-tailed — a handful of enormous flows will flatten every other value under standard scaling. Fit on train only, then persist:

# artifacts/preprocessing.pkl MUST contain:
{
  "scaler": fitted_scaler,
  "feature_order": [...],        # exact column order
  "dropped_columns": [...],
  "port_encoding": {...},
  "schema_hash": "sha256:...",   # fail fast on mismatch at serve time
}
Train/serve skew is the silent killer of this project. If the API feeds columns in a different order than training, scores become garbage without raising a single exception. The schema hash makes it loud instead.

Checkpoint: print row counts per split per class, confirm zero duplicate rows across splits, confirm no NaN/Inf survives. Report the table.

PART 5 — Phase 2: Supervised classifier
Start with RandomForestClassifier from scikit-learn. It's the assignment's own suggested tool, it trains in minutes on CPU with no GPU setup, and it gives you a working, honestly-evaluated model on day one — n_estimators=300, tune max_depth against the validation day, multi-class. Commit this as a running baseline before touching anything else.
Upgrade to LightGBM only after the RandomForest baseline runs end to end and is evaluated. Faster than XGBoost on wide tabular data, handles categoricals natively, and will generally beat RandomForest here — but it's a swap-in improvement with the baseline kept as a fallback artifact, not the starting point.
class_weight='balanced' or per-class weights. Do not SMOTE. Synthetic interpolation between flow records produces packets that could not exist on a real network, and applying it before the split inflates test scores outright. If you must demonstrate SMOTE, do it as an ablation and show it underperforms.
Early stopping on the validation day.
Classes: benign, dos, ddos, brute_force, port_scan, web_attack, botnet, infiltration. Collapse rare sub-families; log the mapping.
Threshold selection — do this properly
Do not use argmax. Choose the operating threshold by false-positive budget:

Given expected daily flow volume V and analyst capacity C alerts/hour:
  max_alerts_per_day = C * 8
  target_FPR = max_alerts_per_day / V
  tau_sup = smallest threshold where FPR(tau) <= target_FPR
State V and C explicitly in the README. This one calculation reframes your threshold from an arbitrary 0.5 into an engineering decision, and it is the thing reviewers remember.

Persist tau_sup in the artifact bundle.

Metrics to emit
Per-class precision, recall, F1, support. Confusion matrix. PR curve and ROC curve, rendered side by side — you will point at the gap between them to explain why ROC-AUC flatters imbalanced classifiers. PR-AUC as the headline. FPR at the chosen threshold. Projected alerts/analyst/hour.

Checkpoint: classification report on the held-out test day, plus a one-paragraph written interpretation of which classes the model handles poorly and why.

PART 6 — Phase 3: Anomaly detector
Train on benign rows only. Monday in full, plus benign rows from Tuesday and Wednesday. Attack rows must never enter this training set — assert it in code, do not just intend it.

Architecture
input(d) → 64 → 32 → 16 → 32 → 64 → output(d)
ReLU, MSE loss, Adam, early stopping on benign validation loss
Dropout 0.1 in the encoder; batch norm helps convergence here
Score = per-row mean squared reconstruction error.

Threshold
tau_anom = 99.5th percentile of reconstruction error on held-out benign validation data. Persist the full benign error distribution (histogram bins, not raw rows) — the dashboard's interactive threshold slider needs it, and so does drift detection.

Baselines
Run IsolationForest, LOF, and ECOD from PyOD on the same split. You need them for two reasons: they prove the autoencoder earns its complexity, and if one of them beats it, that is a finding worth reporting rather than hiding.

Explanation — do not use SHAP here
KernelSHAP on a neural net is slow and you do not need it. The autoencoder gives you explanation for free: per-feature reconstruction error. The features the model failed hardest to reconstruct are precisely why the row looks anomalous.

per_feature_error = (x - x_hat) ** 2          # already computed
top_contributors = argsort(per_feature_error)[-5:]
This is faster, more faithful to the model, and more interpretable than an approximated attribution. Use TreeSHAP for the supervised stage, reconstruction error for the anomaly stage.

Checkpoint: histogram of benign vs attack reconstruction error with the threshold line drawn. The distributions should visibly separate. If they do not, the model is not working and no dashboard will hide that.

PART 7 — Phase 4: Fusion and the headline evaluation
Fusion logic
def classify(x):
    p = supervised.predict_proba(x)
    attack_conf = p[attack_classes].max()

    if attack_conf >= tau_sup:
        return Alert(kind="KNOWN", family=argmax_class(p), conf=attack_conf)

    anom = autoencoder.score(x)
    if anom >= tau_anom:
        return Alert(kind="UNCLASSIFIED_ANOMALY", family=None, score=anom)

    return None
UNCLASSIFIED_ANOMALY is the entire thesis of the project rendered as an enum value. It must survive to the UI as a visually distinct badge.

Leave-one-attack-out — the result that wins the challenge
For each attack family F:

Remove all F rows from supervised training.
Retrain the supervised model.
The autoencoder is unchanged — it never saw any attacks anyway.
Run the full fusion pipeline on a test set containing F.
Record: what fraction of F was flagged, and by which stage.
Output this table:

Held-out family	Caught by Stage 1	Caught by Stage 2	Total recall	Missed
Infiltration	4%	74%	78%	22%
Botnet	...	...	...	...
Port Scan	...	...	...	...
Web Attack	...	...	...	...
DDoS	...	...	...	...
The Stage 2 column is your headline number. "The system had never seen infiltration traffic and surfaced 74% of it." That is a measured claim about catching what signatures miss, and it is worth more than any accuracy figure you could print.

Report the misses honestly. A table with a 22% miss column reads as credible engineering; a table of 99s reads as a bug.

Checkpoint: the completed LOAO table, committed as reports/loao.md.

PART 8 — Phase 5: Backend API
Endpoints
GET    /api/v1/health
POST   /api/v1/score                     batch of flows → scored results
GET    /api/v1/alerts                    filter, sort, cursor-paginate
GET    /api/v1/alerts/{id}               detail + explanation + why/how narrative + remediation + raw flow
POST   /api/v1/alerts/{id}/verdict       {TP | FP | UNSURE, note}
GET    /api/v1/alerts/{id}/related       same source host, 24h window
GET    /api/v1/stream                    SSE live feed
GET    /api/v1/metrics/model             per-class metrics, curves, LOAO
GET    /api/v1/metrics/threshold?t=0.87  recompute alert volume at threshold
GET    /api/v1/metrics/drift             PSI per feature over time
GET    /api/v1/analytics/summary?range=  alerts-over-time, family mix, top hosts/sources, SOC throughput
GET    /api/v1/analytics/mitre-coverage  technique counts, for the coverage heatmap
POST   /api/v1/replay/start              {speed: 1|10|100, dataset}
POST   /api/v1/replay/stop
POST   /api/v1/ingest/start              begin scoring a live capture (Phase 9)
GET    /api/v1/models                    registry, champion/challenger
Scoring
Batch always. Accept List[FlowRecord], score as a single matrix. Per-row predict() calls in the replay loop will be roughly 50x slower and will make the live demo stutter.

Load models once at startup into app state via the lifespan context manager. Verify schema_hash against the artifact bundle and refuse to start on mismatch.

Replay engine
You will not have live enterprise traffic. Build replay.py as an asyncio background task that streams held-out test rows at accelerated time (1x / 10x / 100x), scoring in batches and pushing alerts over SSE.

This is honest — real flows, real ground-truth labels — so the dashboard can show predictions against truth. It also makes the demo visibly alive, which matters more than you would think in a five-minute presentation.

Optionally accept a pcap upload, run CICFlowMeter over it, and score the result.

Alert pipeline
Each alert passes through, in order:

Explain — TreeSHAP top-5 (Stage 1) or top-5 reconstruction-error features (Stage 2).

Narrate — template the explanation into English: "2,400 distinct destination ports contacted in 8 seconds from a single source." Build a per-feature phrase template map.

MITRE map and recommend — attach a technique ID, a one-line plain-English description of what that technique means, and a recommended response. This table is the backbone of the "why / how / how to fix" story the Alert Detail screen tells:

Family	Technique	What it usually means	Recommended response
Brute force (FTP/SSH/web)	T1110	Repeated login attempts against one service, guessing credentials	Lock or rotate the targeted account, enforce MFA, rate-limit or geo-fence the service
DoS / DDoS	T1498, T1499	Traffic volume aimed at exhausting a service's capacity	Enable upstream rate-limiting or scrubbing, fail over the target, block the source range at the edge
Port scan	T1046	A host enumerating open ports on another host, usually reconnaissance	Review firewall rules on the scanned host, confirm no unintended exposure, watch the source for follow-up activity
SQL injection / web attack	T1190	Malformed input aimed at a public-facing application	Patch or WAF-rule the endpoint, rotate credentials the app uses, review recent writes to the database
Botnet C2	T1071	A host checking in with an external command-and-control server	Isolate the host, image it before wiping, rotate any credentials that lived on it
Infiltration	T1204	A host behaving as if it's been used as an entry point	Isolate the host, check for lateral movement from it, review what it could reach
Unclassified anomaly	(none)	Doesn't match a known technique — that's the entire point of Stage 2	No playbook exists yet. Say so explicitly rather than guessing, and route it for manual investigation
This is a static, reviewed lookup (app/remediation.py), not a generative one — resist the urge to have a model freestyle remediation advice per alert. A fixed playbook is something a SOC can trust; an improvised one per alert is something they have to re-verify every time, which defeats the purpose of having it.

Dedupe — key on (src_host, alert_class, floor(ts, 5min)). On hit, increment count and update last_seen rather than inserting. One compromised host producing 5,000 flows is one incident. Without this your queue is unusable within thirty seconds of starting the replay.

Enrich — asset criticality (a static lookup JSON is fine), prior alert count for the host.

Persist and push over SSE.

Why SSE and not WebSockets
The feed is one-directional. EventSource is built into the browser, FastAPI does SSE in about ten lines, and there is no reconnect logic to write. WebSockets buy you nothing here.

Checkpoint: start a replay at 10x, watch alerts arrive over curl -N localhost:8000/api/v1/stream, confirm dedup is collapsing bursts.

PART 9 — Phase 6: Frontend
Seven screens. The ordering of these is a design argument, not a list.

1. Triage Queue — this is the landing page
Not an overview dashboard. Analysts live in the queue, so the queue is home.

TanStack Table, sorted by risk score, not timestamp.
Columns: time, source → destination, severity, verdict, confidence, asset criticality, status, count (from dedup).
Filters: severity, class, time window, verdict status, UNCLASSIFIED_ANOMALY only.
Bulk select → dismiss.
UNCLASSIFIED ANOMALY rows render visually distinct — different badge colour, a marker icon. When someone asks "what does this catch that Snort doesn't," you point at those rows. Make them findable in one click via a filter chip.
Thin stat strip above: alerts last hour, alerts/analyst/hour, current threshold, hosts affected.
Deliberately absent: a large accuracy percentage. A hero tile reading "99.8% ACCURATE" is exactly the failure mode this challenge is testing for. If you want a hero number, use alerts/analyst/hour or Stage-2 novel-attack recall.

2. Alert Detail — the screen that makes it feel real
Side drawer, so queue position is never lost. Structure it around three questions an analyst actually asks, in this order, because the order is the point:

Why was this flagged. Raw flow record, monospace. SHAP waterfall (Recharts horizontal bar, signed) or reconstruction-error bars for anomalies. The generated English sentence — "2,400 distinct destination ports contacted in 8 seconds from a single source" — sits above the chart, not buried under it.

What this likely is. The MITRE technique plus its one-line plain-English description from the remediation table in Part 8 — not just a code linking out to attack.mitre.org, but a sentence an analyst can read without opening a second tab. For UNCLASSIFIED_ANOMALY this panel says so honestly: "Doesn't match a known technique — this is exactly what Stage 2 exists to catch." Host context underneath: other alerts from this source in the last 24h, so a scan-then-exploit sequence reads as one story instead of three disconnected rows.

How to fix it. The recommended-response playbook for that family, rendered as a short checklist an analyst can scan in three seconds mid-shift, not a paragraph. Unclassified anomalies get the honest version instead: "No playbook yet — escalate for manual investigation." Never invent a fix for something the system doesn't actually recognize; a wrong playbook does more damage than an honest shrug.

Then: ground-truth label shown only in replay mode, clearly badged as demo-only, and a footer of True Positive / False Positive / Need more info buttons writing to analyst_verdicts.

No block button. If you include containment at all, make it manual, confirmed, and audited.

3. Live Traffic Monitor
SSE ticker of flows scrolling past.
Flows/sec and alerts/sec sparklines.
Replay speed control.
The best single element in the whole app: a histogram of anomaly scores with the threshold drawn as a draggable vertical line. As the analyst drags it, projected alerts/hour updates live off /metrics/threshold. That one interaction communicates the precision/recall tradeoff better than any table you could build. Debounce the request at ~150ms.
4. Model Performance
Per-class precision/recall/F1 table.
Confusion matrix heatmap.
PR curve and ROC curve side by side, deliberately — with a caption explaining that the gap between them is why ROC-AUC misleads on imbalanced data.
The LOAO panel. Give this real visual weight. It is the strongest evidence in the project.
5. Drift Monitor
PSI per feature over time, with warning bands at 0.1 (moderate) and 0.25 (significant).

PSI = Σ (actual% − expected%) × ln(actual% / expected%)
Training benign score distribution overlaid on the last 24 hours. When those curves separate, the baseline has moved.

"Retrain recommended" banner when PSI crosses 0.25.

Model registry: versions, trained-on date, champion/challenger.

6. Feedback Loop
Small screen, large narrative payoff. Count of new analyst labels since last retrain, TP/FP breakdown, disagreement rate between model and analyst, and a "Retrain with N new labels" button. This closes the loop from analyst back to model and is the clearest signal that you designed a system rather than a script.

7. Analytics — the screen for someone who isn't triaging alerts
Everything on screens 1–6 is built for an analyst working a queue. This one is for whoever asks "so what happened this week" — a team lead, a judge, you during the demo. It's a second screen reached from a nav link, not a rebuild of the queue as a dashboard: screen 1 stays the queue on purpose, for the reason already given above.

Alerts over time, stacked by known family vs. UNCLASSIFIED_ANOMALY, with a range picker (24h / 7d / 30d / all). Watch the unclassified line specifically — that trend is your novel-detection headline, and it's the line a judge will ask about if it spikes.
Attack-family breakdown for the selected range: a bar or donut of what's actually been seen, not a leaderboard of severity labels you invented.
Top targeted hosts and ports, top source IPs/subnets — two small ranked tables. This is the first place anyone looks for "who's actually under attack."
SOC throughput: alerts opened vs. resolved, average time-to-verdict, TP/FP rate over time. This is the metric that argues for the project's existence — it's the number that should trend down as the feedback loop (screen 6) does its job.
MITRE coverage heatmap: a small grid of which techniques have fired and how often. Cheap to build off the table Part 8 already defines, and it's the kind of touch that reads as "enterprise" because real SIEMs show exactly this.
Same rule as screen 1: no accuracy hero tile here either. If this screen needs one big number, make it alerts-per-analyst-hour or the unclassified-anomaly rate, not a vanity percentage.

Frontend rules
TanStack Query for all server state. No useEffect fetch chains. Invalidate alerts on verdict submission so the queue updates itself.
Generate TypeScript types from the FastAPI OpenAPI schema. Do not hand-write them and let them drift.
Virtualise the alert table. It will hold tens of thousands of rows during a 100x replay.
Skeleton loaders, empty states, and error boundaries on every screen. An empty triage queue should say "No open alerts" — not render a blank page that looks broken.
Checkpoint: full walkthrough — start replay, watch alerts arrive, open one, read why/what/how-to-fix, submit a verdict, see it reflected in the feedback panel and counted on the analytics screen.

PART 10 — Phase 7: Drift and active learning
Nightly job computing PSI per feature against the training reference distribution. Store snapshots.
Autoencoder benign-baseline re-fit on recent confirmed-benign traffic. Guard against poisoning: require analyst FP confirmation before a row enters the benign refit pool, and cap the fraction of any single source host.
Retraining pipeline consuming analyst_verdicts, producing a challenger model, evaluating champion vs challenger on the same held-out set, and promoting only on improvement. Log the comparison.
Full audit trail: which model version scored which alert. Non-negotiable for anything security-adjacent.
PART 11 — Phase 8: Packaging
docker-compose up brings up the entire stack with models pre-loaded.
make seed populates a demo database so the dashboard is never empty on first open.
Tests: feature-parity test asserting features.py produces identical output in training and serving paths; schema-hash mismatch test; dedup test; API contract tests.
README containing: architecture diagram, the LOAO table, the PR-vs-ROC explanation, the false-positive-budget arithmetic, the alert-not-block justification, known limitations, and honest next steps.
Limitations to state plainly in the README
Stating these makes the work more credible, not less:

Flow-level features cannot see encrypted payload content.
CICIDS2017 is synthesised lab traffic; a real enterprise baseline is messier and drifts faster.
The autoencoder flags unusual, which is not synonymous with malicious — a new backup job will fire alerts.
An adaptive adversary can shape traffic to stay under the threshold.
LOAO measures generalisation to held-out known attacks, which is a proxy for genuinely novel ones, not proof.
PART 12 — Anti-patterns
The agent must not do any of these. If it already has, fix it before advancing.

Anti-pattern	Why it fails
train_test_split(shuffle=True)	Leaks duplicated flows; fabricates 99.9% scores
SMOTE before splitting	Synthetic rows appear in test; scores meaningless
Accuracy as headline metric	99% benign makes it uninformative
ROC-AUC alone	Flatters imbalanced classifiers; PR-AUC is the honest view
.fit() in an endpoint	Not a serving architecture
Duplicated feature logic	Train/serve skew, silent and fatal
Per-row predict() in the replay loop	~50x slowdown, stuttering demo
No dedup	5,000 alerts from one host; queue unusable
Score with no explanation	Analysts ignore unexplained alerts
Auto-block button	Directly violates the brief
Unpersisted threshold / feature order	Backend and frontend silently disagree
Mock data in the final build	Every number must trace to a real model run
Trusting the CICIDS2017 threshold on live traffic	Domain shift; false-positive rate spikes, not a bug
Capturing or attack-testing a network you don't own	Illegal regardless of intent — lab-only, always
Generative/freeform remediation text per alert	Unreviewable advice a SOC can't trust; use the static playbook
PART 13 — Acceptance checklist
Phase 8 is not complete until every line is true.

Data

 Temporal split; zero duplicate rows shared across splits
 IP/port/timestamp leakage columns dropped and logged
 Destination-port ablation run and reported
 Scaler, feature order, and schema hash persisted together
Models

 RandomForest baseline trained, evaluated, and committed before attempting the LightGBM upgrade
 Autoencoder training set asserted attack-free in code
 tau_sup derived from a stated false-positive budget, not 0.5
 tau_anom set from benign validation percentile
 PyOD baselines run and compared
 LOAO table complete, including a Missed column
Backend

 Batch scoring; models loaded once at startup
 Schema-hash check fails fast on mismatch
 Explanation attached to every alert
 Dedup verified under burst load
 SSE stream stable through a 100x replay
 Zero auto-block code paths
Frontend

 Triage queue is the landing page, sorted by risk
 UNCLASSIFIED_ANOMALY visually distinct and filterable in one click
 Draggable threshold updating projected alert volume live
 PR and ROC rendered side by side with explanatory caption
 LOAO panel present and prominent
 Verdict submission invalidates and refreshes the queue
 No accuracy hero tile anywhere
 Alert Detail answers why / what-it-is / how-to-fix for every alert, including the honest "no playbook" case for unclassified anomalies
 Analytics screen shows trends by family, top hosts/sources, SOC throughput, and MITRE coverage
Real-world testing (Phase 9, optional but strongly recommended)

 All capture and attack-testing scoped to networks and hosts you own or are explicitly authorized to test
 Shadow-mode burn-in run and a local tau_anom computed before any live alert reaches the queue
 Both thresholds (dataset-derived and local) documented, with the gap between them explained
 At least one self-run attack per testable family caught and correctly explained end to end
Docs

 README carries LOAO results, FP arithmetic, alert-not-block justification, limitations
 docker-compose up works from a clean clone
PART 14 — Demo narrative
Build toward this five-minute run. If a screen does not serve it, that screen is decoration.

Frame the gap. "Signature IDS matches known patterns. Here is traffic it has no signature for."
Start the replay at 10x. Alerts populate the queue live.
Open a known attack. Named family, MITRE technique, SHAP explanation, English sentence. "The analyst knows in four seconds what this is."
Filter to UNCLASSIFIED ANOMALY. "These have no signature and no family label. Stage 2 found them because they do not look like normal traffic."
Open one. Reveal ground truth. It is infiltration. Stage 1 never saw infiltration in training.
Cut to the LOAO table. "That is not luck. Here it is measured across every attack family, with the misses shown."
Drag the threshold. "Here is the tradeoff the SOC lead actually controls. At this setting, 40 alerts an hour. At that one, 400."
Submit a verdict. Show the feedback panel. "Analyst judgement becomes next week's training data."
Close on the constraint. "Nothing here blocks traffic. At a million flows a day, a 0.1% false-positive rate is a thousand false alerts — auto-blocking takes production down. We alert, we rank, and we explain."
PART 15 — From replay to a real network
Phases 0–8 run entirely on CICIDS2017. This part is what changes once the target is traffic you're actually generating — your own laptop, your own router, your own lab. Treat it as the phase that makes "we tested this on a real thing" true rather than asserted, the same way Part 7's LOAO table makes "catches novel attacks" true rather than asserted.

Two traffic sources, one pipeline
Keep replay mode exactly as built — it's still how you demo and how you get repeatable, labelled numbers for the README. Add a second source alongside it, not instead of it: a live-capture path that feeds the exact same features.py, the exact same inference.py, the exact same alert pipeline. If live traffic needs its own scoring code, you've already broken the train/serve-skew defence Part 4 built.

Capturing traffic — only where you're allowed to
Only run capture against a network, device, or lab you own or have explicit permission to monitor. That's not a suggestion — packet capture on a network you don't control or lack authorization for is illegal in most places regardless of intent, the same way port-scanning someone else's host is. It also isn't a limitation in practice: everything below works fine on a single home network or a laptop.

Three ways to actually get traffic, in order of how much you'd realistically use each:

An isolated lab you build yourself — two to four VMs on one virtual switch (VirtualBox, VMware, or a couple of cloud instances on a private VPC). Build this one first: full control, fully reproducible, and it doubles as the place you run your own test attacks (below). A single host acting as gateway for the others gives you a clean capture point.
Your own router, if it supports port mirroring / a SPAN port — captures real household traffic without touching anyone else's.
Your own machine's interface — the simplest option, capturing only what your own laptop sends and receives. Limited traffic diversity, but zero setup and completely unambiguous about whose traffic it is.
Tooling: Zeek or Suricata turn a live interface into flow-level logs directly. Or capture raw with tcpdump, then run CICFlowMeter over the pcap to get the same flow features training used — same output shape either way, so features.py doesn't care which path produced it.

The problem you will actually hit: domain shift
CICIDS2017 is lab-generated 2017 traffic. Real traffic on your own network today looks nothing like it — different applications, mostly TLS-encrypted, a completely different "normal" baseline. Point a model trained purely on CICIDS2017 at live traffic with no adjustment and expect a false-positive rate well above anything your validation numbers promised. Nearly everything will look anomalous relative to a five-year-old baseline it's never seen the shape of. This is expected. Say so in the README instead of being surprised by it in the demo.

The fix is a shadow-mode burn-in: before any live alert reaches the queue, run the pipeline in log-only mode — score everything, alert no one — against your own real traffic for a stretch (a few hours covers a demo; a few days is more honest). Collect the reconstruction-error distribution on that local traffic and recompute tau_anom from your own percentile, not the CICIDS2017-derived one. Report both thresholds and the gap between them in the README — that gap is a real, interesting result in its own right, not something to hide.

Model A faces the same shift from the other direction: it can only name families it saw at training time, in a feature space shaped by 2017 traffic, so expect it to under-fire on live traffic and expect Stage 2 to carry more of the weight than it did on the dataset. That's not a failure — it's the project's actual thesis showing up in a place you didn't script it.

Proving detection works, on traffic CICIDS2017 never shaped
A quiet local network won't generate attack traffic on its own, so make some — against machines you own, inside the lab from step 1 above:

nmap a port scan against your own lab VM
hydra a brute-force attempt against a throwaway SSH or FTP service you stood up yourself, with a password you don't use anywhere else
hping3 or a slowloris-style tool, at a rate that only stresses your own test endpoint
Same rule as capture: only ever against hosts you own and control. This is standard practice for testing your own defenses — it's what turns into unauthorized access the moment it's pointed at anything you don't have permission to test.

Because you triggered it yourself, you know the exact ground truth — which gives you a second, independent version of the leave-one-attack-out test in Part 7: does detection hold up on traffic shaped nothing like CICIDS2017, on attacks the model has never seen the network's version of, not just the dataset's version of.

What changes in the README
Add a short, specific section: what recalibration you actually did, the local threshold you landed on versus the dataset-derived one, and what happened when your own attack traffic hit the pipeline. "Trained on CICIDS2017, pointed it at our own network, watched the false-positive rate spike, recalibrated in shadow mode, then caught our own test attacks" is a stronger, more credible story than a clean number that never touched a real network.

This phase is what turns the project from a well-evaluated notebook into something that's actually been run against the world once. It's also genuinely optional if you're time-constrained — see the closing note below.

Checkpoint: shadow-mode burn-in completed with both thresholds documented, at least one self-run attack from each family you can safely simulate caught and correctly explained end to end in the dashboard.

Execution order
Phase 0  Scaffolding          → health check end to end
Phase 1  Data + features      → clean splits, persisted preprocessing
Phase 2  Supervised model     → classification report, threshold from FP budget
Phase 3  Anomaly model        → separated error distributions
Phase 4  Fusion + LOAO        → the headline table
Phase 5  Backend API          → live SSE replay
Phase 6  Frontend             → seven screens
Phase 7  Drift + feedback     → closed loop
Phase 8  Packaging            → one-command demo
Phase 9  Real traffic         → shadow-mode threshold, self-tested detection
One phase per agent session. Stop at each checkpoint, verify the acceptance criteria, then advance.

If you are time-constrained, build Phases 0–5 plus the Triage Queue and Alert Detail screens. That delivers the full narrative at roughly half the work. Phases 7 and 8 are polish — valuable, but they do not carry the argument. Phase 9 is the strongest possible addition if you have any time left over — nothing else on this list turns "we built a detector" into "we pointed it at something real and it worked" — but build it last, after the core story from Phases 0–6 is solid and demoable on its own.