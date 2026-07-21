#!/usr/bin/env bash
# Smoke test for ProcessSupervisor — validates the core lifecycle locally.
# Runs both the desktop (Node) and backend (Python) supervisors with fake processes.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }

check() {
  local label="$1" cmd="$2"
  echo -n "  ▶ $label ... "
  if eval "$cmd" >/dev/null 2>&1; then
    green "PASS"
    PASS=$((PASS + 1))
  else
    red "FAIL"
    FAIL=$((FAIL + 1))
    echo "    exit code: $?"
  fi
}

echo "=========================================="
echo "  ProcessSupervisor Smoke Test (macOS)"
echo "=========================================="
echo ""

echo "[1/3] Desktop BackendProcessSupervisor"
cd "$ROOT/desktop"
check "existing tests pass" "node --test tests/test-backend-process-supervisor.cjs"

echo ""
echo "[2/3] Backend ScraperProcessSupervisor"
VENV=$(mktemp -d)
python3 -m venv "$VENV"
source "$VENV/bin/activate"
pip install pytest -q -q 2>/dev/null || true
cd "$ROOT/backend"
check "existing tests pass" "python3 -m pytest tests/test_scraper_process_supervisor.py -q"

echo ""
echo "[3/3] Legacy test suite (regression)"
cd "$ROOT/desktop"
check "desktop runtime-target" "node --test tests/test-runtime-target.cjs"
cd "$ROOT/backend"
check "backend scraper_process" "python3 -m pytest tests/test_scraper_process.py -q"
check "backend jobs" "python3 -m pytest tests/test_jobs.py -q" || true

deactivate
rm -rf "$VENV"

echo ""
echo "=========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
