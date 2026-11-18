#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run the failure-attribution experiments end to end and write the report.
#
# Every stage resumes: instances already logged are skipped, so an interrupted
# run continues where it stopped and re-spends nothing. Kill and restart freely.
#
#   bash experiments/run_experiments.sh                 # both baselines, 17 sets
#   SYSTEMS=macsql       bash experiments/run_experiments.sh
#   PERT_GROUPS="NLQ SQL"     bash experiments/run_experiments.sh
#   LIMIT=5              bash experiments/run_experiments.sh   # smoke test
#
# Env:
#   SYSTEMS   comma list: macsql,magsql,dinsql       (default: macsql,magsql)
#   DATASETS  space list: drspider bird               (default: drspider)
#   PERT_GROUPS    space list: NLQ SQL DB                 (default: all three)
#   LIMIT     cap instances per set                  (default: none)
#   MODEL     LLM backbone for every system          (default: gpt-4o-mini)
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
SYSTEMS="${SYSTEMS:-macsql,magsql}"
PERT_GROUPS="${PERT_GROUPS:-NLQ SQL DB}"
# Dr.Spider carries construction-time stage ground truth, so it is the only
# dataset recall can be computed on. BIRD adds the attributed distribution on
# natural instances, and activates the x2 external-knowledge slot via its
# `evidence` field. PERT_GROUPS applies to Dr.Spider only; BIRD has one set.
DATASETS="${DATASETS:-drspider}"
MODEL="${MODEL:-gpt-4o-mini}"
LIMIT_ARG=""
[ -n "${LIMIT:-}" ] && LIMIT_ARG="--limit ${LIMIT}"

mkdir -p output_logs reports
STAMP="$(date +%Y%m%d_%H%M%S)"
log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# Fail fast on a missing key rather than after a long partial run.
if [ -z "${OPENAI_API_KEY:-}" ] && ! grep -qs '^OPENAI_API_KEY=' .env; then
  echo "ERROR: no OPENAI_API_KEY (env or .env). Aborting before spending time." >&2
  exit 1
fi

