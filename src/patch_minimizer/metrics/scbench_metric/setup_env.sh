#!/usr/bin/env bash
# Build the SCBench metric home used by SCBenchMetricRunner.
#
# Creates $SCBENCH_METRIC_HOME (default ~/.cache/scbench_metric) containing:
#   venv/            Python 3.12 venv with slop_code + its metric deps
#   slop-code-bench/ pinned SlopCodeBench source checkout
#   bin/sg,ast-grep  the ast-grep binary (214-rule "slop" ruleset engine)
#   bin/uv           shim so slop_code's `uv run ruff/ty` resolves the venv tools
#   slop_rules.yaml  the bundled 214-rule slop ruleset
#   ENV.sh           `source` this to export SCBENCH_METRIC_HOME + PATH + rules
#
# Idempotent-ish: re-running rebuilds the venv (uv caches wheels, so it's fast).
set -euo pipefail

HOME_DIR="${SCBENCH_METRIC_HOME:-$HOME/.cache/scbench_metric}"
SCB_REPO="${SCB_REPO:-https://github.com/SprocketLab/slop-code-bench.git}"
# AIDEV-NOTE: v0.3 is the only compatible tag; v0.2 and main lack required metric helpers and slop_rules.yaml.
SCB_REF="${SCB_REF:-v0.3}"
ASTGREP_VER="${ASTGREP_VER:-0.39.6}"

# AIDEV-NOTE: Match ast-grep's release archive to the host architecture before setup.
case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64)
    ASTGREP_TARGET="x86_64-unknown-linux-gnu"
    ;;
  Darwin:arm64|Darwin:aarch64)
    ASTGREP_TARGET="aarch64-apple-darwin"
    ;;
  *)
    echo "[setup] unsupported host: $(uname -s) $(uname -m)" >&2
    echo "[setup] supported hosts: Linux x86-64 and macOS Apple Silicon" >&2
    exit 1
    ;;
esac

WORK="$(mktemp -d)"
mkdir -p "$HOME_DIR/bin"

echo "[setup] metric home: $HOME_DIR"

# 1) uv (manages its own Python 3.12) -------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  python3 -m venv "$WORK/uvhost"
  "$WORK/uvhost/bin/pip" install -q uv
  UV="$WORK/uvhost/bin/uv"
else
  UV="$(command -v uv)"
fi
"$UV" python install 3.12

# 2) clone slop-code-bench (for the package source + slop_rules.yaml) ------
git clone --depth 1 --branch "$SCB_REF" "$SCB_REPO" "$WORK/slop-code-bench"

# 3) 3.12 venv + metric deps (pinned tree-sitter pair from uv.lock) --------
"$UV" venv --clear --python 3.12 "$HOME_DIR/venv"
"$UV" pip install --python "$HOME_DIR/venv/bin/python" -q \
  pydantic structlog radon "tree-sitter==0.25.2" "tree-sitter-language-pack==0.13.0" \
  networkx numpy scipy click typer rich pyyaml ruff ty \
  gitpython ujson deepdiff tenacity tabulate python-dotenv omegaconf \
  pydantic-settings tiktoken pandas pyarrow jinja2 markupsafe httpx docker
"$UV" pip install --python "$HOME_DIR/venv/bin/python" -q --no-deps "$WORK/slop-code-bench"

# 4) ast-grep binary (prebuilt; no npm/cargo needed) ----------------------
curl -sSL \
  "https://github.com/ast-grep/ast-grep/releases/download/${ASTGREP_VER}/app-${ASTGREP_TARGET}.zip" \
  -o "$WORK/ag.zip"
python3 -c "import zipfile; zipfile.ZipFile('$WORK/ag.zip').extractall('$HOME_DIR/bin')"
chmod +x "$HOME_DIR/bin/sg" "$HOME_DIR/bin/ast-grep"

# 5) uv->direct shim + ruleset --------------------------------------------
cat > "$HOME_DIR/bin/uv" <<'EOF'
#!/bin/bash
if [ "$1" = "run" ]; then shift; fi
exec "$@"
EOF
chmod +x "$HOME_DIR/bin/uv"
cp "$WORK/slop-code-bench/configs/slop_rules.yaml" "$HOME_DIR/slop_rules.yaml"
rm -rf "$HOME_DIR/slop-code-bench"
cp -R "$WORK/slop-code-bench" "$HOME_DIR/slop-code-bench"

# 6) ENV.sh ----------------------------------------------------------------
cat > "$HOME_DIR/ENV.sh" <<EOF
export SCBENCH_METRIC_HOME="$HOME_DIR"
export SCBENCH_PY="$HOME_DIR/venv/bin/python"
export AST_GREP_RULES_PATH="$HOME_DIR/slop_rules.yaml"
export PATH="$HOME_DIR/bin:$HOME_DIR/venv/bin:\$PATH"
EOF

rm -rf "$WORK"
echo "[setup] done. Smoke test:"
"$HOME_DIR/venv/bin/python" -c \
  "from slop_code.metrics.driver import measure_snapshot_quality; print('  slop_code OK')"
echo "[setup] source $HOME_DIR/ENV.sh  before running the pipeline."
