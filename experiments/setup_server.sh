#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-shot server setup for the failure-attribution experiments.
#
# Brings a clean machine to the point where run_experiments.sh can run:
#   1. python deps
#   2. Spider databases (needed by every baseline)
#   3. Dr.Spider perturbation corpus (17 fault families, 3 target stages)
#   4. baseline repos, pinned + patched for the modern OpenAI SDK
#   5. benchmark JSONs + namespaced perturbed databases
#
# Idempotent: every step is skipped if its output already exists, so re-running
# after a failure costs nothing. Safe to run repeatedly.
#
# Usage:  bash experiments/setup_server.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m !! %s\033[0m\n' "$*"; }
die_missing() { printf '\033[1;31m !! %s\033[0m\n' "$*" >&2; exit 1; }

# Pinned so a re-run months from now reproduces the same baselines.
MACSQL_URL=https://github.com/wbbeyourself/MAC-SQL.git
MACSQL_SHA=31a9df5e0d520be4769be57a4b9022e5e34a14f4
MAGSQL_URL=https://github.com/Lancelot-Xie/MAG-SQL.git
MAGSQL_SHA=e69c494de198d0a25ca1fa19e3837cfd36590cdb
DINSQL_URL=https://github.com/MohammadrezaPourreza/Few-shot-NL2SQL-with-prompting.git
DINSQL_SHA=0474801616130413b024fd008b016a190684649e
CHESS_URL=https://github.com/ShayanTalaei/CHESS.git
CHESS_SHA=3d6e835f858d26885d21d4bc0215aeecf855efbe

DRSPIDER_URL="https://media.githubusercontent.com/media/awslabs/diagnostic-robustness-text-to-sql/main/data.tar.gz"
SPIDER_URL="https://huggingface.co/datasets/xlangai/spider/resolve/main/spider_data.zip"

# ---------------------------------------------------------------------------
log "1/6  Python dependencies"
# ---------------------------------------------------------------------------
$PY -m pip install --quiet --upgrade pip
$PY -m pip install --quiet -r requirements.txt
$PY -m pip install --quiet -r experiments/requirements-experiments.txt
$PY -c "import openai, func_timeout, rapidfuzz, matplotlib; print('  deps ok')"

# ---------------------------------------------------------------------------
log "2/6  Spider databases"
# ---------------------------------------------------------------------------
SPIDER_DIR="data/raw/spider_unzipped/spider_data"
if [ -d "$SPIDER_DIR/database" ]; then
  echo "  already present: $SPIDER_DIR"
else
  mkdir -p data/raw
  if [ ! -f data/raw/spider_data.zip ]; then
    echo "  downloading Spider (~200 MB)..."
    # The upstream zip has disappeared from HuggingFace before; a failure here
    # is not fatal because Dr.Spider ships the same dev databases (step 3).
    curl -fL --retry 3 -o data/raw/spider_data.zip "$SPIDER_URL" \
      || { rm -f data/raw/spider_data.zip
           warn "Spider download failed ($SPIDER_URL)."
           warn "Falling back to the Spider-dev databases bundled with Dr.Spider."; }
  fi
  if [ -f data/raw/spider_data.zip ]; then
    mkdir -p data/raw/spider_unzipped
    unzip -q -o data/raw/spider_data.zip -d data/raw/spider_unzipped
    echo "  extracted"
  fi
fi

# The pipeline and all baselines resolve databases at data/databases/<id>/<id>.sqlite
if [ ! -d data/databases ] || [ -z "$(ls -A data/databases 2>/dev/null)" ]; then
  if [ -d "$SPIDER_DIR/database" ]; then
    echo "  populating data/databases from Spider..."
    mkdir -p data/databases
    cp -r "$SPIDER_DIR/database/." data/databases/
  else
    echo "  deferred to step 3 (Dr.Spider Spider-dev databases)"
  fi
fi
echo "  data/databases: $(find data/databases -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l) databases"

# ---------------------------------------------------------------------------
log "3/6  Dr.Spider perturbation corpus"
# ---------------------------------------------------------------------------
if [ -d data/raw/drspider/Spider-dev ]; then
  echo "  already extracted"
else
  if [ ! -f data/raw/drspider.tar.gz ]; then
    echo "  downloading Dr.Spider (~168 MB)..."
    curl -fL --retry 3 -o data/raw/drspider.tar.gz "$DRSPIDER_URL"
  fi
  mkdir -p data/raw/drspider
  tar -xzf data/raw/drspider.tar.gz -C data/raw/drspider
  echo "  extracted $(find data/raw/drspider -maxdepth 1 -mindepth 1 -type d | wc -l) perturbation sets"
fi

# Fallback for step 2: Dr.Spider bundles the 20 Spider dev databases, which are
# the only ones the sampled benchmark references (the DB_* sets get namespaced
# copies in step 5).
if [ -z "$(ls -A data/databases 2>/dev/null)" ]; then
  [ -d data/raw/drspider/Spider-dev/databases ] \
    || die_missing "no Spider databases: the download failed and Dr.Spider carries none"
  echo "  populating data/databases from Dr.Spider Spider-dev..."
  mkdir -p data/databases
  cp -r data/raw/drspider/Spider-dev/databases/. data/databases/
  echo "  data/databases: $(find data/databases -maxdepth 1 -mindepth 1 -type d | wc -l) databases"
fi

