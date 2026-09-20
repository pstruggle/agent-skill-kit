#!/usr/bin/env sh
# Install the skills in this repository into one or more agent skill directories.
#
#   dsh     user:    $DSH_HOME/skills               (default ~/.dsh/skills)
#   dsh     project: <project>/.dsh/skills
#   codex   both:    $CODEX_HOME/skills             (default ~/.codex/skills)
#   claude  user:    ~/.claude/skills
#   claude  project: <project>/.claude/skills
#   agents  user:    ~/.agents/skills
#   agents  project: <project>/.agents/skills
#
# Codex discovers skills from CODEX_HOME only, so --scope project still installs
# into $CODEX_HOME/skills. Use --dest to also install into a custom directory.
#
# Examples:
#   ./install.sh --list
#   ./install.sh --target all --scope user
#   ./install.sh --target dsh --scope project --project-root ~/work/my-app
#   ./install.sh --target codex,claude --mode link
#   ./install.sh --uninstall --target claude

set -eu

targets='all'
scope='user'
skills=''
mode='copy'
project_root=$(pwd)
dest=''
force=0
dry_run=0
uninstall=0
list=0

usage() {
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target) targets=$2; shift 2 ;;
        --scope) scope=$2; shift 2 ;;
        --skill) skills=$2; shift 2 ;;
        --mode) mode=$2; shift 2 ;;
        --project-root) project_root=$2; shift 2 ;;
        --dest) dest=$2; shift 2 ;;
        --force) force=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        --list) list=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown option: $1" >&2; usage 1 ;;
    esac
done

case "$scope" in user|project) ;; *) echo "Invalid --scope: $scope" >&2; exit 1 ;; esac
case "$mode" in copy|link) ;; *) echo "Invalid --mode: $mode" >&2; exit 1 ;; esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root="$script_dir/skills"
[ -d "$source_root" ] || { echo "Missing skills directory: $source_root" >&2; exit 1; }

dsh_home=${DSH_HOME:-$HOME/.dsh}
codex_home=${CODEX_HOME:-$HOME/.codex}

# root_for <target> -> install root for the selected scope
root_for() {
    case "$1" in
        dsh)    if [ "$scope" = project ]; then printf '%s' "$project_root/.dsh/skills"; else printf '%s' "$dsh_home/skills"; fi ;;
        agents) if [ "$scope" = project ]; then printf '%s' "$project_root/.agents/skills"; else printf '%s' "$HOME/.agents/skills"; fi ;;
        claude) if [ "$scope" = project ]; then printf '%s' "$project_root/.claude/skills"; else printf '%s' "$HOME/.claude/skills"; fi ;;
        codex)  printf '%s' "$codex_home/skills" ;;
        *)      echo "Unknown target: $1" >&2; exit 1 ;;
    esac
}

all_skills() {
    for dir in "$source_root"/*/; do
        [ -d "$dir" ] || continue
        name=$(basename "$dir")
        [ -f "$dir/SKILL.md" ] && printf '%s\n' "$name"
    done
}

selected_skills() {
    if [ -z "$skills" ]; then
        all_skills
        return
    fi
    printf '%s' "$skills" | tr ',' '\n' | while IFS= read -r want; do
        [ -n "$want" ] || continue
        if [ ! -f "$source_root/$want/SKILL.md" ]; then
            echo "Unknown skill: $want" >&2
            exit 1
        fi
        printf '%s\n' "$want"
    done
}

if [ "$targets" = all ]; then
    target_list='dsh codex claude agents'
else
    target_list=$(printf '%s' "$targets" | tr ',' ' ')
fi
if [ -n "$dest" ]; then
    case " $target_list " in
        *" custom "*) ;;
        *) target_list="$target_list custom" ;;
    esac
fi

if [ "$list" = 1 ]; then
    echo
    echo 'Skills in this repository:'
    all_skills | sed 's/^/  - /'
    echo
    echo "Install destinations (scope: $scope):"
    for name in $target_list; do
        [ "$name" = custom ] && root=$dest || root=$(root_for "$name")
        printf '  %-7s %s\n' "$name" "$root"
    done
    echo
    exit 0
fi

planned=0
skipped=0

for skill in $(selected_skills); do
    for name in $target_list; do
        if [ "$name" = custom ]; then root=$dest; else root=$(root_for "$name"); fi
        dest_path="$root/$skill"

        if [ "$uninstall" = 1 ]; then
            if [ ! -e "$dest_path" ]; then
                echo "[$name] $skill: not installed"
                skipped=$((skipped + 1))
                continue
            fi
            echo "[$name] remove  $dest_path"
            [ "$dry_run" = 1 ] || rm -rf -- "$dest_path"
            planned=$((planned + 1))
            continue
        fi

        if [ -e "$dest_path" ]; then
            if [ "$force" != 1 ]; then
                echo "[$name] $skill: already exists, skipped (use --force to replace)"
                skipped=$((skipped + 1))
                continue
            fi
            echo "[$name] replace $dest_path"
            [ "$dry_run" = 1 ] || rm -rf -- "$dest_path"
        else
            printf '[%s] %-4s %s\n' "$name" "$mode" "$dest_path"
        fi

        if [ "$dry_run" = 1 ]; then planned=$((planned + 1)); continue; fi

        mkdir -p -- "$root"
        if [ "$mode" = link ]; then
            ln -s -- "$source_root/$skill" "$dest_path"
        else
            cp -R -- "$source_root/$skill" "$dest_path"
        fi
        planned=$((planned + 1))
    done
done

echo
if [ "$dry_run" = 1 ]; then
    echo "Install: $planned action(s), $skipped skipped (dry run, nothing written)"
else
    echo "Install: $planned action(s), $skipped skipped"
fi
echo 'Restart the agent session so it picks up the new skill.'
