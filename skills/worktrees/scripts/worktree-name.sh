#!/usr/bin/env bash
set -euo pipefail

slug="${1:?Usage: worktree-name.sh <task-slug>}"
repo_name="$(basename "$(git rev-parse --show-toplevel)")"

printf '%s-%s-%s\n' "$(date +%Y%m%d-%H%M)" "$repo_name" "$slug"
