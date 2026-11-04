#!/usr/bin/env bash
# g3m-sync — безопасная синхронизация python-nix с оригиналом.
#
#   g3m-sync             fetch → превью → merge в отдельной ветке → проверки → ff python-nix
#   g3m-sync --mirror    то же, но сначала подтянуть main форка из оригинала (y114git/G3M)
#   g3m-sync --repair    привести файлы апстрима к версии апстрима (после неудачного merge)
#   g3m-sync --finish    продолжить после ручного разрешения README/CHANGELOG
#   g3m-sync --abort     отменить незавершённый merge, python-nix не тронут
#
# Флаги: --yes (не спрашивать), --fast (без `nix flake check`), --push (git push после ff)
#
# Модель владения путями:
#   FORK_ONLY — есть только у форка, всегда остаётся нашим;
#   MIXED     — правят обе стороны, при конфликте останавливаемся и ждём тебя;
#   остальное — принадлежит апстриму: берём его версию целиком.
# Если когда-нибудь появится настоящий патч форка в src/ — добавь этот файл в MIXED.

set -euo pipefail

REMOTE="${G3M_REMOTE:-upstream}"             # откуда читаем main (у тебя это твой форк)
PUSH_REMOTE="${G3M_PUSH_REMOTE:-origin}"     # куда пушим (upstream у тебя без push)
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
g3m-sync            fetch, превью, merge в отдельной ветке, проверки, ff python-nix
g3m-sync --mirror   сначала обновить main форка из оригинала
g3m-sync --repair   привести файлы апстрима к версии апстрима (чинит старый плохой merge)
g3m-sync --finish   завершить после ручного разрешения README/CHANGELOG
g3m-sync --abort    отменить, python-nix не тронут
флаги: --yes  --fast (без nix flake check)  --push
USAGE
      exit 0
      ;;
    *) echo "неизвестный аргумент: $arg" >&2; exit 64 ;;
  esac
done

EXCL=()
for p in "${FORK_ONLY[@]}" "${MIXED[@]}"; do EXCL+=(":(exclude)$p"); done

info() { echo "▸ $*"; }
warn() { echo "⚠ $*" >&2; }
die() { echo "✗ $*" >&2; exit 1; }

confirm() {
  [[ $YES == 1 ]] && return 0
  [[ -t 0 ]] || die "нужно подтверждение: запусти из терминала или добавь --yes"
  local answer
  read -r -p "$1 [y/N] " answer
  [[ $answer == y || $answer == Y ]]
}

in_merge() { git rev-parse -q --verify MERGE_HEAD >/dev/null; }

unmerged() { git diff --name-only --diff-filter=U; }

# Запустить команду с отключёнными merge-драйверами из .gitattributes
# (например `* merge=ours`, который молча оставляет нашу версию без конфликта).
# .git/info/attributes приоритетнее .gitattributes, правило временное.
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

