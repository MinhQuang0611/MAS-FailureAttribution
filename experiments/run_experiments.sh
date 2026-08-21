#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run the failure-attribution experiments end to end and write the report.
#
# Every stage resumes: instances already logged are skipped, so an interrupted
# run continues where it stopped and re-spends nothing. Kill and restart freely.
#
#   bash experiments/run_experiments.sh                 # MAC-SQL, all 17 sets
#   SYSTEMS=macsql,nlsql bash experiments/run_experiments.sh
#   PERT_GROUPS="NLQ SQL"     bash experiments/run_experiments.sh
#   LIMIT=5              bash experiments/run_experiments.sh   # smoke test
#
# Env:
#   SYSTEMS   comma list: macsql,magsql,nlsql        (default: macsql)
#   PERT_GROUPS    space list: NLQ SQL DB                 (default: all three)
#   LIMIT     cap instances per set                  (default: none)
#   MODEL     LLM backbone for every system          (default: gpt-4o)
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
SYSTEMS="${SYSTEMS:-macsql}"
PERT_GROUPS="${PERT_GROUPS:-NLQ SQL DB}"
MODEL="${MODEL:-gpt-4o}"
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

export MACSQL_MODEL="$MODEL"
export MAGSQL_MODEL="$MODEL"

has() { case ",$SYSTEMS," in *",$1,"*) return 0;; *) return 1;; esac; }

# ---------------------------------------------------------------------------
if has macsql; then
  log "MAC-SQL over Dr.Spider  (groups: $PERT_GROUPS, model: $MODEL)"
  $PY baselines/macsql_instrumented.py \
      --groups $PERT_GROUPS --model "$MODEL" $LIMIT_ARG \
      2>&1 | tee "output_logs/macsql_${STAMP}.log" \
      | grep -E "^\[[0-9]+/|ok=|elapsed|done:|!!" || true
fi

# ---------------------------------------------------------------------------
if has magsql; then
  log "MAG-SQL over Dr.Spider"
  echo "NOTE: MAG-SQL's Soft_Schema_linker runs two whole-corpus preprocessing"
  echo "      passes first (value matching, then one LLM call per table per DB)."
  echo "      That cost is paid once and cached to disk, but it is not small."
  $PY baselines/magsql_instrumented.py \
      --groups $PERT_GROUPS --model "$MODEL" $LIMIT_ARG \
      2>&1 | tee "output_logs/magsql_${STAMP}.log" \
      | grep -E "^\[[0-9]+/|ok=|elapsed|done:|!!" || true
fi

# ---------------------------------------------------------------------------
if has nlsql; then
  log "nlsql pipeline over Dr.Spider  (the reconstructed-artifact control)"
  if ! curl -s -m 5 -o /dev/null http://localhost:8388/docs; then
    echo "  nlsql API not reachable at :8388 — start it with:"
    echo "      cd nlsql && docker compose up -d"
    echo "  skipping."
  else
    $PY scripts_drspider/run_drspider.py \
        --groups $PERT_GROUPS --model "$MODEL" $LIMIT_ARG \
        2>&1 | tee "output_logs/nlsql_${STAMP}.log" \
        | grep -E "^\[[0-9]+/|elapsed|done|!!" || true
  fi
fi

# ---------------------------------------------------------------------------
log "Analysis"
# ---------------------------------------------------------------------------
# 1. Attribution bias on the original 400-instance perturbation benchmark
#    (reads existing nlsql logs only — no API calls).
if [ -d output_logs/raw_logs ]; then
  $PY evaluation/attribution_bias.py 2>&1 | tee "reports/attribution_bias_${STAMP}.txt"
fi

# 2. Dr.Spider: per-set recall, confusion matrix, strategy ablation.
$PY evaluation/drspider_bias_report.py 2>&1 | tee "reports/drspider_bias_${STAMP}.txt"

# 3. Cross-system comparison, if more than one system produced logs.
if [ -f evaluation/cross_system_report.py ]; then
  $PY evaluation/cross_system_report.py 2>&1 | tee "reports/cross_system_${STAMP}.txt"
fi

log "Done. Reports in reports/ (stamp ${STAMP})"
ls -la reports/ | tail -5
