#!/bin/sh
# Runs on GitHub Actions: the paper engine until $1 (IST, HH:MM), saving its state to the
# repo's "data" branch every 5 minutes (one commit, force-pushed, so the history stays small).
# The dashboard on GitHub Pages reads data/dashboard.json from there.
set -u
UNTIL="$1"
ROOT="$(pwd)"
cd "$ROOT/trading"

# 1. restore the latest account from the data branch (the first run uses the one in the repo)
if git -C "$ROOT" fetch -q origin data 2>/dev/null; then
  git -C "$ROOT" show origin/data:state.json > trader/paper/state.json.new && mv trader/paper/state.json.new trader/paper/state.json
  echo "Restored the account from the data branch."
else
  echo "No data branch yet: starting from trading/trader/paper/state.json."
fi

save() {
  python3 -I "$ROOT/trader-paper/dashboard.py" --state trader/paper/state.json --config trader/config.json \
    --bundle /tmp/dashboard.json || return 0
  out="$(mktemp -d)"
  cp trader/paper/state.json "$out/state.json"
  cp /tmp/dashboard.json "$out/dashboard.json"
  (
    cd "$out" && git init -q && git checkout -q -b data && git add state.json dashboard.json &&
    git -c user.name="paper-desk-bot" -c user.email="paper-desk-bot@users.noreply.github.com" \
      commit -qm "paper desk state $(date -u +%Y-%m-%dT%H:%MZ)" &&
    git push -qf "${PUSH_URL:-https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git}" data
  ) && echo "Saved the state at $(date -u +%H:%M) UTC."
  rm -rf "$out"
}

# 2. the engine, in the background; save every 5 minutes while it runs, and once more at the end
python3 -I "$ROOT/trader-paper/paper.py" run --until "$UNTIL" &
ENGINE=$!
LAST=$(date +%s)
while kill -0 "$ENGINE" 2>/dev/null; do
  sleep 10
  if [ $(( $(date +%s) - LAST )) -ge 300 ]; then
    save
    LAST=$(date +%s)
  fi
done
wait "$ENGINE"
CODE=$?
save
exit $CODE
