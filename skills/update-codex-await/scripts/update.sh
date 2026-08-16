#!/usr/bin/env bash
set -euo pipefail

readonly fork_repo="dannyfranca/codex"
readonly patch_branch="await-max-timeout"
readonly repo_dir="${CODEX_AWAIT_REPO:-${HOME}/git/codex-await}"
readonly install_dir="${HOME}/.local/bin"
readonly install_state_dir="${HOME}/.local/share/codex-await"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_commands() {
  local command_name
  for command_name in git gh npm cargo curl tar strip uname find cmp; do
    command -v "$command_name" >/dev/null || die "missing command: $command_name"
  done
  [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] \
    || die "only Linux x86-64 is currently supported"
}

ensure_repo() {
  if [[ ! -d "$repo_dir/.git" ]]; then
    mkdir -p "$(dirname "$repo_dir")"
    if ! gh repo view "$fork_repo" >/dev/null 2>&1; then
      gh repo fork openai/codex --clone=false
    fi
    git clone "git@github.com:${fork_repo}.git" "$repo_dir"
  fi

  if ! git -C "$repo_dir" remote get-url upstream >/dev/null 2>&1; then
    git -C "$repo_dir" remote add upstream https://github.com/openai/codex.git
  fi
}

read_timeout() {
  local config_file="${CODEX_HOME:-${HOME}/.codex}/config.toml"
  local timeout
  timeout=$(sed -n 's/^[[:space:]]*background_terminal_max_timeout[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*$/\1/p' "$config_file" 2>/dev/null | tail -n 1)
  printf 'Configured background terminal timeout: %s ms\n' "${timeout:-default}"
}

find_upstream_code_mode_host() {
  local npm_root="$1"
  local -a candidates
  mapfile -t candidates < <(
    find "$npm_root/@openai/codex/node_modules/@openai" \
      -type f -path '*/vendor/*/bin/codex-code-mode-host' -print 2>/dev/null
  )
  [[ "${#candidates[@]}" -eq 1 ]] \
    || die "expected one official codex-code-mode-host, found ${#candidates[@]}"
  printf '%s\n' "${candidates[0]}"
}

verify_installed_cli() {
  local version="$1"
  [[ "$($install_dir/codex --version)" == "codex-cli $version" ]] \
    || die "installed patched Codex version mismatch"
  [[ "$($install_dir/codex-upstream --version)" == "codex-cli $version" ]] \
    || die "installed official Codex version mismatch"
  [[ -x "$install_dir/codex-code-mode-host" ]] \
    || die "missing installed codex-code-mode-host"
  "$install_dir/codex-code-mode-host" --help >/dev/null \
    || die "installed codex-code-mode-host failed its smoke test"
}

ensure_nextest() {
  if command -v cargo-nextest >/dev/null; then
    return
  fi

  local cargo_bin_dir="${CARGO_HOME:-${HOME}/.cargo}/bin"
  local temp_dir
  temp_dir=$(mktemp -d /tmp/codex-nextest.XXXXXX)
  mkdir -p "$cargo_bin_dir"
  curl --fail --location --silent --show-error https://get.nexte.st/latest/linux \
    -o "$temp_dir/nextest.tar.gz"
  tar -xzf "$temp_dir/nextest.tar.gz" -C "$temp_dir"
  install -m 0755 "$temp_dir/cargo-nextest" "$cargo_bin_dir/cargo-nextest"
  export PATH="$cargo_bin_dir:$PATH"
}

state_file() {
  printf '%s/codex-await-update-state\n' "$(git -C "$repo_dir" rev-parse --absolute-git-dir)"
}

write_state() {
  local version="$1"
  local release_tag="$2"
  local expected_remote_oid="$3"
  local update_branch="$4"
  local state
  state=$(state_file)
  {
    printf '%s\n' "$version"
    printf '%s\n' "$release_tag"
    printf '%s\n' "$expected_remote_oid"
    printf '%s\n' "$update_branch"
  } >"$state"
}

read_state() {
  local state
  state=$(state_file)
  [[ -f "$state" ]] || die "no prepared update; run: $0 prepare"
  mapfile -t update_state <"$state"
  [[ "${#update_state[@]}" -eq 4 ]] || die "invalid update state: $state"
}

restore_generated_lockfile() {
  git -C "$repo_dir" restore --source=HEAD --worktree -- codex-rs/Cargo.lock
}

