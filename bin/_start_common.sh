#!/bin/bash
#
# Shared start/stop mechanics for bin/start.sh, bin/daemon.sh, bin/local-start.sh
# and bin/local-stop.sh.
#
# The four scripts answer different questions for a user: bootstrap the
# environment, control a background service, run one pass locally, stop what is
# running. The mechanics of putting the pipeline in the background are one thing,
# and they had drifted apart: two interpreters (python3.14 and .venv/bin/python),
# two PID files (kdr.pid and logs/kdr.pid), two argument parsers, and two
# pre-flight imports. A run started by one script then stopped cleanly only when
# a different script was told to stop it. Sourcing this file is what keeps them
# from drifting again.
#
# Every script must cd to the project root before calling these, because the
# pipeline is started as `src/scraper.py` relative to that root.
#
# Source it, then use:
#   kdr_parse_args "$@"     reads the flags the entry scripts expose
#   kdr_running_pid         prints the live PID the PID file names, if any
#   kdr_check_interpreter   fails unless the project's own .venv can run it
#   kdr_preflight_import    fails unless src.scraper imports, before backgrounding
#   kdr_start_background [log]  backgrounds the pipeline and writes the PID file
#   kdr_stop_running        TERM-signals that PID, then KILLs if it ignores it
#
# The library sets:
#   KDR_ROOT, KDR_PID_FILE, KDR_LOG, PYTHON_BIN
#   PARSE_DEBUG, PARSE_DRY_RUN, KDR_ARGS, KDR_STOP, KDR_STATUS, KDR_HELP

KDR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The one PID file every entry point reads and writes. It used to be two:
# start.sh and daemon.sh wrote logs/kdr.pid while local-start.sh wrote kdr.pid
# in the repo root, so a run started by one script was stopped cleanly only
# when a different script was told to stop it.
KDR_PID_FILE="${KDR_ROOT}/logs/kdr.pid"

# The one log the background pipeline writes to.
KDR_LOG="${KDR_ROOT}/logs/kdr.log"

# The one interpreter every entry point runs: the project's own virtualenv,
# which is the only place alpaca, fastapi, plotly and psycopg2 are installed.
PYTHON_BIN="${KDR_ROOT}/.venv/bin/python"

# Read the flags the entry scripts expose. `--debug` and `--dry-run` are passed
# through to src/scraper.py; `--stop` and `--status` are handled by the caller,
# which is why the parse result is split: KDR_ARGS is what the pipeline runs
# with, KDR_STOP/KDR_STATUS/KDR_HELP say what the caller was asked to do.
kdr_parse_args() {
    PARSE_DEBUG=""
    PARSE_DRY_RUN=""
    KDR_ARGS=()
    KDR_STOP=0
    KDR_STATUS=0
    KDR_HELP=0

    local help=false
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --debug|-d)
                PARSE_DEBUG="--debug"
                shift
                ;;
            --dry-run)
                PARSE_DRY_RUN="--dry-run"
                shift
                ;;
            --status)
                KDR_STATUS=1
                shift
                ;;
            --stop)
                KDR_STOP=1
                shift
                ;;
            --help|-h)
                KDR_HELP=1
                shift
                ;;
            *)
                PARSE_ARGS+=("$1")
                shift
                ;;
        esac
    done

    if [ -n "$PARSE_DEBUG" ]; then
        KDR_ARGS+=("$PARSE_DEBUG")
    fi
    if [ -n "$PARSE_DRY_RUN" ]; then
        KDR_ARGS+=("$PARSE_DRY_RUN")
    fi

    if [[ "$KDR_STOP" == 1 || "$KDR_STATUS" == 1 || "$KDR_HELP" == 1 ]]; then
        return 0
    fi

    if [ "${#PARSE_ARGS[@]}" -gt 0 ]; then
        echo "Unknown option: ${PARSE_ARGS[*]}" >&2
        return 1
    fi

    return 0
}

# Echo the PID the PID file names when that process is alive. A PID file whose
# process is gone is stale and is removed here, so no caller has to remember to.
kdr_running_pid() {
    local pid=""
    if [ -f "$KDR_PID_FILE" ]; then
        pid=$(cat "$KDR_PID_FILE" 2>/dev/null)
    fi
    if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
        echo "$pid"
        return 0
    fi
    [ -f "$KDR_PID_FILE" ] && rm -f "$KDR_PID_FILE"
    return 1
}

kdr_check_interpreter() {
    if [ ! -x "$PYTHON_BIN" ]; then
        echo "No ${PYTHON_BIN} under ${KDR_ROOT}: run ./bin/start.sh to create the virtualenv." >&2
        return 1
    fi
}

# Fail visibly here rather than swallowing the error in the background: an
# import failure (missing .env, missing dependency) would otherwise be lost to
# the rotating log, which is not where a person looks when nothing starts.
kdr_preflight_import() {
    local import_error
    if ! import_error=$(PYTHONPATH="${PYTHONPATH:-}:${KDR_ROOT}" "$PYTHON_BIN" -c "import src.scraper" 2>&1); then
        echo "src/scraper.py cannot be imported:" >&2
        echo "$import_error" >&2
        return 1
    fi
}

# stdout and stderr go to /dev/null on purpose: src/scraper.py writes every log
# record to the rotating logs/kdr.log itself, and a second copy of the same
# stream is what grew logs/ past hundreds of megabytes.
kdr_start_background() {
    local log_file="${1:-$KDR_LOG}"
    mkdir -p "$(dirname "$log_file")"
    nohup "$PYTHON_BIN" src/scraper.py "${KDR_ARGS[@]}" >"$log_file" 2>&1 &
    KDR_PID=$!
    echo "$KDR_PID" > "$KDR_PID_FILE"
    sleep 1
    if ! ps -p "$KDR_PID" > /dev/null 2>&1; then
        echo "failed to start"
        return 1
    fi
    echo "Process started with PID $KDR_PID"
}

# Ask for a graceful shutdown first, which is what lets the pipeline finish its
# DB writes and stop the dashboard; only escalate when it does not answer.
kdr_stop_running() {
    local pid
    pid=$(kdr_running_pid) || return 1
    local timeout=30
    local count=0

    kill -TERM "$pid" 2>/dev/null
    while ps -p "$pid" > /dev/null 2>&1; do
        sleep 1
        count=$((count + 1))
        if [ "$count" -ge "$timeout" ]; then
            echo "Process did not terminate gracefully, sending SIGKILL"
            kill -KILL "$pid" 2>/dev/null
            sleep 2
            break
        fi
    done
    rm -f "$KDR_PID_FILE" 2>/dev/null
    echo "$pid"
}
