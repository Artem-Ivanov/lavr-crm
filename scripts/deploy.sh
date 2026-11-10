#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
  echo 'Usage: scripts/deploy.sh [FULL_COMMIT_SHA]' >&2
  exit 2
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
commit=${1:-$(git -C "$project_dir" rev-parse HEAD)}
case "$commit" in
  ''|*[!0-9a-f]*) echo 'Provide a full 40-character lowercase commit SHA.' >&2; exit 2 ;;
esac
if [ "${#commit}" -ne 40 ]; then
  echo 'Provide a full 40-character lowercase commit SHA.' >&2
  exit 2
fi

exec ssh -F /dev/null -i "$HOME/.ssh/lavr_crm" \
  -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$HOME/.ssh/lavr_crm_known_hosts" \
  -o UpdateHostKeys=no -o ConnectTimeout=30 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  deploy@157.228.139.156 \
  "python3 /home/deploy/lavr-crm-deploy/deploy.py $commit"