checkout_patch_branch() {
  local patch_commit="$1"
  if git -C "$repo_dir" show-ref --verify --quiet "refs/heads/${patch_branch}"; then
    git -C "$repo_dir" switch "$patch_branch"
    [[ "$(git -C "$repo_dir" rev-parse HEAD)" == "$patch_commit" ]] \
      || die "local $patch_branch differs from origin; reconcile it manually"
  else
    git -C "$repo_dir" switch --create "$patch_branch" --track "origin/${patch_branch}"
  fi
}

prepare() {
  require_commands
  ensure_repo
  [[ -z "$(git -C "$repo_dir" status --short)" ]] || die "dirty checkout: $repo_dir"
  read_timeout

  local version release_tag remote_ref expected_remote_oid patch_commit patch_parent old_tag
  local update_branch installed_commit upstream_version npm_root upstream_host
  version=$(npm view @openai/codex version)
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "npm returned non-stable version: $version"
  release_tag="rust-v${version}"
  remote_ref="refs/remotes/origin/${patch_branch}"

  git -C "$repo_dir" fetch upstream "refs/tags/${release_tag}:refs/tags/${release_tag}"
  git -C "$repo_dir" fetch origin --prune
  git -C "$repo_dir" rev-parse --verify "${release_tag}^{commit}" >/dev/null \
    || die "missing upstream tag: $release_tag"
  git -C "$repo_dir" rev-parse --verify "${remote_ref}^{commit}" >/dev/null \
    || die "missing fork branch: $patch_branch"

  expected_remote_oid=$(git -C "$repo_dir" rev-parse "${remote_ref}^{commit}")
  patch_commit="$expected_remote_oid"
  patch_parent=$(git -C "$repo_dir" rev-parse "${patch_commit}^")
  old_tag=$(git -C "$repo_dir" tag --points-at "$patch_parent" | sed -n '/^rust-v[0-9][0-9.]*$/p' | head -n 1)
  [[ -n "$old_tag" ]] || die "$patch_branch must contain one patch commit atop a stable release tag"

  installed_commit=$(sed -n '1p' "$install_state_dir/installed-commit" 2>/dev/null || true)
  upstream_version=$("$install_dir/codex-upstream" --version 2>/dev/null || true)
  npm_root=$(npm root -g)
  upstream_host=$(find_upstream_code_mode_host "$npm_root")
  if [[ "$patch_parent" == "$(git -C "$repo_dir" rev-parse "${release_tag}^{commit}")" \
    && "$installed_commit" == "$patch_commit" \
    && -x "$install_dir/codex" \
    && -x "$install_dir/codex-code-mode-host" \
    && -x "$upstream_host" ]] \
    && cmp --silent "$install_dir/codex-code-mode-host" "$upstream_host" \
    && [[ "$upstream_version" == "codex-cli $version" ]]; then
    printf 'UP_TO_DATE: Codex %s, patch %s\n' "$version" "$patch_commit"
    return
  fi

  if [[ "$patch_parent" == "$(git -C "$repo_dir" rev-parse "${release_tag}^{commit}")" ]]; then
    checkout_patch_branch "$patch_commit"
    write_state "$version" "$release_tag" "$expected_remote_oid" ""
    printf 'PREPARED: existing Codex %s patch requires local install\n' "$version"
    return
  fi

  update_branch="await-update-${version}"
  if git -C "$repo_dir" show-ref --verify --quiet "refs/heads/${update_branch}"; then
    die "temporary branch already exists: $update_branch"
  fi

  git -C "$repo_dir" switch --create "$update_branch" "$release_tag"
  write_state "$version" "$release_tag" "$expected_remote_oid" "$update_branch"
  if ! git -C "$repo_dir" cherry-pick "$patch_commit"; then
    printf 'CONFLICT: resolve in %s, preserve the skill contract, then cherry-pick --continue\n' "$repo_dir"
    exit 2
  fi
  printf 'PREPARED: Codex %s on %s\n' "$version" "$update_branch"
}

