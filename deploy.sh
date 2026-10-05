#!/usr/bin/env bash
#
# Deploy Watchlog on the Pi. Run it there:
#
#     cd ~/Projects/watchlog && ./deploy.sh
#
# The steps are not hard, but they are easy to half-remember, and one of them
# fails silently. The webhook and the Apple TV listener are long-running
# processes that import the code once at startup: a deploy that does not restart
# them leaves them publishing the old page forever, and nothing reports an
# error, because nothing went wrong. That happened on 2026-09-27 and took an
# evening to notice.
#
#   --no-pull    deploy the working tree as it is, without fetching
#   --no-tests   skip the suites (they take about a minute)
#
set -euo pipefail

cd "$(dirname "$0")"
PYTHON="./venv/bin/python"
SERVICES=(watchlog-webhook watchlog-appletv watchlog-admin)

pull=yes tests=yes
for arg in "$@"; do
    case "$arg" in
        --no-pull)  pull=no ;;
        --no-tests) tests=no ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

test -x "$PYTHON" || { echo "no venv here -- is this the Pi?" >&2; exit 1; }

if [ "$pull" = yes ]; then
    say "Pulling"
    before="$(git hash-object deploy.sh)"
    git pull --ff-only
    # Bash keeps running the copy of this script it started with, so a pull that
    # changed deploy.sh would otherwise be deployed by the old steps. Start over
    # as the new script, without pulling again.
    if [ "$(git hash-object deploy.sh)" != "$before" ]; then
        echo "  deploy.sh changed; restarting as the new version"
        exec ./deploy.sh --no-pull "$@"
    fi
fi
git --no-pager log --oneline -1

# Before the tests, which import everything: a dependency added to
# requirements.txt is installed before anything tries to use it, and a missing
# one fails the suites rather than the services after their restart.
say "Dependencies"
./venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
echo "  up to date"

# Tests before anything is restarted, so a broken commit cannot take the
# sensors down with it.
if [ "$tests" = yes ]; then
    say "Tests"
    failed=0
    for suite in tests/test_*.py; do
        name="$(basename "$suite" .py)"
        printf '  %-16s ' "$name"
        if out="$($PYTHON -m "tests.$name" 2>&1)"; then
            echo "${out##*$'\n'}"
        else
            echo "FAILED"
            echo "$out" | grep -E '^\s+FAIL' || echo "$out" | tail -5
            failed=1
        fi
    done
    [ "$failed" = 0 ] || { echo; echo "not deploying a failing tree" >&2; exit 1; }
fi

# Unit files live in the repo but run from /etc. Copy only what actually
# differs, so daemon-reload and the enable below are no-ops on a normal deploy.
say "Units"
changed=0
for unit in systemd/*.service systemd/*.timer; do
    name="$(basename "$unit")"
    if ! sudo cmp -s "$unit" "/etc/systemd/system/$name"; then
        sudo cp "$unit" "/etc/systemd/system/$name"
        echo "  updated $name"
        changed=1
    fi
done
if [ "$changed" = 1 ]; then
    sudo systemctl daemon-reload
    for timer in systemd/*.timer; do
        sudo systemctl enable --now "$(basename "$timer")" >/dev/null
    done
    echo "  reloaded systemd"
else
    echo "  unchanged"
fi

# The step that is easy to forget and impossible to notice.
say "Restarting the long-running services"
sudo systemctl restart "${SERVICES[@]}"
sleep 5
for unit in "${SERVICES[@]}"; do
    printf '  %-20s %s\n' "$unit" "$(systemctl is-active "$unit")"
done

say "Publishing"
$PYTHON - <<'PY'
from watchlog import render, publish
path = render.write_output()
publish.push()
print(f"  rendered and published {path}")
PY

say "Health"
token="$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2-)"
curl -s "http://127.0.0.1:$(grep '^ADMIN_PORT=' .env | cut -d= -f2- || echo 8421)/?token=$token" \
    | grep -A3 'class="health' | sed 's/<[^>]*>//g' | grep -v '^--$' | grep -v '^\s*$' \
    | sed 's/^ */  /'
