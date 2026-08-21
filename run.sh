#!/usr/bin/env bash
# ===========================================================================
#  Failure-attribution experiments — single entrypoint.
#
#  Edit the CONFIG block below, then:
#
#      bash run.sh                      # foreground
#      nohup bash run.sh > run.out 2>&1 &   # unattended, survives logout
#
#  Everything resumes. Instances already logged are skipped, so killing this
#  and restarting costs nothing. Setup steps skip whatever already exists.
#
#  Any CONFIG value can also be overridden from the environment:
#      SYSTEMS=magsql LIMIT=2 bash run.sh
# ===========================================================================

# --------------------------- CONFIG ----------------------------------------

# Which pipelines to run. Comma-separated: macsql, magsql, nlsql
#   macsql  3 agents, schema artifact is real  (recommended first run)
#   magsql  4 agents, finest-grained artifacts (expensive preprocessing)
#   nlsql   the control - needs its Docker stack up on :8388
SYSTEMS="${SYSTEMS:-macsql}"

# Which Dr.Spider perturbation groups. Space-separated: NLQ SQL DB
#   NLQ -> Intent ground truth  (900)   <- the stage with 0% recall today
#   SQL -> Skeleton ground truth (500)
#   DB  -> Schema ground truth   (300)
PERT_GROUPS="${PERT_GROUPS:-NLQ SQL DB}"

# Cap instances per perturbation set. Empty = all 100. Set to 2-5 to smoke test.
LIMIT="${LIMIT:-}"

# LLM backbone. Must be the SAME for every system, or the comparison measures
# the model rather than the pipeline architecture.
MODEL="${MODEL:-gpt-4o}"

# Python interpreter on this machine.
PYTHON="${PYTHON:-python3}"

# Set to 1 to skip setup (deps/corpora/baselines already in place).
SKIP_SETUP="${SKIP_SETUP:-0}"

# Set to 1 to skip the run and only regenerate reports from existing logs.
ANALYSIS_ONLY="${ANALYSIS_ONLY:-0}"

# ---------------------------------------------------------------------------

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p output_logs reports
MAIN_LOG="output_logs/run_${STAMP}.log"

c() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

banner() {
  echo
  c "======================================================================"
  c " $*"
  c "======================================================================"
}

# --------------------------- preflight -------------------------------------
banner "Preflight"

command -v "$PYTHON" >/dev/null 2>&1 || {
  command -v python >/dev/null 2>&1 && PYTHON=python \
    || die "no python found. Set PYTHON=/path/to/python"
}
echo "  python : $($PYTHON --version 2>&1) ($(command -v "$PYTHON"))"

command -v git >/dev/null 2>&1 || die "git not found"
command -v curl >/dev/null 2>&1 || die "curl not found (needed to fetch corpora)"

# Fail on a missing key now rather than after a long partial run.
if [ -z "${OPENAI_API_KEY:-}" ] && ! grep -qs '^OPENAI_API_KEY=' .env; then
  die "no OPENAI_API_KEY in the environment and none in .env
       create it with:  printf 'OPENAI_API_KEY=sk-...\\n' > .env"
fi
echo "  api key: present"

# Corpora plus databases need roughly 3 GB.
AVAIL_KB=$(df -Pk . 2>/dev/null | awk 'NR==2{print $4}')
if [ -n "${AVAIL_KB:-}" ] && [ "$AVAIL_KB" -lt 3000000 ]; then
  warn "  disk   : only $((AVAIL_KB/1024)) MB free — corpora need ~3 GB"
else
  echo "  disk   : $((${AVAIL_KB:-0}/1024/1024)) GB free"
fi

echo "  systems: $SYSTEMS"
echo "  groups : $PERT_GROUPS"
echo "  model  : $MODEL"
[ -n "$LIMIT" ] && warn "  limit  : $LIMIT per set (SMOKE TEST — not a full run)"

# nlsql cannot run without its stack; say so now, not in three hours.
case ",$SYSTEMS," in *,nlsql,*)
  if ! curl -s -m 5 -o /dev/null http://localhost:8388/docs 2>/dev/null; then
    warn "  nlsql  : API not reachable on :8388 — it will be skipped."
    warn "           start it with:  cd nlsql && docker compose up -d"
  else
    echo "  nlsql  : API reachable"
  fi
;; esac

# --------------------------- setup -----------------------------------------
if [ "$SKIP_SETUP" = "1" ]; then
  banner "Setup skipped (SKIP_SETUP=1)"
else
  banner "Setup — deps, corpora, baselines"
  PYTHON="$PYTHON" PER_SET="${PER_SET:-100}" \
    bash experiments/setup_server.sh 2>&1 | tee -a "$MAIN_LOG" \
    || die "setup failed — see $MAIN_LOG"
fi

# --------------------------- run -------------------------------------------
if [ "$ANALYSIS_ONLY" = "1" ]; then
  banner "Run skipped (ANALYSIS_ONLY=1)"
else
  banner "Experiments — $SYSTEMS"
  START=$(date +%s)
  PYTHON="$PYTHON" SYSTEMS="$SYSTEMS" PERT_GROUPS="$PERT_GROUPS" \
  MODEL="$MODEL" LIMIT="$LIMIT" \
    bash experiments/run_experiments.sh 2>&1 | tee -a "$MAIN_LOG"
  echo
  echo "  wall clock: $(( ($(date +%s) - START) / 60 )) min"
fi

# --------------------------- summary ---------------------------------------
banner "Summary"

for d in "nlsql:output_logs/drspider" \
         "MAC-SQL:output_logs/macsql_drspider" \
         "MAG-SQL:output_logs/magsql_drspider"; do
  name="${d%%:*}"; path="${d#*:}"
  if [ -d "$path" ]; then
    n=$(find "$path" -name '*.json' ! -name '_*' ! -name 'resume_checkpoint.json' 2>/dev/null | wc -l)
    printf '  %-8s %5s instances logged\n' "$name" "$n"
  fi
done

echo
echo "  reports:"
ls -1t reports/ 2>/dev/null | head -5 | sed 's/^/    /'
echo
echo "  full log: $MAIN_LOG"
echo

# Surface the headline number rather than making the reader open a file.
LATEST=$(ls -1t reports/cross_system_*.txt 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  c "Cross-system stage recall (higher = attribution finds the real stage):"
  sed -n '/SUMMARY/,$p' "$LATEST" | sed 's/^/  /'
fi

c "Done."