finish() {
  require_commands
  ensure_repo
  ensure_nextest
  read_state

  local version="${update_state[0]}"
  local release_tag="${update_state[1]}"
  local expected_remote_oid="${update_state[2]}"
  local update_branch="${update_state[3]}"
  local head parent binary npm_root upstream_launcher upstream_host
  local temp_binary temp_host temp_link state_temp
  head=$(git -C "$repo_dir" rev-parse HEAD)
  parent=$(git -C "$repo_dir" rev-parse HEAD^)
  [[ "$parent" == "$(git -C "$repo_dir" rev-parse "${release_tag}^{commit}")" ]] \
    || die "HEAD must be one patch commit atop $release_tag"
  [[ -z "$(git -C "$repo_dir" status --short)" ]] || die "finish requires a clean checkout"

  trap restore_generated_lockfile EXIT
  cargo fmt --manifest-path "$repo_dir/codex-rs/Cargo.toml" --all -- --check
  CARGO_NET_GIT_FETCH_WITH_CLI=true RUST_MIN_STACK=8388608 NEXTEST_PROFILE=local \
    cargo nextest run --manifest-path "$repo_dir/codex-rs/Cargo.toml" --no-fail-fast \
      -p codex-core \
      -E 'test(empty_write_stdin_uses_configured_max_yield_time) | test(nonempty_write_stdin_keeps_interactive_yield_bounds) | test(terminating_during_stdin_poll_returns_exited_response)'
  CARGO_NET_GIT_FETCH_WITH_CLI=true \
    cargo build --manifest-path "$repo_dir/codex-rs/Cargo.toml" --release -p codex-cli --bin codex
  restore_generated_lockfile
  trap - EXIT
  [[ -z "$(git -C "$repo_dir" status --short)" ]] || die "build left unexpected repository changes"

  binary="$repo_dir/codex-rs/target/release/codex"
  [[ "$($binary --version)" == "codex-cli $version" ]] || die "built binary version mismatch"

  if [[ -n "$update_branch" ]]; then
    git -C "$repo_dir" branch --force "$patch_branch" "$head"
    git -C "$repo_dir" push origin "${head}:refs/heads/${patch_branch}" \
      "--force-with-lease=refs/heads/${patch_branch}:${expected_remote_oid}"
    if git -C "$repo_dir" show-ref --verify --quiet "refs/tags/await-v${version}"; then
      [[ "$(git -C "$repo_dir" rev-list -n 1 "await-v${version}")" == "$head" ]] \
        || die "rollback tag await-v${version} points elsewhere"
    else
      git -C "$repo_dir" tag --annotate "await-v${version}" --message "Codex ${version} await patch" "$head"
    fi
    git -C "$repo_dir" push origin "refs/tags/await-v${version}"
  fi

  npm install --global "@openai/codex@${version}"
  mkdir -p "$install_dir" "$install_state_dir"
  npm_root=$(npm root -g)
  upstream_launcher="$npm_root/@openai/codex/bin/codex.js"
  [[ -x "$upstream_launcher" ]] || die "official npm Codex launcher not found: $upstream_launcher"
  upstream_host=$(find_upstream_code_mode_host "$npm_root")
  [[ -x "$upstream_host" ]] || die "official codex-code-mode-host is not executable: $upstream_host"

  temp_binary="$install_dir/.codex-await.new"
  install -m 0755 "$binary" "$temp_binary"
  strip --strip-debug --strip-unneeded "$temp_binary"
  temp_host="$install_dir/.codex-code-mode-host.new"
  install -m 0755 "$upstream_host" "$temp_host"
  temp_link="$install_dir/.codex-upstream.new"
  ln -s "$upstream_launcher" "$temp_link"

  "$temp_host" --help >/dev/null \
    || die "staged codex-code-mode-host failed its smoke test"
  mv "$temp_host" "$install_dir/codex-code-mode-host"
  mv "$temp_binary" "$install_dir/codex"
  mv "$temp_link" "$install_dir/codex-upstream"

  verify_installed_cli "$version"

  state_temp="$install_state_dir/.installed-commit.new"
  printf '%s\n' "$head" >"$state_temp"
  mv "$state_temp" "$install_state_dir/installed-commit"
  rm -f "$(state_file)"

  if [[ -n "$update_branch" ]]; then
    git -C "$repo_dir" switch "$patch_branch"
    git -C "$repo_dir" branch --delete "$update_branch"
  fi

  printf 'INSTALLED: %s (patch %s)\n' "$install_dir/codex" "$head"
}

abort_update() {
  ensure_repo
  if [[ -f "$(state_file)" ]]; then
    read_state
    local update_branch="${update_state[3]}"
    if [[ -f "$(git -C "$repo_dir" rev-parse --absolute-git-dir)/CHERRY_PICK_HEAD" ]]; then
      git -C "$repo_dir" cherry-pick --abort
    fi
    checkout_patch_branch "${update_state[2]}"
    if [[ -n "$update_branch" ]] && git -C "$repo_dir" show-ref --verify --quiet "refs/heads/${update_branch}"; then
      git -C "$repo_dir" branch --delete --force "$update_branch"
    fi
    rm -f "$(state_file)"
  fi
  printf 'ABORTED: installed CLI and patch branch unchanged\n'
}

case "${1:-}" in
  prepare) prepare ;;
  finish) finish ;;
  abort) abort_update ;;
  *) die "usage: $0 {prepare|finish|abort}" ;;
esac
