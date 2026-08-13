#!/usr/bin/env bash
#
# All-in-one deployment for the IoT sensor server: git, images, build, start,
# health check, TimescaleDB extension.
#
# Migrations, collectstatic and compilemessages are run by web/entrypoint.sh when the
# `web` container starts, so this script does not repeat them.
#
#   ./deploy.sh                    full deployment (pull + build + up + wait)
#   ./deploy.sh --no-pull          fetch nothing: neither the git update nor the images
#   ./deploy.sh --dry-run          print what would run, execute nothing
#   ./deploy.sh check              only look for a pending update, change nothing
#   ./deploy.sh status             service status
#   ./deploy.sh logs [service]     follow the logs
#   ./deploy.sh stop               stop everything
#   ./deploy.sh restart            restart without rebuilding
#   ./deploy.sh timescale-update   update the TimescaleDB extension alone
#   ./deploy.sh superuser          re-apply the .env superuser (idempotent)
#   ./deploy.sh shell              Django shell inside the web container
#   ./deploy.sh help               this help
#
# `check` (alias `--check`) exits with a status meant to be scripted:
#   0   already up to date
#   10  an update is pending
#   1   cannot tell (not a git repository, no upstream, fetch failed)
#
# WHY THIS FILE IS NAMED deploy.sh AND COMMITTED EXECUTABLE. The homelab console
# offers a per-stack "deploy" button, and the machine resolves what to run itself:
# `home-server-stacks/_common/ansible/roles/report/files/homelab-probe` reports a
# deployment script only when it is one of `deploy.sh`, `deploy`, `update.sh`, sits in
# the compose project's working directory, carries the executable bit, and is tracked
# by git. The wake agent then runs it with `timeout 3600 ./deploy.sh`, as the owner of
# the checkout, **with no arguments and with stdin on /dev/null**. Hence: bare
# `./deploy.sh` must be the full deployment, it must never ask a question, and every
# failure must be an exit code — a script that prompts would hang, and one that hides
# a failed fetch would answer "deployed" to a button that deployed nothing.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Never let git open a credential or host-key prompt: this runs with stdin on
# /dev/null under the wake agent, where a prompt is an hour-long hang instead of an
# error message.
export GIT_TERMINAL_PROMPT=0

MANAGE=(python manage.py)
# Every service here carries a healthcheck, dependencies first so the first failure
# reported is the cause rather than a consequence.
WATCHED_SERVICES=(timescaledb redis mosquitto web nginx)
HEALTH_TIMEOUT=180

PULL=1
DRY_RUN=0

# --- Output ------------------------------------------------------------------

if [ -t 1 ]; then
    C_STEP=$'\033[1;34m'; C_OK=$'\033[0;32m'; C_WARN=$'\033[0;33m'
    C_ERR=$'\033[0;31m'; C_END=$'\033[0m'
else
    C_STEP=""; C_OK=""; C_WARN=""; C_ERR=""; C_END=""
fi

step() { printf '\n%s==> %s%s\n' "$C_STEP" "$1" "$C_END"; }
ok()   { printf '%s  ✓ %s%s\n' "$C_OK" "$1" "$C_END"; }
warn() { printf '%s  ! %s%s\n' "$C_WARN" "$1" "$C_END"; }
fail() { printf '%s  ✗ %s%s\n' "$C_ERR" "$1" "$C_END" >&2; exit 1; }

# Run a command, or just print it in --dry-run mode.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] %s\n' "$*"
        return 0
    fi
    "$@"
}

# The help text is the file header: one place to keep up to date.
usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

# --- Preflight checks --------------------------------------------------------

check_tools() {
    command -v docker >/dev/null 2>&1 || fail "docker not found."
    docker compose version >/dev/null 2>&1 \
        || fail "the 'docker compose' plugin is missing (docker-compose v1 is not supported)."
    docker info >/dev/null 2>&1 \
        || fail "the docker daemon is not responding: is it running, and is your account in the docker group?"
    ok "docker and docker compose available"
}

check_env() {
    if [ ! -f .env ]; then
        warn ".env missing, copying .env.example"
        cp .env.example .env
        fail "edit .env (at least DJANGO_SECRET_KEY, POSTGRES_PASSWORD, MQTT_PASSWORD) then run again."
    fi
    # Leftover example values are the number one cause of a failed deployment.
    local leftovers
    leftovers="$(grep -cE 'change-?me' .env || true)"
    if [ "$leftovers" -gt 0 ]; then
        warn "$leftovers example value(s) still in .env (change-me/changeme)"
    fi
    ok ".env present"
}

# Read a variable from .env, falling back to the given default.
env_value() {
    local key="$1" default="${2:-}" line
    line="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 || true)"
    if [ -z "$line" ]; then
        printf '%s' "$default"
    else
        printf '%s' "${line#*=}"
    fi
}