# Взять из коммита $1 версию всех файлов апстрима и удалить те, которых там нет.
adopt_upstream_files() {
  local stale=()
  git checkout "$1" -- . "${EXCL[@]}"
  mapfile -t stale < <(comm -23 \
    <(git -c core.quotePath=false ls-files -- . "${EXCL[@]}" | sort -u) \
    <(git -c core.quotePath=false ls-tree -r --name-only "$1" | sort -u))
  if ((${#stale[@]})); then
    info "удаляю файлы, которых нет в апстриме:"
    printf '    %s\n' "${stale[@]}"
    git rm -q -f -- "${stale[@]}"
  fi
}

run_checks() {
  info "проверка: маркеры конфликтов"
  if git grep -nE '^(<<<<<<< |>>>>>>> )' -- . >/dev/null; then
    git grep -nE '^(<<<<<<< |>>>>>>> )' -- . | head
    die "в файлах остались маркеры конфликтов"
  fi
  if command -v ruff >/dev/null; then
    info "проверка: синтаксис (ruff E9)"
    ruff check --select E9 --no-cache --quiet . || die "синтаксические ошибки"
  else
    warn "ruff не найден — синтаксис не проверен"
  fi
}

# nix flake check → fast-forward рабочей ветки → (push)
land() {
  local branch="$1"
  if [[ $FAST == 0 ]]; then
    info "nix flake check -L (долго; --fast чтобы пропустить)"
    nix flake check -L || die "nix flake check упал; $WORK не тронут, ветка $branch сохранена"
  fi
  git checkout -q "$WORK"
  git merge -q --ff-only "$branch"
  git branch -q -d "$branch"
  info "готово: $WORK обновлён до $(git rev-parse --short HEAD)"
  if [[ $PUSH == 1 ]]; then
    git push
  else
    info "если всё в порядке: git push"
  fi
}

cmd_mirror() {
  info "main форка ← оригинал ($ORIGINAL)"
  local tip cur
  git fetch -q --no-tags "$ORIGINAL" "$MIRROR"
  tip=$(git rev-parse FETCH_HEAD)
  git fetch -q --prune "$REMOTE"
  cur=$(git rev-parse -q --verify "refs/remotes/$REMOTE/$MIRROR" || true)
  if [[ $cur == "$tip" ]]; then
    info "main уже совпадает с оригиналом (${tip:0:7})"
    return 0
  fi
  if [[ -z $cur ]] || git merge-base --is-ancestor "$cur" "$tip"; then
    git push -q "$PUSH_REMOTE" "$tip:refs/heads/$MIRROR"
  else
    warn "оригинал переписал историю: форк ${cur:0:7} → оригинал ${tip:0:7}"
    confirm "Перезаписать main в форке (force-with-lease)?" || die "отменено"
    git push -q --force-with-lease="refs/heads/$MIRROR:$cur" "$PUSH_REMOTE" "$tip:refs/heads/$MIRROR"
  fi
  info "main форка обновлён до ${tip:0:7}"
}

cmd_sync() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "есть незакоммиченные изменения — сначала commit или stash"
  fi
  in_merge && die "merge уже идёт: допиши (--finish) или отмени (--abort)"

  git checkout -q "$WORK"

  local old new
  old=$(git rev-parse -q --verify "refs/remotes/$REMOTE/$MIRROR" || true)
  [[ $MIRROR_FIRST == 1 ]] && cmd_mirror
  info "fetch $REMOTE"
  git fetch --prune "$REMOTE"
  new=$(git rev-parse "refs/remotes/$REMOTE/$MIRROR")

  if [[ -n $old && $old != "$new" ]] && ! git merge-base --is-ancestor "$old" "$new"; then
    warn "история $REMOTE/$MIRROR была переписана (force-update): ${old:0:7} → ${new:0:7}"
    warn "ожидай дубли коммитов; файлы апстрима всё равно будут взяты целиком"
    confirm "Продолжить?" || die "отменено"
  fi

  if git merge-base --is-ancestor "$new" HEAD; then
    info "$WORK уже содержит $REMOTE/$MIRROR (${new:0:7})"
    if ! git diff --quiet "$new" HEAD -- . "${EXCL[@]}"; then
      warn "но файлы апстрима отличаются от апстрима (след старого merge):"
      git diff --stat "$new" HEAD -- . "${EXCL[@]}" | tail -15
      warn "исправить: g3m-sync --repair"
      exit 3
    fi
    exit 0
  fi

  info "новые коммиты апстрима:"
  git log --oneline --no-decorate "HEAD..$new" | head -30
  git diff --shortstat "HEAD...$new" || true

  local base drift
  base=$(git merge-base HEAD "$new")
  drift=$(git diff --stat "$base" HEAD -- . "${EXCL[@]}" || true)
  if [[ -n $drift ]]; then
    warn "правки форка в файлах апстрима — они будут заменены версией апстрима:"
    echo "$drift"
    confirm "Отбросить эти правки?" \
      || die "если правки намеренные, добавь файлы в MIXED и запусти снова"
  fi

  local backup branch
  backup="backup/$WORK-$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
  branch="merge/upstream-${new:0:7}"
  git show-ref -q --verify "refs/heads/$branch" && die "ветка $branch уже существует (удали её или --abort)"
  git branch -f "$backup" HEAD
  info "бэкап: $backup"
  git checkout -q -b "$branch"

  with_default_merge git merge --no-commit --no-ff "$REMOTE/$MIRROR" || true
  in_merge || die "merge не стартовал (проверь untracked-файлы); вернись: git checkout $WORK"

  info "беру версию апстрима для его файлов"
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
    warn "нужно разрешить вручную:"
    printf '    %s\n' "${left[@]}"
    echo "Разреши конфликты, сделай git add, затем: g3m-sync --finish"
    echo "Подсказка: git diff ${base:0:7} $new -- README.md  покажет, что изменил апстрим"
    exit 2
  fi

  cmd_finish
}

cmd_finish() {
  in_merge || die "нет незавершённого merge (сначала g3m-sync)"
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)
  [[ $branch == merge/upstream-* ]] || die "ожидалась ветка merge/upstream-*, сейчас: $branch"

  [[ -z $(unmerged) ]] || die "остались конфликты: $(unmerged | tr '\n' ' ')"
  git diff --quiet || die "есть неподготовленные правки — выполни git add"

  run_checks

  info "проверка: файлы апстрима совпадают с апстримом"
  if ! git diff --cached --quiet MERGE_HEAD -- . "${EXCL[@]}"; then
    git diff --cached --stat MERGE_HEAD -- . "${EXCL[@]}"
    die "файлы апстрима отличаются от апстрима (см. выше)"
  fi

  local short
  short=$(git rev-parse --short MERGE_HEAD)
  git commit -q -m "merge: $REMOTE/$MIRROR @ $short into $WORK"
  info "merge закоммичен в $branch"

  land "$branch"
}

cmd_repair() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "есть незакоммиченные изменения — сначала commit или stash"
  fi
  in_merge && die "merge уже идёт: допиши (--finish) или отмени (--abort)"
  git checkout -q "$WORK"

  [[ $MIRROR_FIRST == 1 ]] && cmd_mirror
  git fetch -q --prune "$REMOTE"
  local new branch backup
  new=$(git rev-parse "refs/remotes/$REMOTE/$MIRROR")
  git merge-base --is-ancestor "$new" HEAD \
    || die "$WORK ещё не содержит ${new:0:7}: сначала обычный g3m-sync"

  if git diff --quiet "$new" HEAD -- . "${EXCL[@]}"; then
    info "чинить нечего: файлы апстрима совпадают с ${new:0:7}"
    exit 0
  fi
  info "файлы апстрима, отличающиеся от ${new:0:7}:"
  git diff --stat "$new" HEAD -- . "${EXCL[@]}" | tail -25
  confirm "Привести их к версии апстрима?" || die "отменено"

  backup="backup/$WORK-$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"
  branch="fix/upstream-${new:0:7}"
  git show-ref -q --verify "refs/heads/$branch" && die "ветка $branch уже существует"
  git branch -f "$backup" HEAD
  info "бэкап: $backup"
  git checkout -q -b "$branch"
  adopt_upstream_files "$new"
  run_checks
  git diff --cached --quiet "$new" -- . "${EXCL[@]}" || die "файлы апстрима всё ещё отличаются"
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
    info "ветка $branch удалена, $WORK не тронут"
  fi
}

cd "$(git rev-parse --show-toplevel)"
case "$MODE" in
  sync) cmd_sync ;;
  finish) cmd_finish ;;
  abort) cmd_abort ;;
  repair) cmd_repair ;;
esac
