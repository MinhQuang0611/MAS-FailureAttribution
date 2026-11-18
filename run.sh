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

# Which pipelines to run. Comma-separated: macsql, magsql, dinsql
#   macsql  MAC-SQL   3 agents, schema artifact is real
#   magsql  MAG-SQL   4 agents, finest-grained artifacts (expensive preprocessing)
#   dinsql  DIN-SQL   4 modules, and the only one whose gate is explicit: its
#           classifier routes each question EASY / NON-NESTED / NESTED, and the
#           decomposition artifact exists only on NESTED. Dr.Spider only (its
#           BIRD variant is a separate upstream script). 4 LLM calls/instance.
# All three are published systems run at their pinned upstream commits. CHESS is
# a fourth in setup_server.sh but is BIRD-only, so it cannot be scored on
# Dr.Spider. The nlsql control is not part of this comparison.
SYSTEMS="${SYSTEMS:-macsql,magsql}"

# Which datasets. Space-separated: drspider bird
#   drspider  17 perturbation families, stage ground truth by construction —
#             the only dataset stage RECALL can be measured on
#   bird      1,534 natural instances (sampled), no injected faults: gives the
#             attributed DISTRIBUTION on a second corpus, and activates the x2
#             external-knowledge slot through BIRD's `evidence` field
DATASETS="${DATASETS:-drspider}"

# Which Dr.Spider perturbation groups. Space-separated: NLQ SQL DB
#   NLQ -> Intent ground truth  (900)   <- the stage with 0% recall today
#   SQL -> Skeleton ground truth (500)
#   DB  -> Schema ground truth   (300)
PERT_GROUPS="${PERT_GROUPS:-NLQ SQL DB}"

# Cap instances per perturbation set. Empty = all 100. Set to 2-5 to smoke test.
LIMIT="${LIMIT:-}"

# LLM backbones. Space-separated; every system is run against each in turn, so
# the result is a system x backbone grid. Within one backbone the model is the
# same for every system — otherwise the comparison would measure the LLM rather
# than the pipeline architecture. Across backbones is the second axis: whether
# the attribution bias survives a change of model.
#
#   gpt-4o-mini                 OpenAI, the backbone of the original study
#   ollama/qwen3:32b       a local ollama server (open weights, no API cost)
#
# Costs multiply by the number of backbones. Start with one.
MODELS="${MODELS:-${MODEL:-gpt-4o-mini}}"

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
echo "  data   : $DATASETS"
echo "  groups : $PERT_GROUPS"
echo "  models : $MODELS"
[ -n "$LIMIT" ] && warn "  limit  : $LIMIT per set (SMOKE TEST — not a full run)"

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
  START=$(date +%s)
  LAST=""; for m in $MODELS; do LAST="$m"; done
  for m in $MODELS; do
    banner "Experiments — $SYSTEMS on $m"
    SKIP=1; [ "$m" = "$LAST" ] && SKIP=0
    PYTHON="$PYTHON" SYSTEMS="$SYSTEMS" PERT_GROUPS="$PERT_GROUPS" \
    DATASETS="$DATASETS" MODEL="$m" LIMIT="$LIMIT" SKIP_ANALYSIS="$SKIP" \
      bash experiments/run_experiments.sh 2>&1 | tee -a "$MAIN_LOG"
  done
  echo
  echo "  wall clock: $(( ($(date +%s) - START) / 60 )) min"
fi

# --------------------------- summary ---------------------------------------
banner "Summary"

# One row per system x backbone, matching the layout the logs are written in.
for path in output_logs/macsql_* output_logs/magsql_* output_logs/dinsql_*; do
  [ -d "$path" ] || continue
  name="$(basename "$path")"
  for md in "$path"/*/; do
    [ -d "$md" ] || continue
    n=$(find "$md" -name '*.json' ! -name '_*' ! -name 'resume_checkpoint.json' 2>/dev/null | wc -l)
    printf '  %-18s %-22s %5s instances logged\n' "$name" "$(basename "$md")" "$n"
  done
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