# ---------------------------------------------------------------------------
log "4/6  Baseline repositories (pinned + patched)"
# ---------------------------------------------------------------------------
mkdir -p baselines
clone_pinned() {   # name url sha
  local name=$1 url=$2 sha=$3
  if [ -d "baselines/$name/.git" ]; then
    echo "  $name already cloned"
  else
    echo "  cloning $name..."
    git clone --quiet "$url" "baselines/$name"
    ( cd "baselines/$name" && git checkout --quiet "$sha" )
  fi
}
# Overrides rather than patches. These are whole-file replacements of the
# upstream API-transport modules, so copying them is exact on every platform.
# A patch would carry surrounding context lines and break the moment a checkout
# normalises line endings differently (which is exactly what happened on
# Windows). After setup, `git -C baselines/<name> diff` shows precisely what
# was changed relative to the pinned upstream commit.
apply_override() {   # name
  local name=$1 src="baselines/overrides/$name"
  [ -d "$src" ] || return 0
  ( cd "$src" && find . -type f ) | while read -r rel; do
    rel="${rel#./}"
    mkdir -p "baselines/$name/$(dirname "$rel")"
    cp "$src/$rel" "baselines/$name/$rel"
    echo "    override: $rel"
  done
  echo "  $name configured (OpenAI SDK >=1.0 transport, env-based config)"
}

clone_pinned MAC-SQL "$MACSQL_URL" "$MACSQL_SHA"; apply_override MAC-SQL
clone_pinned MAG-SQL "$MAGSQL_URL" "$MAGSQL_SHA"; apply_override MAG-SQL

# MAG-SQL hardcodes its value-match cache at ./data/bird/dev/match_content.json.
# That directory does not exist here, and one shared file across 17 Dr.Spider
# sets would serve one set's matches to another. Make it env-driven; the runner
# points MAGSQL_MATCH_CACHE at a per-set file. Idempotent string replacement,
# not a patch hunk, so line-ending normalisation cannot break it.
$PY - <<'PYFIX'
from pathlib import Path
p = Path("baselines/MAG-SQL/main_scripts/MAG.py")
old = "        matched_dict_path = './data/bird/dev/match_content.json'"
new = ("        matched_dict_path = os.environ.get("
       "'MAGSQL_MATCH_CACHE', './data/bird/dev/match_content.json')")
s = p.read_text(encoding="utf-8")
if new in s:
    print("  MAG-SQL match cache: already env-driven")
else:
    assert old in s, "MAG.py no longer contains the hardcoded cache path"
    p.write_text(s.replace(old, new), encoding="utf-8")
    print("  MAG-SQL match cache: now read from $MAGSQL_MATCH_CACHE")
PYFIX
clone_pinned DIN-SQL "$DINSQL_URL" "$DINSQL_SHA"
# DIN-SQL ships as a script, not a module: it parses sys.argv at import time and
# its line 444 (`API_KEY = #key`) is not valid Python, so the published file
# cannot be imported at all. Replace exactly that block with an env-driven
# header plus an openai<1.0 -> >=1.0 transport shim. Prompts, few-shot
# exemplars, sampling parameters and control flow are untouched, so
# `git -C baselines/DIN-SQL diff` shows the whole change. Idempotent.
$PY - <<'PYFIX'
import sys
from pathlib import Path
sys.path.insert(0, "baselines/overrides/DIN-SQL")
import _harness_shim as shim

p = Path("baselines/DIN-SQL/DIN-SQL.py")
s = p.read_text(encoding="utf-8")
if "harness shim (attributability audit)" in s:
    print("  DIN-SQL: shim already applied")
elif shim.UPSTREAM_BLOCK in s:
    p.write_text(s.replace(shim.UPSTREAM_BLOCK, shim.HEADER), encoding="utf-8")
    print("  DIN-SQL: argv/API-key block replaced, openai>=1.0 transport installed")
else:
    raise SystemExit("DIN-SQL.py no longer contains the expected argv/API-key block")
PYFIX

if [ "${WITH_CHESS:-0}" = "1" ]; then
  clone_pinned CHESS "$CHESS_URL" "$CHESS_SHA"
  warn "CHESS is BIRD-only (no Spider support); it cannot use the Dr.Spider benchmark."
fi

# ---------------------------------------------------------------------------
log "5/6  Benchmark construction"
# ---------------------------------------------------------------------------
# The sampled benchmark JSONs are committed so every machine scores the exact
# same instances. Rebuild only if they are missing.
if [ -f data/drspider_benchmark/manifest.json ]; then
  echo "  benchmark already present ($(ls data/drspider_benchmark/*.json | wc -l) files)"
else
  $PY scripts_drspider/build_drspider_benchmark.py --per-set "${PER_SET:-100}"
fi

# Stage the DB_* perturbed databases under namespaced ids. Idempotent: it skips
# databases already copied and records `orig_db_id` so re-runs are no-ops.
$PY scripts_drspider/prepare_db_perturbation_dbs.py

# ---------------------------------------------------------------------------
log "6/6  Environment check"
# ---------------------------------------------------------------------------
if [ -z "${OPENAI_API_KEY:-}" ] && [ ! -f .env ]; then
  warn "No OPENAI_API_KEY in the environment and no .env file."
  warn "Create .env with:   OPENAI_API_KEY=sk-..."
  warn "(.env is gitignored and must never be committed.)"
else
  echo "  API key source: ${OPENAI_API_KEY:+environment}${OPENAI_API_KEY:-.env file}"
fi

log "Setup complete — next:  bash experiments/run_experiments.sh"
