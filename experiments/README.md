# Failure-Attribution Experiments

Measuring whether stage-level failure attribution in multi-agent Text-to-SQL
pipelines reports the stage that actually failed — or merely the stage where
the symptom became visible.

## Run it

```bash
git clone https://github.com/MinhQuang0611/experiment.git
cd experiment

printf 'OPENAI_API_KEY=sk-...\n' > .env     # gitignored; never commit it

bash experiments/setup_server.sh            # deps, corpora, baselines (~20 min)
bash experiments/run_experiments.sh         # MAC-SQL over all 17 sets
```

Smoke-test first if the machine is new:

```bash
LIMIT=3 bash experiments/run_experiments.sh
```

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
| `SYSTEMS` | `macsql` | comma list: `macsql,magsql,nlsql` |
| `GROUPS` | `NLQ SQL DB` | which perturbation groups |
| `LIMIT` | – | cap instances per set (smoke test) |
| `MODEL` | `gpt-4o` | backbone for every system |
| `PER_SET` | `100` | sample size, only when rebuilding the benchmark |
| `WITH_CHESS` | `0` | also clone CHESS (BIRD-only; see below) |

Keeping one `MODEL` across systems matters: differing backbones would confound
a comparison of pipeline architectures with a comparison of LLMs.

## Cost and runtime

Measured at ~6 s and ~$0.014 per instance for MAC-SQL on gpt-4o.

| Run | Instances | Approx time | Approx cost |
|---|---|---|---|
| MAC-SQL, all 17 sets | 1,700 | ~3 h | ~$25 |
| MAG-SQL, all 17 sets | 1,700 | longer — see below | higher |
| nlsql, all 17 sets | 1,700 | ~8 h | — |

**MAG-SQL carries a preprocessing cost.** Its `Soft_Schema_linker` makes one
LLM call per table per database before scoring begins, plus a value-matching
pass over the corpus. Cached to disk, paid once per set, but substantial on the
`DB_*` sets (40–48 databases each). Run `GROUPS="NLQ SQL"` first.

**`nlsql` needs its stack up** — `cd nlsql && docker compose up -d` (FastAPI,
Postgres, Qdrant, Redis, Mongo), API on `:8388`. The other two systems call the
OpenAI API directly and need no local services.

## Not included, and why

- **MARS-SQL** (77.84 BIRD / 89.75 Spider) ships Qwen-7B RL checkpoints
  requiring ~14 GB VRAM plus vLLM and ray. Its checkpoints are BIRD-trained, so
  Spider is off-distribution. Needs a rented GPU.
- **CHESS** has no Spider support — no file in the repo references it. Running
  it means downloading BIRD and forfeiting the Dr.Spider ground truth. Clone it
  with `WITH_CHESS=1` if you want to start that branch.
- **Arctic-Text2SQL-R1** is a single RL-trained model with no stages to
  attribute to.

## Layout

```
experiments/
  setup_server.sh              deps, corpora, pinned+patched baselines
  run_experiments.sh           run systems, then analysis
scripts_drspider/
  build_drspider_benchmark.py  sample 17 families -> benchmark JSONs
  prepare_db_perturbation_dbs.py  stage DB_* databases under namespaced ids
  run_drspider.py              nlsql runner
baselines/
  macsql_instrumented.py       MAC-SQL + Logging Matrix
  magsql_instrumented.py       MAG-SQL + Logging Matrix
  patches/*.patch              OpenAI SDK >=1.0 port, env-based config
evaluation/
  attribution_bias.py          confusion matrix on the 400-instance benchmark
  attribution_strategies.py    alternative orderings, re-scored on saved logs
  drspider_bias_report.py      per-set recall + confusion + ablation
  cross_system_report.py       recall vs artifact independence
```

## Reproducibility notes

- Baselines are pinned to exact commits and patched from `baselines/patches/`.
  The clones are gitignored; `setup_server.sh` reconstructs them.
- Only transport and configuration are patched. Prompts, agent logic and
  control flow are upstream.
- `attribution_strategies.py` re-scores **saved logs**, so alternative
  attribution rules cost no API calls — attribution is a pure function of the
  recorded state.
- The `DB_*` families reuse db_id names across sets with **different database
  content** (verified by checksum), so their databases are namespaced
  (`battle_death_0__synonym`). Copying them under their original names would
  silently overwrite one set's schema with another's.
