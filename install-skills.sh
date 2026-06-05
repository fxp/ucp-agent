#!/usr/bin/env bash
# Symlinks ucp-agent skills into ~/.claude/skills/
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
mkdir -p "$TARGET_DIR"

link_dir() {
  local src="$1"
  local name; name=$(basename "$src")
  local dst="$TARGET_DIR/$name"
  if [ -L "$dst" ]; then
    ln -sfn "$src" "$dst" && echo "  refreshed: $dst"
  elif [ -e "$dst" ]; then
    echo "  SKIPPED (real path exists): $dst" >&2
  else
    ln -s "$src" "$dst" && echo "  linked: $dst"
  fi
}

echo "Linking ucp-agent skills into $TARGET_DIR/"
for d in "$REPO_ROOT"/skills/*/; do
  [ -f "$d/SKILL.md" ] || { echo "  skip $(basename "$d") (no SKILL.md)"; continue; }
  link_dir "${d%/}"
done

echo
echo "Done. Skills installed:"
echo "  supply-chain  — 供应链 API 操作"
echo "  vending-agent — 贩卖机 AI Agent 购买流程"
