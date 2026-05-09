#!/usr/bin/env bash
# scripts/run_scraper.sh
#
# Cron entrypoint for any cli.py subcommand. Loads .env, activates the
# project venv if one exists, runs the command, and appends both stdout
# and stderr (with timestamps) to a per-scraper log file.
#
# Usage from crontab (paths are absolute on purpose so cron's empty PATH
# doesn't bite us):
#
#   0 */6 * * *  /opt/hltv-parser-ver1/scripts/run_scraper.sh pandascore run
#   0 2  * * *   /opt/hltv-parser-ver1/scripts/run_scraper.sh steam refresh
#
# Logs land in /opt/hltv-parser-ver1/logs/<scraper>.log — rotate with
# logrotate or just `truncate` periodically. Each line is prefixed
# with a UTC timestamp so the file is grep-friendly.

set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

# Load .env if present. Uses `set -a` so every assignment is exported
# without each line needing `export`. Lines starting with `#` are skipped.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Activate venv if there's one. Common layouts: .venv (uv/poetry/pip)
# or venv (vanilla). Skip silently if neither exists — the system
# python is fine on a one-purpose droplet.
if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
elif [[ -f venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

mkdir -p logs

# First positional arg is the scraper name; that drives the log file
# choice. Everything else is forwarded verbatim to cli.py.
scraper="${1:?usage: run_scraper.sh <scraper> [cli args...]}"
shift
log_file="logs/${scraper}.log"

# `ts` would be nicer but isn't installed everywhere; awk is universal.
{
    printf '\n=== run start: %s args=%q ===\n' "$(date -u +%FT%TZ)" "$*"
    python cli.py "$scraper" "$@" 2>&1
    rc=$?
    printf '=== run end:   %s rc=%d ===\n' "$(date -u +%FT%TZ)" "$rc"
    exit "$rc"
} | awk '{ printf "%s %s\n", strftime("%FT%TZ", systime(), 1), $0; fflush() }' \
  >> "$log_file"