# The bind-mounted directories must exist *before* the first `up`: otherwise Docker
# creates them as root and the owner can no longer write to or clean them. The
# PostgreSQL directory is deliberately left to Docker, since initdb requires owning it.
prepare_directories() {
    local data dir
    data="$(env_value DATA_DIR ./data)"
    for dir in mosquitto redis logs media; do
        if [ ! -d "$data/$dir" ]; then
            run mkdir -p "$data/$dir"
            ok "created $data/$dir"
        fi
    done
}

# --- Steps -------------------------------------------------------------------

update_repository() {
    step "Updating the repository"
    if [ ! -d .git ]; then
        warn "not a git repository, skipping the update"
        return 0
    fi
    # -uno: tracked files only. DATA_DIR defaults to ./data, inside the checkout, and
    # the containers write there — counting untracked files would call every server
    # dirty and refuse to ever deploy.
    if [ -n "$(git status --porcelain -uno)" ]; then
        git status --short -uno
        fail "the repository has local changes: commit them, stash them, or use --no-pull."
    fi
    local before
    before="$(git rev-parse HEAD)"
    # --ff-only: a checkout carrying local commits must stop here visibly rather than
    # have them merged away by a button nobody is watching.
    run git pull --ff-only
    if [ "$DRY_RUN" -eq 0 ]; then
        local after
        after="$(git rev-parse HEAD)"
        if [ "$before" = "$after" ]; then
            ok "already up to date ($(git rev-parse --short HEAD))"
        else
            ok "updated: $(git rev-parse --short "$before") -> $(git rev-parse --short "$after")"
            git --no-pager log --oneline "$before..$after"
        fi
    fi
}

# Report whether the remote is ahead, and touch nothing else. `git fetch` only writes
# remote-tracking refs, never the working tree, so this is safe to run on a schedule.
check_update() {
    step "Checking for a pending update"
    [ -d .git ] || fail "not a git repository, cannot check for updates."

    local branch upstream
    branch="$(git rev-parse --abbrev-ref HEAD)"
    upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
    [ -n "$upstream" ] || fail "branch '$branch' tracks no upstream: nothing to compare against."

    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] git fetch --quiet\n'
        warn "comparing against the remote refs already on disk"
    else
        git fetch --quiet || fail "git fetch failed: is the remote reachable?"
    fi

    local behind ahead
    read -r behind ahead <<< "$(git rev-list --left-right --count "${upstream}...HEAD")"
    ok "branch '$branch' tracking '$upstream'"

    # A dirty tree does not block this check, but it will block the deployment.
    if [ -n "$(git status --porcelain -uno)" ]; then
        warn "local changes present: a deployment will need --no-pull"
    fi

    if [ "$ahead" -gt 0 ] && [ "$behind" -eq 0 ]; then
        ok "up to date ($ahead local commit(s) not pushed)"
        return 0
    fi

    if [ "$behind" -eq 0 ]; then
        ok "up to date ($(git rev-parse --short HEAD))"
        return 0
    fi

    if [ "$ahead" -gt 0 ]; then
        warn "branches have diverged: $behind incoming, $ahead local commit(s)"
    else
        warn "$behind commit(s) pending"
    fi
    git --no-pager log --oneline "HEAD..${upstream}"
    printf '\n     deploy with: ./deploy.sh\n'
    exit 10
}

# Refresh the images that come from a registry. The tags in docker-compose.yml are
# pinned to an exact version, so this only matters when a pin changed in the commits
# just pulled — but that is precisely the deployment where forgetting it means running
# the old image while git says otherwise. The Dockerfile's base image needs its own
# pull: `docker compose pull` skips the buildable services, and a year-old FROM is just
# as stale as a year-old broker. The tag is read from the Dockerfile rather than
# repeated here.
#
# A registry we cannot reach is a warning, not a failure: the images already on disk
# are enough to deploy, and aborting would leave the update half done.
pull_images() {
    step "Refreshing the images"
    local stale=0 base
    run docker compose pull --ignore-buildable --quiet || stale=1
    base="$(awk '/^FROM/ { print $2; exit }' web/Dockerfile 2>/dev/null || true)"
    if [ -n "$base" ]; then
        run docker pull --quiet "$base" || stale=1
    fi
    if [ "$stale" -eq 1 ]; then
        warn "some images could not be refreshed, keeping the ones already on disk"
    else
        ok "images up to date"
    fi
}

build_image() {
    step "Building the web image"
    run docker compose build web
    ok "image built"
}

start_services() {
    step "Starting the services"
    # --remove-orphans is what makes a deletion delete: `up -d` alone leaves a
    # container whose service is gone from the compose file running for ever. It only
    # touches containers labelled as belonging to this compose project.
    run docker compose up -d --remove-orphans
    ok "services started"
}

# Wait for a service to become healthy, or fail showing its last log lines.
wait_for_service() {
    local service="$1" elapsed=0 cid health status
    cid="$(docker compose ps -q "$service" 2>/dev/null || true)"
    [ -n "$cid" ] || fail "service $service did not start."

    while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
        status="$(docker inspect -f '{{.State.Status}}' "$cid")"
        [ "$status" = "running" ] || break
        health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid")"
        case "$health" in
            healthy|no-healthcheck) ok "$service: $health"; return 0 ;;
            unhealthy) break ;;
        esac
        sleep 3
        elapsed=$((elapsed + 3))
        printf '\r  ... %s: %s (%ss)' "$service" "$health" "$elapsed"
    done
    printf '\n'
    docker compose logs --tail 40 "$service" || true
    fail "$service did not become operational within ${HEALTH_TIMEOUT}s."
}

