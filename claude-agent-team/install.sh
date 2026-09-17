#!/usr/bin/env bash
# Install the Claude Code agent team into a user or project .claude/agents directory.
#
#   ./install.sh --scope user
#   ./install.sh --scope project --path ~/code/some-project
#   ./install.sh --scope user --dry-run
#
# Existing files are never overwritten unless --force is given; every skipped
# file is named in the output so nothing is lost silently.

set -euo pipefail

scope=user
project_path=""
force=0
dry_run=0

usage() {
    # Every leading comment line after the shebang, stopping at the first
    # line that is not one. A fixed line range drifts the moment the header
    # is edited, and printed `set -euo pipefail` into the help text once.
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    echo
    echo "Options:"
    echo "  --scope user|project   user: \$HOME/.claude/agents (default)"
    echo "                         project: <path>/.claude/agents"
    echo "  --path <dir>           project root; required for --scope project"
    echo "  --force                overwrite files that already exist"
    echo "  --dry-run              report what would happen, write nothing"
    echo "  -h, --help             this message"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --scope)   scope="${2:?--scope needs a value}"; shift 2 ;;
        --path)    project_path="${2:?--path needs a value}"; shift 2 ;;
        --force)   force=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$here/agents"

[ -d "$source_dir" ] || { echo "No agents directory beside this script. Expected: $source_dir" >&2; exit 1; }

case "$scope" in
    user)
        root="$HOME"
        ;;
    project)
        [ -n "$project_path" ] || { echo "--scope project needs --path <project root>." >&2; exit 2; }
        [ -d "$project_path" ] || { echo "Not a directory: $project_path" >&2; exit 1; }
        root="$(cd "$project_path" && pwd)"
        ;;
    *)
        echo "--scope must be 'user' or 'project', got: $scope" >&2; exit 2 ;;
esac

target="$root/.claude/agents"

echo
echo "  source : $source_dir"
echo "  target : $target"
if [ "$dry_run" -eq 1 ]; then
    echo "  scope  : $scope   (dry run -- nothing will be written)"
else
    echo "  scope  : $scope"
fi
echo

if [ ! -d "$target" ]; then
    if [ "$dry_run" -eq 1 ]; then
        echo "  would create $target"
    else
        mkdir -p "$target"
        echo "  created $target"
    fi
fi

installed=0; replaced=0; skipped=0; total=0

for file in "$source_dir"/*.md; do
    [ -e "$file" ] || { echo "No .md files in $source_dir" >&2; exit 1; }
    name="$(basename "$file")"
    destination="$target/$name"
    total=$((total + 1))

    if [ -e "$destination" ] && [ "$force" -eq 0 ]; then
        echo "  skipped         $name   (already present -- pass --force to overwrite)"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -e "$destination" ]; then
        [ "$dry_run" -eq 1 ] || cp "$file" "$destination"
        if [ "$dry_run" -eq 1 ]; then echo "  would replace   $name"; else echo "  replaced        $name"; fi
        replaced=$((replaced + 1))
    else
        [ "$dry_run" -eq 1 ] || cp "$file" "$destination"
        if [ "$dry_run" -eq 1 ]; then echo "  would install   $name"; else echo "  installed       $name"; fi
        installed=$((installed + 1))
    fi
done

echo
echo "  $installed new, $replaced replaced, $skipped skipped"

if [ "$skipped" -eq "$total" ]; then
    echo "  Nothing changed. Every file was already there."
elif [ "$dry_run" -eq 0 ]; then
    echo
    echo "  Start a new Claude Code session, then try:"
    echo "    use the security-reviewer agent to audit this repository"
fi
echo
