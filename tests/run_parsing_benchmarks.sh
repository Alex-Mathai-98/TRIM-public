#!/usr/bin/env bash
# AIDEV-NOTE: runs all parser_baseline.py verify tests and checks matched counts
# against each suite's best_score.json — exits non-zero on any regression.
#
# Usage:
#   . .claude/prelude.sh
#   bash tests/run_parsing_benchmarks.sh          # run all suites
#   bash tests/run_parsing_benchmarks.sh swe_bench_swe_agent live_kbench_swe_agent_gemini_3_pro  # specific suites
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_DIR="$SCRIPT_DIR/parsing_benchmark_tests"

# All suites with a parser_baseline.py (excludes live_kbench_common which is shared data).
ALL_SUITES=(
    swe_bench_swe_agent
    live_kbench_swe_agent_gemini_3_pro
    live_kbench_openhands_gemini_3_pro
    live_kbench_mini_swe_claude_opus_4_5
)

if [ $# -gt 0 ]; then
    SUITES=("$@")
else
    SUITES=("${ALL_SUITES[@]}")
fi

passed=0
failed=0
skipped=0
results=()

for suite in "${SUITES[@]}"; do
    suite_dir="$BENCH_DIR/$suite"
    baseline="$suite_dir/parser_baseline.py"
    score_file="$suite_dir/best_score.json"

    if [ ! -f "$baseline" ]; then
        echo "SKIP  $suite  (no parser_baseline.py)"
        results+=("SKIP  $suite  (no parser_baseline.py)")
        skipped=$((skipped + 1))
        continue
    fi
    if [ ! -f "$score_file" ]; then
        echo "SKIP  $suite  (no best_score.json)"
        results+=("SKIP  $suite  (no best_score.json)")
        skipped=$((skipped + 1))
        continue
    fi

    best_matched=$(python3 -c "import json; print(json.load(open('$score_file'))['matched'])")
    best_total=$(python3 -c "import json; print(json.load(open('$score_file'))['total'])")

    echo "---------------------------------------------------------------"
    echo "RUN   $suite  (best: $best_matched/$best_total)"
    echo "---------------------------------------------------------------"

    output=$(python3 "$baseline" verify 2>&1) || true
    echo "$output"

    verify_line=$(echo "$output" | grep -E '^verify:' | tail -1)
    if [ -z "$verify_line" ]; then
        echo "FAIL  $suite  (no verify output — parser crashed?)"
        results+=("FAIL  $suite  (no verify output)")
        failed=$((failed + 1))
        continue
    fi

    # AIDEV-NOTE: \b is required — a bare 'matched=' also matches inside 'mismatched='.
    current_total=$(echo "$verify_line" | grep -oP '\btotal=\K[0-9]+')
    current_matched=$(echo "$verify_line" | grep -oP '\bmatched=\K[0-9]+')

    # AIDEV-NOTE: non-integers make [ -lt ] error silently and fall through to PASS; fail instead.
    if ! [[ "$current_matched" =~ ^[0-9]+$ && "$current_total" =~ ^[0-9]+$ ]]; then
        echo "FAIL  $suite  (could not parse verify line: $verify_line)"
        results+=("FAIL  $suite  (unparseable verify output)")
        failed=$((failed + 1))
        continue
    fi

    if [ "$current_matched" -lt "$best_matched" ]; then
        echo "FAIL  $suite  matched=$current_matched < best=$best_matched (regression!)"
        results+=("FAIL  $suite  matched=$current_matched < best=$best_matched  REGRESSION")
        failed=$((failed + 1))
    elif [ "$current_matched" -gt "$best_matched" ]; then
        echo "PASS  $suite  matched=$current_matched > best=$best_matched (improvement!)"
        echo "      Update best_score.json: matched=$current_matched total=$current_total"
        results+=("PASS  $suite  matched=$current_matched/$current_total  (improved from $best_matched)")
        passed=$((passed + 1))
    else
        echo "PASS  $suite  matched=$current_matched/$current_total"
        results+=("PASS  $suite  matched=$current_matched/$current_total")
        passed=$((passed + 1))
    fi
done

echo ""
echo "==============================================================="
echo "SUMMARY: $passed passed, $failed failed, $skipped skipped"
echo "==============================================================="
for r in "${results[@]}"; do
    echo "  $r"
done
echo ""

if [ "$failed" -gt 0 ]; then
    echo "RESULT: FAIL — regression detected"
    exit 1
else
    echo "RESULT: PASS — no regressions"
    exit 0
fi