check_health() {
    step "Checking health"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] would wait for: %s\n' "${WATCHED_SERVICES[*]}"
        return 0
    fi
    local service
    for service in "${WATCHED_SERVICES[@]}"; do
        wait_for_service "$service"
    done
}

# The TimescaleDB image ships the extension, but the database keeps the version it was
# created with: pulling a newer image updates neither, and the gap widens silently
# until a continuous aggregate or a compression policy misbehaves for a reason fixed
# releases ago. `ALTER EXTENSION ... UPDATE` is a NOTICE and a success when there is
# nothing to do, so this can run on every deployment; it must be the **first statement
# of a fresh session**, which is why it gets its own `psql -c`.
#
# A failure here is a warning, not a failed deployment: the services are already up and
# serving on the older extension, and the fix is a decision (read the release notes,
# take a backup first) rather than something to retry blindly.
update_timescaledb_extension() {
    step "TimescaleDB extension"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  [dry-run] ALTER EXTENSION timescaledb UPDATE\n'
        return 0
    fi
    local user db before after
    user="$(env_value POSTGRES_USER sensors)"
    db="$(env_value POSTGRES_DB sensors)"

    before="$(docker compose exec -T timescaledb psql -U "$user" -d "$db" -Atc \
        "select extversion from pg_extension where extname='timescaledb'" 2>/dev/null || true)"
    if ! docker compose exec -T timescaledb psql -U "$user" -d "$db" -q \
        -c 'ALTER EXTENSION timescaledb UPDATE;' >/dev/null 2>&1; then
        warn "the extension update failed (still on ${before:-unknown}); run it by hand:"
        printf "     docker compose exec timescaledb psql -U %s -d %s -c 'ALTER EXTENSION timescaledb UPDATE;'\n" \
            "$user" "$db"
        return 0
    fi
    after="$(docker compose exec -T timescaledb psql -U "$user" -d "$db" -Atc \
        "select extversion from pg_extension where extname='timescaledb'" 2>/dev/null || true)"
    if [ "$before" = "$after" ]; then
        ok "extension up to date (${after:-unknown})"
    else
        ok "extension updated: ${before:-unknown} -> ${after:-unknown}"
        # The library is loaded per backend, so the sessions opened before the update
        # keep the old one. Only `web` holds long-lived connections here.
        run docker compose restart web
        ok "web restarted so its connections pick up the new library"
        wait_for_service web
    fi
}

summary() {
    step "Service status"
    docker compose ps
    local port mqtt
    port="$(env_value WEB_EXPOSED_PORT 8000)"
    mqtt="$(env_value MQTT_EXPOSED_PORT 1883)"
    printf '\n'
    ok "dashboard available at http://localhost:${port}/"
    printf '     health: http://localhost:%s/healthz/\n' "$port"
    printf '     mqtt:   localhost:%s\n' "$mqtt"
    printf '     logs:   ./deploy.sh logs\n'
}

deploy() {
    step "Deploying the sensor server"
    [ "$DRY_RUN" -eq 1 ] && warn "--dry-run mode: no command is executed"
    check_tools
    check_env
    prepare_directories
    [ "$PULL" -eq 1 ] && update_repository
    [ "$PULL" -eq 1 ] && pull_images
    build_image
    start_services
    check_health
    update_timescaledb_extension
    [ "$DRY_RUN" -eq 0 ] && summary
    return 0
}

# --- Entry point -------------------------------------------------------------

COMMAND="deploy"
ARGUMENT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-pull)  PULL=0 ;;
        --dry-run)  DRY_RUN=1 ;;
        -h|--help|help) usage; exit 0 ;;
        --check)    COMMAND="check" ;;
        deploy|check|status|logs|stop|restart|timescale-update|superuser|shell)
            COMMAND="$1"
            if [ $# -gt 1 ] && [[ "$2" != -* ]]; then
                ARGUMENT="$2"
                shift
            fi
            ;;
        *) fail "unknown argument: $1 (see ./deploy.sh help)" ;;
    esac
    shift
done

case "$COMMAND" in
    deploy)           deploy ;;
    check)            check_update ;;
    status)           check_tools; docker compose ps ;;
    logs)             docker compose logs -f ${ARGUMENT:+"$ARGUMENT"} ;;
    stop)             step "Stopping"; docker compose down; ok "services stopped" ;;
    restart)          step "Restarting"; docker compose restart; check_health; summary ;;
    timescale-update) check_tools; check_env; update_timescaledb_extension ;;
    superuser)        docker compose exec web "${MANAGE[@]}" ensure_superuser ;;
    shell)            docker compose exec web "${MANAGE[@]}" shell ;;
esac
