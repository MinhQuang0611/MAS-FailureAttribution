# Failure-Attribution Experiments

Measuring whether stage-level failure attribution in multi-agent Text-to-SQL
pipelines reports the stage that actually failed — or merely the stage where
the symptom became visible.

## Run it

```bash
git clone https://github.com/MinhQuang0611/MAS-FailureAttribution.git
cd MAS-FailureAttribution

printf 'OPENAI_API_KEY=sk-...\n' > .env     # gitignored; never commit it

bash run.sh                                 # setup + run + reports, one shot
```

`run.sh` is the entrypoint: edit its CONFIG block, or override from the
environment. Smoke-test first on a new machine:

```bash
LIMIT=2 bash run.sh                         # ~1 min, a couple of dollars
```

Unattended:

```bash
nohup bash run.sh > run.out 2>&1 &
tail -f run.out
```

`experiments/setup_server.sh` and `experiments/run_experiments.sh` are the
stages `run.sh` calls; run them directly only if you want one without the other
(`SKIP_SETUP=1` and `ANALYSIS_ONLY=1` cover the usual cases).

Everything resumes. Instances already logged are skipped, so an interrupted run
continues where it stopped and re-spends nothing. Kill and restart freely.

## What the experiment asks

The original pipeline attributes 27% of outcomes to the skeleton stage and 0%
to intent. Scored against 398 perturbation instances whose faulty stage is
fixed by construction, that attribution has **0% stage recall** — injected
faults land on Skeleton ~60% and Execution ~39% of the time regardless of where
they were actually injected.

The cause is mechanical rather than statistical. In `pipeline/orchestrator.py`,
`x7.uses_join` and `x8.expected_clauses` are regex extractions of the final
generated SQL, so the classifier's test *"clauses in x8 absent from x10"* is
empty by construction and can never fire. Eleven of sixteen root-cause labels
are unreachable; `exec_wrong_result` is the `else` branch.

That motivates the comparison this repo runs: three pipelines that differ in
how real their per-stage artifacts are.

| System | Stages | Artifacts |
|---|---|---|
| `nlsql` | 4 nominal | re-derived from the final SQL — **not independent** |
| MAC-SQL | 3 agents | schema real (pre-SQL); intent + generation merged |
| MAG-SQL | 4 agents | schema, decomposition, generation, repair all separate |

If recall rises along that ordering, attribution bias is a property of
instrumentation quality, not an inherent limit of state-differencing.

## Datasets

| Dataset | Instances | Stage ground truth | What it supports |
|---|---|---|---|
| `drspider` | 1,700 (17 x 100) | by construction | recall — the bias measurement |
| `bird` | 499 sampled of 1,534 | none | attributed distribution; activates `x2` |

The split is load-bearing. Recall asks whether attribution names the stage where
the fault was actually injected, and only a perturbed corpus can answer it. BIRD
has no injected faults, so its records carry `target_error_type: null`, are
absent from the recall report by construction, and appear only in
`reports/distribution_*.txt`. Reporting a distribution while implying recall is
the error this study documents; the two reports are kept apart so the codebase
cannot make it.

BIRD also fills the `x2` external-knowledge slot, dormant under Spider: its
`evidence` field is the domain hint that slot was designed for.

```bash
DATASETS="drspider bird" bash run.sh
```

## Benchmark

Dr.Spider, 17 perturbation families, 100 sampled per family (seed 42), grouped
by the stage each family stresses:

| Group | Sets | n | Ground truth |
|---|---|---|---|
| `NLQ_*` | 9 | 900 | **Intent** — question reworded, gold SQL preserved |
| `SQL_*` | 5 | 500 | **Skeleton** — required query structure changed |
| `DB_*` | 3 | 300 | **Schema** — schema renamed / abbreviated / content-shifted |

The group→stage mapping is **our design assumption**, not a label supplied by
the Dr.Spider authors. It certifies where a fault was *injected*, not that the
pipeline failed there. Report it as such.

