#!/usr/bin/env bash
# Bellhaven sync: setup, tests, pipeline, review app.
#
# Usage (from the bellhaven-sync folder):
#   bash run.sh            setup + tests + API check + pipeline + start the review app
#   bash run.sh --no-app   same, but stop before starting the review app
#   bash run.sh --app      only start the review app (skip everything else)
#   bash run.sh --install-shortcut
#                          put a "Bellhaven Review" launcher on your Windows desktop
#
# Every step checks its exit code. On the first failure it prints a block
# between ##### lines and stops: paste that block into the chat.

set -u
set -o pipefail
cd "$(dirname "$0")" || exit 1
mkdir -p logs
LOG="logs/run_$(date +%Y%m%d_%H%M%S).log"
PY=".venv/bin/python"
STEP_NO=0

fail() {  # fail <step name> <command text> <exit code>
  echo ""
  echo "################################ FAILED ################################"
  echo "Step:      $1"
  echo "Command:   $2"
  echo "Exit code: $3"
  echo "Folder:    $(pwd)"
  echo "System:    $(uname -sr) | $(python3 --version 2>&1)"
  echo "--- last 40 lines of output ---"
  tail -n 40 "$LOG"
  echo "########################################################################"
  echo "Full log saved to: $LOG"
  echo "Paste everything between the ##### lines into the chat."
  exit "$3"
}

step() {  # step <name> <command...>
  STEP_NO=$((STEP_NO + 1))
  local name="$1"; shift
  echo ""
  echo "==> [$STEP_NO] $name"
  echo "    \$ $*"
  echo "==> [$STEP_NO] $name :: $*" >> "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    fail "$name" "$*" "$rc"
  fi
  echo "    ok"
}

load_env() {
  if [ -f .env ]; then
    set -a; . ./.env; set +a
  fi
  if [ -z "${BH_TOKEN:-}" ]; then
    echo "BH_TOKEN is not set. Create a .env file in this folder:" | tee -a "$LOG"
    echo "  printf 'BH_TOKEN=bh_your_token\nBH_REVIEWER=\"Your Name\"\n' > .env" | tee -a "$LOG"
    fail "Load token" "load .env / BH_TOKEN" 2
  fi
  export BH_DB_PATH="${BH_DB_PATH:-state/bellhaven.db}"
}

start_app() {
  if (echo > /dev/tcp/127.0.0.1/8000) 2>/dev/null; then
    echo ""
    echo "The review app is already running at http://localhost:8000 (opening it)."
    explorer.exe "http://localhost:8000" >/dev/null 2>&1 || true
    return 0
  fi
  echo ""
  echo "==> Starting the review app. Open http://localhost:8000 in your browser."
  echo "    Press Ctrl+C to stop it."
  "$PY" -m uvicorn bellhaven_sync.app:app --port 8000 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  # 0 = normal stop, 130 = Ctrl+C: both are fine
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 130 ]; then
    fail "Start review app" "$PY -m uvicorn bellhaven_sync.app:app --port 8000" "$rc"
  fi
}

install_shortcut() {
  local desktop_win desktop bat project
  echo "==> Finding your Windows desktop folder"
  desktop_win="$(powershell.exe -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" 2>>"$LOG" | tr -d '\r')"
  if [ -z "$desktop_win" ]; then
    echo "powershell.exe returned no desktop path" | tee -a "$LOG"
    fail "Find Windows desktop" "powershell.exe [Environment]::GetFolderPath('Desktop')" 1
  fi
  desktop="$(wslpath -u "$desktop_win" 2>>"$LOG")" || fail "Convert desktop path" "wslpath -u $desktop_win" 1
  [ -d "$desktop" ] || { echo "Not a folder: $desktop" | tee -a "$LOG"; fail "Check desktop folder" "test -d $desktop" 1; }
  project="$(pwd)"
  bat="$desktop/Bellhaven Review.bat"
  cat > "$bat" <<BAT
@echo off
title Bellhaven CRM review
echo Starting the Bellhaven review app. Keep this window open while you review; close it to stop the app.
start "" /min cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8000"
wsl.exe --cd "$project" -- bash run.sh --app
echo.
echo The review app has stopped. Press any key to close this window.
pause >nul
BAT
  [ -s "$bat" ] || fail "Write launcher" "write $bat" 1
  sed -i 's/$/\r/' "$bat" || fail "Convert launcher line endings" "sed $bat" 1
  echo "    created: $desktop_win\\Bellhaven Review.bat"
  echo "    Double-click it to start the review app; close its window to stop the app."
}

MODE="${1:-all}"

if [ "$MODE" = "--install-shortcut" ]; then
  install_shortcut
  exit 0
fi

if [ "$MODE" = "--app" ]; then
  [ -x "$PY" ] || { echo "No .venv yet: run 'bash run.sh' first." | tee -a "$LOG"; fail "Check venv" "test -x $PY" 1; }
  load_env
  start_app
  exit 0
fi

step "Check Python 3 is installed" python3 --version

if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo ""
  echo "python3-venv is missing; installing it (sudo may ask for your WSL password)."
  step "Update apt package list" sudo apt-get update
  step "Install python3-venv and pip" sudo apt-get install -y python3-venv python3-pip
fi

if [ ! -x .venv/bin/pip ]; then
  step "Remove broken virtual environment (if any)" rm -rf .venv
  step "Create virtual environment" python3 -m venv .venv
fi

step "Upgrade pip" "$PY" -m pip install --upgrade pip
step "Install requirements" "$PY" -m pip install -r requirements.txt
step "Run offline tests" "$PY" -m unittest -v

load_env
step "Check CRM API access with your token" "$PY" -c \
  "from bellhaven_sync.crm import CRMClient; print('API ok:', CRMClient()._req('GET', '/me'))"
step "Run pipeline (creates proposals, never writes to the CRM)" "$PY" -m bellhaven_sync.pipeline run
step "Show proposal status" "$PY" -m bellhaven_sync.pipeline status

echo ""
echo "All steps passed. Log: $LOG"

if [ "$MODE" != "--no-app" ]; then
  start_app
fi
