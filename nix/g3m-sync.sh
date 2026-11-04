set -euo pipefail

REMOTE="${G3M_REMOTE:-upstream}"
PUSH_REMOTE="${G3M_PUSH_REMOTE:-origin}"
ORIGINAL="${G3M_ORIGINAL:-https://github.com/y114git/G3M.git}"
MIRROR="${G3M_MIRROR:-main}"
WORK="${G3M_WORK:-python-nix}"

FORK_ONLY=(nix flake.nix flake.lock .gitattributes)
MIXED=(README.md CHANGELOG.md .gitignore)

MODE=sync YES=0 FAST=0 PUSH=0 MIRROR_FIRST=0
for arg in "$@"; do
  case "$arg" in
    --finish) MODE=finish ;;
    --abort) MODE=abort ;;
    --repair) MODE=repair ;;
    --yes | -y) YES=1 ;;
    --fast) FAST=1 ;;
    --push) PUSH=1 ;;
    --mirror) MIRROR_FIRST=1 ;;
    -h | --help)
      cat <<'USAGE'
g3m-sync            fetch, preview, merge on a separate branch, checks, fast-forward python-nix
g3m-sync --mirror   update the fork's main from the original first
g3m-sync --repair   reset upstream-owned files to upstream (fixes an old bad merge)
g3m-sync --finish   finish after resolving README/CHANGELOG by hand
g3m-sync --abort    cancel, python-nix is left untouched
flags: --yes  --fast (skip nix flake check)  --push
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 64 ;;
  esac
done

EXCL=()
for p in "${FORK_ONLY[@]}" "${MIXED[@]}"; do EXCL+=(":(exclude)$p"); done

info() { echo "▸ $*"; }
warn() { echo "⚠ $*" >&2; }
die() { echo "✗ $*" >&2; exit 1; }

confirm() {
  [[ $YES == 1 ]] && return 0
  [[ -t 0 ]] || die "confirmation required: run from a terminal or pass --yes"
  local answer
  read -r -p "$1 [y/N] " answer
  [[ $answer == y || $answer == Y ]]
}

in_merge() { git rev-parse -q --verify MERGE_HEAD >/dev/null; }

unmerged() { git diff --name-only --diff-filter=U; }

with_default_merge() {
  local attrs bak="" rc=0
  attrs=$(git rev-parse --git-path info/attributes)
  mkdir -p "$(dirname "$attrs")"
  if [[ -f $attrs ]]; then
    bak=$(mktemp)
    cp "$attrs" "$bak"
  fi
  printf '* merge=text\n' >>"$attrs"
  "$@" >/dev/null 2>&1 || rc=$?
  if [[ -n $bak ]]; then
    cp "$bak" "$attrs"
    rm -f "$bak"
  else
    rm -f "$attrs"
  fi
  return "$rc"
}