# Backbone routing. A model id may carry a provider prefix:
#
#   gpt-4o-mini                the OPENAI_API_KEY in the environment or .env
#   ollama/qwen3:32b      a local ollama server, OpenAI-compatible on :11434
#   openai/gpt-4o-mini         explicit form of the default
#
# Both baselines build their client from OPENAI_API_BASE, so pointing that at
# any OpenAI-compatible server is all an open-weight backbone needs. The log
# directory is keyed on the full id, prefix included, so runs never collide.
API_MODEL="$MODEL"
case "$MODEL" in
  ollama/*)
    API_MODEL="${MODEL#ollama/}"
    export OPENAI_API_BASE="${OLLAMA_BASE:-http://localhost:11434/v1}"
    export OPENAI_API_KEY="ollama"      # ollama ignores it, the SDK requires it
    if ! curl -s -m 5 -o /dev/null "${OPENAI_API_BASE%/v1}/api/tags"; then
      echo "ERROR: ollama not reachable at $OPENAI_API_BASE" >&2
      exit 1
    fi
    ;;
  # NOT stripped: core/api_config.py and MAG-SQL's llm.py route on the
  # namespace being present ("openai/x" -> OpenRouter key + base, bare "x"
  # -> OpenAI direct). Stripping it here silently sent every openai/* run
  # to OpenAI instead of OpenRouter. The log dir keys on --label, not on
  # this, so leaving the prefix on does not move any existing logs.
  openai/*) : ;;
esac

export MACSQL_MODEL="$API_MODEL"
export MAGSQL_MODEL="$API_MODEL"

has() { case ",$SYSTEMS," in *",$1,"*) return 0;; *) return 1;; esac; }

# ---------------------------------------------------------------------------
if has macsql; then
  for DS in $DATASETS; do
    log "MAC-SQL over $DS  (groups: $PERT_GROUPS, model: $MODEL)"
    $PY baselines/macsql_instrumented.py --dataset "$DS" \
        --groups $PERT_GROUPS --model "$API_MODEL" --label "$MODEL" $LIMIT_ARG \
        2>&1 | tee "output_logs/macsql_${DS}_$(echo "$MODEL" | tr '/:' '--')_${STAMP}.log" \
        | grep -E "^\[[0-9]+/|ok=|elapsed|done:|!!" || true
  done
fi

# ---------------------------------------------------------------------------
if has magsql; then
  log "MAG-SQL over Dr.Spider"
  echo "NOTE: MAG-SQL's Soft_Schema_linker runs two whole-corpus preprocessing"
  echo "      passes first (value matching, then one LLM call per table per DB)."
  echo "      That cost is paid once and cached to disk, but it is not small."
  for DS in $DATASETS; do
    log "MAG-SQL over $DS"
    $PY baselines/magsql_instrumented.py --dataset "$DS" \
        --groups $PERT_GROUPS --model "$API_MODEL" --label "$MODEL" $LIMIT_ARG \
        2>&1 | tee "output_logs/magsql_${DS}_$(echo "$MODEL" | tr '/:' '--')_${STAMP}.log" \
        | grep -E "^\[[0-9]+/|ok=|elapsed|done:|!!" || true
  done
fi

# ---------------------------------------------------------------------------
# DIN-SQL is Dr.Spider-only here: upstream ships a separate DIN-SQL_BIRD.py
# whose prompts carry BIRD's `evidence` field, and running the Spider script on
# BIRD would silently drop that hint.
if has dinsql; then
  log "DIN-SQL over Dr.Spider  (groups: $PERT_GROUPS, model: $MODEL)"
  echo "NOTE: DIN-SQL issues 4 LLM calls per instance (schema linking,"
  echo "      classification, generation, self-correction) on long few-shot"
  echo "      prompts. Budget before a full 1,700-instance sweep."
  $PY baselines/dinsql_instrumented.py --dataset drspider \
      --groups $PERT_GROUPS --model "$API_MODEL" --label "$MODEL" $LIMIT_ARG \
      2>&1 | tee "output_logs/dinsql_drspider_$(echo "$MODEL" | tr '/:' '--')_${STAMP}.log" \
      | grep -E "^\[[0-9]+/|ok=|elapsed|done:|!!" || true
fi

# ---------------------------------------------------------------------------
# Skipped for all but the last backbone of a multi-model sweep: the reports read
# every log directory, so only the final pass carries complete numbers.
if [ "${SKIP_ANALYSIS:-0}" = "1" ]; then
  echo
  echo "  (analysis deferred)"
  exit 0
fi

log "Analysis"
# ---------------------------------------------------------------------------
# 1. Attribution bias on the original 400-instance perturbation benchmark
#    (reads existing nlsql logs only — no API calls).
if [ -d output_logs/raw_logs ]; then
  $PY evaluation/attribution_bias.py 2>&1 | tee "reports/attribution_bias_${STAMP}.txt"
fi

# 2. Dr.Spider: per-set recall, confusion matrix, strategy ablation.
#    Reads the nlsql control's logs, which this comparison no longer produces.
if [ -d output_logs/drspider ]; then
  $PY evaluation/drspider_bias_report.py 2>&1 | tee "reports/drspider_bias_${STAMP}.txt"
fi

# 3. Correct stored verdicts before anything reads them. Re-executes saved
#    predictions only — no API calls — and fixes the comparator bug that made
#    a TEXT '3' differ from an INTEGER 3.
$PY evaluation/rescore.py 2>&1 | tail -20 | tee "reports/rescore_${STAMP}.txt"

# 4. Instrument audit. Runs BEFORE the recall table on purpose: a zero is not
#    a result about attribution until stage inactivity has been excluded.
$PY evaluation/instrument_audit.py 2>&1 | tee "reports/instrument_audit_${STAMP}.txt"

# 5. Failure statistics: confusion matrix, per-family, failure modes, stage activity.
for sysname in macsql magsql dinsql; do
  $PY evaluation/failure_stats.py --system "$sysname" 2>&1
done | tee "reports/failure_stats_${STAMP}.txt"

# 6. Framework-attributed distribution: every dataset, no ground truth needed.
$PY evaluation/distribution_report.py 2>&1 | tee "reports/distribution_${STAMP}.txt"

# 7. Recall against construction-time ground truth: Dr.Spider only.
if [ -f evaluation/cross_system_report.py ]; then
  $PY evaluation/cross_system_report.py 2>&1 | tee "reports/cross_system_${STAMP}.txt"
fi

log "Done. Reports in reports/ (stamp ${STAMP})"
ls -la reports/ | tail -5