The sampled JSONs are committed so every machine scores identical instances.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SYSTEMS` | `macsql,magsql` | comma list: `macsql,magsql` |
| `DATASETS` | `drspider` | space-separated: `drspider bird` |
| `PERT_GROUPS` | `NLQ SQL DB` | which perturbation groups (Dr.Spider only) |
| `LIMIT` | – | cap instances per set (smoke test) |
| `MODELS` | `gpt-4o-mini` | space-separated backbones; every system runs against each |
| `PER_SET` | `100` | sample size, only when rebuilding the benchmark |
| `SKIP_SETUP` | `0` | `1` skips setup (already provisioned) |
| `ANALYSIS_ONLY` | `0` | `1` regenerates reports from existing logs, no API calls |
| `PYTHON` | `python3` | interpreter to use |
| `WITH_CHESS` | `0` | also clone CHESS (BIRD-only; see below) |

Keeping one backbone across systems within a sweep matters: differing backbones
would confound a comparison of pipeline architectures with a comparison of LLMs.
Across backbones is the second axis — whether the attribution bias survives a
change of model — so results are filed under `<system>/<backbone>/` and reported
as a system x backbone grid.

A model id may carry a provider prefix:

| Id | Routed to |
|---|---|
| `gpt-4o-mini` | OpenAI, using `OPENAI_API_KEY` |
| `openai/gpt-4o-mini` | the same, written explicitly |
| `ollama/qwen3:32b` | a local ollama server on `:11434` (override with `OLLAMA_BASE`) |

Both baselines build their OpenAI client from `OPENAI_API_BASE`, so any
OpenAI-compatible server works as an open-weight backbone.

```bash
MODELS="gpt-4o-mini gpt-4o-mini ollama/qwen3:32b" bash run.sh
```

## Cost and runtime

Measured at ~6 s and ~$0.014 per instance for MAC-SQL on gpt-4o-mini.

| Run | Instances | Approx time | Approx cost |
|---|---|---|---|
| MAC-SQL, all 17 sets | 1,700 | ~3 h | ~$25 |
| MAG-SQL, all 17 sets | 1,700 | longer — see below | higher |

**MAG-SQL carries a preprocessing cost.** Its `Soft_Schema_linker` makes one
LLM call per table per database before scoring begins, plus a value-matching
pass over the corpus. Cached to disk, paid once per set, but substantial on the
`DB_*` sets (40–48 databases each). Run `PERT_GROUPS="NLQ SQL"` first.

**DIN-SQL is now included** (`SYSTEMS=dinsql`), and is the reason this list
shrank. It runs on Spider, so Dr.Spider's stage labels apply, and its query
classifier is an explicit three-valued gate — EASY / NON-NESTED / NESTED — where
the decomposition artifact exists only on the NESTED branch. That makes its
stage-activity rate readable straight off the logs with no detector at all, on a
different stage of a different system from MAC-SQL's Selector. Two caveats are
recorded per instance rather than absorbed: its schema stage emits advisory
schema *links* rather than a pruned schema, and its 2023-era output parsers miss
against a modern chat backbone often enough that the miss rate is itself a
number worth reporting (`upstream_parse_ok` in every log entry). Costs 4 LLM
calls per instance on long few-shot prompts. Dr.Spider only: BIRD needs the
separate upstream `DIN-SQL_BIRD.py`, whose prompts carry BIRD's `evidence`
field.

**`nlsql` is no longer run.** The comparison now scores the two published
baselines only — MAC-SQL and MAG-SQL, both at pinned upstream commits. The
nlsql column above comes from its existing logs in `output_logs/raw_logs`,
which `evaluation/attribution_bias.py` reads without any API calls; nothing
re-runs the pipeline, so its Docker stack is not needed.

## Not included, and why

- **MARS-SQL** (77.84 BIRD / 89.75 Spider) ships Qwen-7B RL checkpoints
  requiring ~14 GB VRAM plus vLLM and ray. Its checkpoints are BIRD-trained, so
  Spider is off-distribution. Needs a rented GPU.
- **CHESS** has no Spider support — no file in the repo references it. Running
  it means downloading BIRD and forfeiting the Dr.Spider ground truth. Clone it
  with `WITH_CHESS=1` if you want to start that branch. It is still the next
  system worth adding: its repo ships two ready-made pipeline variants, with and
  without the Schema Selector, which is the interventional A/B this study
  currently lacks.
- **Arctic-Text2SQL-R1** is a single RL-trained model with no stages to
  attribute to.

## Layout

```
run.sh                         single entrypoint: config, preflight, setup, run
experiments/
  setup_server.sh              deps, corpora, pinned baselines + overrides
  run_experiments.sh           run systems, then analysis
scripts_drspider/
  build_drspider_benchmark.py  sample 17 families -> benchmark JSONs
  prepare_db_perturbation_dbs.py  stage DB_* databases under namespaced ids
  run_drspider.py              nlsql runner
baselines/
  macsql_instrumented.py       MAC-SQL + Logging Matrix
  magsql_instrumented.py       MAG-SQL + Logging Matrix
  overrides/                   OpenAI SDK >=1.0 transport, env-based config
evaluation/
  attribution_bias.py          confusion matrix on the 400-instance benchmark
  attribution_strategies.py    alternative orderings, re-scored on saved logs
  drspider_bias_report.py      per-set recall + confusion + ablation
  cross_system_report.py       recall vs artifact independence
```

## Reproducibility notes

- Baselines are pinned to exact commits; `baselines/overrides/` replaces their
  API-transport modules wholesale (a patch would break on any checkout that
  normalises line endings differently). Run `git -C baselines/<name> diff` after
  setup to see exactly what differs from upstream.
  The clones are gitignored; `setup_server.sh` reconstructs them.
- Only transport and configuration are replaced. Prompts, agent logic and
  control flow are upstream.
- `attribution_strategies.py` re-scores **saved logs**, so alternative
  attribution rules cost no API calls — attribution is a pure function of the
  recorded state.
- The `DB_*` families reuse db_id names across sets with **different database
  content** (verified by checksum), so their databases are namespaced
  (`battle_death_0__synonym`). Copying them under their original names would
  silently overwrite one set's schema with another's.