adopt_upstream_files() {
  local stale=()
  git checkout "$1" -- . "${EXCL[@]}"
  mapfile -t stale < <(comm -23 \
    <(git -c core.quotePath=false ls-files -- . "${EXCL[@]}" | sort -u) \
    <(git -c core.quotePath=false ls-tree -r --name-only "$1" | sort -u))
  if ((${#stale[@]})); then
    info "removing files that do not exist upstream:"
    printf '    %s\n' "${stale[@]}"
    git rm -q -f -- "${stale[@]}"
  fi
}

run_checks() {
  info "check: conflict markers"
  if git grep -nE '^(<<<<<<< |>>>>>>> )' -- . >/dev/null; then
    git grep -nE '^(<<<<<<< |>>>>>>> )' -- . | head
    die "conflict markers are left in files"
  fi
  if command -v ruff >/dev/null; then
    info "check: syntax (ruff E9)"
    ruff check --select E9 --no-cache --quiet . || die "syntax errors"
  else
    warn "ruff not found, syntax not checked"
  fi
}

land() {
  local branch="$1"
  if [[ $FAST == 0 ]]; then
    info "nix flake check -L (slow; use --fast to skip)"
    nix flake check -L || die "nix flake check failed; $WORK untouched, branch $branch kept"
  fi
  git checkout -q "$WORK"
  git merge -q --ff-only "$branch"
  git branch -q -d "$branch"
  info "done: $WORK is now at $(git rev-parse --short HEAD)"
  if [[ $PUSH == 1 ]]; then
    git push
  else
    info "if everything looks fine: git push"
  fi
}

cmd_mirror() {
  info "fork main <- original ($ORIGINAL)"
  local tip cur
  git fetch -q --no-tags "$ORIGINAL" "$MIRROR"
  tip=$(git rev-parse FETCH_HEAD)
  git fetch -q --prune "$REMOTE"
  cur=$(git rev-parse -q --verify "refs/remotes/$REMOTE/$MIRROR" || true)
  if [[ $cur == "$tip" ]]; then
    info "main already matches the original (${tip:0:7})"
    return 0
  fi
  if [[ -z $cur ]] || git merge-base --is-ancestor "$cur" "$tip"; then
    git push -q "$PUSH_REMOTE" "$tip:refs/heads/$MIRROR"
  else
    warn "the original rewrote history: fork ${cur:0:7} -> original ${tip:0:7}"
    confirm "Overwrite main in the fork (force-with-lease)?" || die "cancelled"
    git push -q --force-with-lease="refs/heads/$MIRROR:$cur" "$PUSH_REMOTE" "$tip:refs/heads/$MIRROR"
  fi
  info "fork main updated to ${tip:0:7}"
}

cmd_sync() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "uncommitted changes: commit or stash first"
  fi
  in_merge && die "a merge is in progress: finish it (--finish) or cancel it (--abort)"

  git checkout -q "$WORK"

  local old new
  old=$(git rev-parse -q --verify "refs/remotes/$REMOTE/$MIRROR" || true)
  [[ $MIRROR_FIRST == 1 ]] && cmd_mirror
  info "fetch $REMOTE"
  git fetch --prune "$REMOTE"
  new=$(git rev-parse "refs/remotes/$REMOTE/$MIRROR")

  if [[ -n $old && $old != "$new" ]] && ! git merge-base --is-ancestor "$old" "$new"; then
    warn "history of $REMOTE/$MIRROR was rewritten (force-update): ${old:0:7} -> ${new:0:7}"
    warn "expect duplicate commits; upstream-owned files are still taken whole"
    confirm "Continue?" || die "cancelled"
  fi

  if git merge-base --is-ancestor "$new" HEAD; then
    info "$WORK already contains $REMOTE/$MIRROR (${new:0:7})"
    if ! git diff --quiet "$new" HEAD -- . "${EXCL[@]}"; then
      warn "but upstream-owned files differ from upstream (leftover of an old merge):"
      git diff --stat "$new" HEAD -- . "${EXCL[@]}" | tail -15
      warn "fix: g3m-sync --repair"
      exit 3
    fi
    exit 0
  fi

  info "new upstream commits:"
  git log --oneline --no-decorate "HEAD..$new" | head -30
  git diff --shortstat "HEAD...$new" || true

  local base drift
  base=$(git merge-base HEAD "$new")
  drift=$(git diff --stat "$base" HEAD -- . "${EXCL[@]}" || true)
  if [[ -n $drift ]]; then
    warn "fork changes in upstream-owned files, they will be replaced by the upstream version:"
    echo "$drift"
    confirm "Discard these changes?" \
      || die "if the changes are intentional, add the files to MIXED and run again"
  fi

  local backup branch
  backup="backup/$WORK-$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
  branch="merge/upstream-${new:0:7}"
  git show-ref -q --verify "refs/heads/$branch" && die "branch $branch already exists (delete it or use --abort)"
  git branch -f "$backup" HEAD
  info "backup: $backup"
  git checkout -q -b "$branch"

  with_default_merge git merge --no-commit --no-ff "$REMOTE/$MIRROR" || true
  in_merge || die "merge did not start (check untracked files); go back: git checkout $WORK"

  info "taking the upstream version of upstream-owned files"
  adopt_upstream_files "$new"

  local f p left=()
  mapfile -t left < <(unmerged)
  for f in "${left[@]}"; do
    for p in "${FORK_ONLY[@]}"; do
      if [[ $f == "$p" || $f == "$p"/* ]]; then
        git checkout --ours -- "$f"
        git add -- "$f"
        break
      fi
    done
  done

  mapfile -t left < <(unmerged)
  if ((${#left[@]})); then
    warn "resolve by hand:"
    printf '    %s\n' "${left[@]}"
    echo "Resolve the conflicts, run git add, then: g3m-sync --finish"
    echo "Hint: git diff ${base:0:7} $new -- README.md shows what upstream changed"
    exit 2
  fi

  cmd_finish
}

cmd_finish() {
  in_merge || die "no merge in progress (run g3m-sync first)"
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)
  [[ $branch == merge/upstream-* ]] || die "expected a merge/upstream-* branch, got: $branch"

  [[ -z $(unmerged) ]] || die "conflicts remain: $(unmerged | tr '\n' ' ')"
  git diff --quiet || die "unstaged changes: run git add"

  run_checks

  info "check: upstream-owned files match upstream"
  if ! git diff --cached --quiet MERGE_HEAD -- . "${EXCL[@]}"; then
    git diff --cached --stat MERGE_HEAD -- . "${EXCL[@]}"
    die "upstream-owned files differ from upstream (see above)"
  fi

  local short
  short=$(git rev-parse --short MERGE_HEAD)
  git commit -q -m "merge: $REMOTE/$MIRROR @ $short into $WORK"
  info "merge committed on $branch"

  land "$branch"
}

cmd_repair() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "uncommitted changes: commit or stash first"
  fi
  in_merge && die "a merge is in progress: finish it (--finish) or cancel it (--abort)"
  git checkout -q "$WORK"

  [[ $MIRROR_FIRST == 1 ]] && cmd_mirror
  git fetch -q --prune "$REMOTE"
  local new branch backup
  new=$(git rev-parse "refs/remotes/$REMOTE/$MIRROR")
  git merge-base --is-ancestor "$new" HEAD \
    || die "$WORK does not contain ${new:0:7} yet: run a regular g3m-sync first"

  if git diff --quiet "$new" HEAD -- . "${EXCL[@]}"; then
    info "nothing to repair: upstream-owned files match ${new:0:7}"
    exit 0
  fi
  info "upstream-owned files that differ from ${new:0:7}:"
  git diff --stat "$new" HEAD -- . "${EXCL[@]}" | tail -25
  confirm "Reset them to the upstream version?" || die "cancelled"

  backup="backup/$WORK-$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
  branch="fix/upstream-${new:0:7}"
  git show-ref -q --verify "refs/heads/$branch" && die "branch $branch already exists"
  git branch -f "$backup" HEAD
  info "backup: $backup"
  git checkout -q -b "$branch"
  adopt_upstream_files "$new"
  run_checks
  git diff --cached --quiet "$new" -- . "${EXCL[@]}" || die "upstream-owned files still differ"
  git commit -q -m "fix: take upstream state of upstream-owned files (${new:0:7})"
  land "$branch"
}

cmd_abort() {
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)
  in_merge && git merge --abort
  git checkout -q "$WORK"
  if [[ $branch == merge/upstream-* ]]; then
    git branch -q -D "$branch"
    info "branch $branch deleted, $WORK untouched"
  fi
}

cd "$(git rev-parse --show-toplevel)"
case "$MODE" in
  sync) cmd_sync ;;
  finish) cmd_finish ;;
  abort) cmd_abort ;;
  repair) cmd_repair ;;
esac
