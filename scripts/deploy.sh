#!/bin/bash
# Deploys the working tree to the Cerbo and restarts the service.
#
#   scripts/deploy.sh root@<cerbo-ip> ["ssh -i ~/.ssh/some-key"]   # from the repository root
#
# One command covers every case: it ships the code and QML, ships
# build/wasm/venus-webassembly.zip when one has been built (see scripts/README.md), then runs
# enable.sh on the device — which installs QML/WASM only if their content changed — and
# restarts the service. A WASM change needs no extra deploy steps, only the rebuild first.
#
# NEVER add --delete: the device directory holds files that exist only there —
# config.ini, state.json (written by the running service), wasm/venus-webassembly.zip,
# and the old stack's files under old-stack-backup/.

set -e
cd "$(dirname "$0")/.."

HOST=${1:?usage: scripts/deploy.sh user@cerbo-host optional-ssh-command}
SSH_CMD=${2:-ssh}
APP_DIR=/data/apps/dbus-battery-bank

echo "Running tests..."
.venv/bin/python -m pytest -q

echo "Syncing code to $HOST:$APP_DIR ..."
rsync -arz -e "$SSH_CMD" \
    --exclude .git --exclude .venv --exclude build --exclude .claude \
    --exclude __pycache__ --exclude .pytest_cache --exclude .DS_Store \
    ./ "$HOST:$APP_DIR/"

if [ -f build/wasm/venus-webassembly.zip ]; then
    echo "Syncing the browser WASM bundle..."
    rsync -az -e "$SSH_CMD" build/wasm/venus-webassembly.zip "$HOST:$APP_DIR/wasm/"
fi

echo "Applying GUI changes (if any) and restarting the service..."
$SSH_CMD "$HOST" "bash $APP_DIR/enable.sh && svc -t /service/dbus-battery-bank"

echo "Deployed. Watch the log:"
echo "  $SSH_CMD $HOST 'tail -f /var/log/dbus-battery-bank/current | tai64nlocal'"
