#!/usr/bin/env bash
# ===========================================================================
#  Full experiment chain, entirely on openai/gpt-5.6-luna via OpenRouter.
#
#  Every stage is resume-safe: instances already logged are skipped, so this
#  can be killed and restarted at any point at the cost of the one in-flight
#  instance. That is what makes it safe to run as a restartable service.
#
#  Run it as a service (survives SSH logout and reboot):
#      systemctl --user start  mas-experiments
#      systemctl --user status mas-experiments
#      journalctl --user -u mas-experiments -f
#
#  Or directly in the foreground:
#      bash experiments/run_chain.sh
# ===========================================================================
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source .venv/bin/activate

MODEL="${CHAIN_MODEL:-openai/gpt-5.6-luna}"

step() { printf '\n############ %s  [%s]\n' "$*" "$(date '+%F %T')"; }

step "[1/3] BIRD full sweep (1,534) — MAC-SQL + MAG-SQL / $MODEL"
SYSTEMS=macsql,magsql DATASETS=bird MODELS="$MODEL" SKIP_SETUP=1 bash run.sh

step "[2/3] Dr.Spider full sweep (1,700) — MAC-SQL / $MODEL"
# Required before the counterfactual: its failure set and its control arm must
# come from the SAME backbone being intervened on, or "did the failure survive
# the repair?" confounds the intervention with a change of model.
SYSTEMS=macsql DATASETS=drspider MODELS="$MODEL" SKIP_SETUP=1 bash run.sh

step "[3/3] Counterfactual attribution — MAC-SQL / $MODEL"
echo "--- schema arm (DB group: seed the Selector with the gold tables) ---"
python baselines/counterfactual.py --intervention schema --groups DB     --model "$MODEL"
echo "--- intent arm (NLQ group: hand back the pre-perturbation question) ---"
python baselines/counterfactual.py --intervention intent --groups NLQ    --model "$MODEL"
echo "--- none arm (control: resampling alone, no repair) ---"
python baselines/counterfactual.py --intervention none   --groups NLQ DB --model "$MODEL"

step "ALL DONE"
