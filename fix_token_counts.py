#!/usr/bin/env python3
"""
Fix Token Counts - Перерахунок токенів після ручної модифікації сесії

Логіка:
  next_cache_read = prev_cache_read + prev_cache_creation + input_tokens

Використання:
  python3 fix_token_counts.py [session_file] [--dry-run]

  # Or set session dir via environment:
  export CLAUDE_SESSION_DIR="$HOME/.claude/projects/my-project"
  python3 fix_token_counts.py --dry-run
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Session directory - from env or default to current .claude
def get_session_dir() -> Path:
    if "CLAUDE_SESSION_DIR" in os.environ:
        return Path(os.environ["CLAUDE_SESSION_DIR"])
    # Try to find from XDG_CONFIG_HOME
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".claude"))
    return Path(config_home) / "projects"

CHARS_PER_TOKEN = 3.5


def find_current_session(session_dir: Optional[Path] = None) -> Optional[Path]:
    """Find the most recent session file in given or default directory."""
    if session_dir is None:
        session_dir = get_session_dir()
    if not session_dir.exists():
        return None
    # Search recursively for jsonl files
    sessions = list(session_dir.glob("**/*.jsonl"))
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def estimate_tokens_from_content(line: str) -> int:
    """Estimate tokens from line content."""
    return int(len(line) / CHARS_PER_TOKEN)


def find_nested_key(obj, key):
    """Recursively find a key in nested structure."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = find_nested_key(v, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_nested_key(item, key)
            if result is not None:
                return result
    return None


def set_nested_key(obj, key, value):
    """Recursively set a key in nested structure. Returns True if found and set."""
    if isinstance(obj, dict):
        if key in obj:
            obj[key] = value
            return True
        for v in obj.values():
            if set_nested_key(v, key, value):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if set_nested_key(item, key, value):
                return True
    return False


def fix_token_counts(session_path: Path, dry_run: bool = False):
    """Fix token counts in session file."""

    print(f"Session: {session_path.name}")

    # Read all lines
    lines = []
    with open(session_path, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    print(f"Total lines: {len(lines)}")

    # Find compact summary end
    compact_end_idx = -1
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
            if obj.get("isCompactSummary") or obj.get("subtype") == "compact_boundary":
                compact_end_idx = i
        except:
            pass

    if compact_end_idx == -1:
        print("No compact summary found!")
        return False

    print(f"Compact summary ends at line: {compact_end_idx}")

    # Calculate initial cache_read from summary content + system prompt estimate
    # System prompt (CLAUDE.md, system messages) ≈ 20K tokens
    SYSTEM_PROMPT_TOKENS = 20000

    # Get summary content length
    summary_tokens = 0
    try:
        summary_obj = json.loads(lines[compact_end_idx])
        content = summary_obj.get("message", {}).get("content", "")
        if isinstance(content, str):
            summary_tokens = int(len(content) / CHARS_PER_TOKEN)
        elif isinstance(content, list):
            total_chars = sum(len(str(c)) for c in content)
            summary_tokens = int(total_chars / CHARS_PER_TOKEN)
    except:
        summary_tokens = 3000  # Default estimate

    initial_cache_read = SYSTEM_PROMPT_TOKENS + summary_tokens
    print(f"Initial cache_read: {initial_cache_read} (system ~{SYSTEM_PROMPT_TOKENS} + summary ~{summary_tokens})")

    # Process lines after compact summary
    prev_cache_read = initial_cache_read
    prev_cache_creation = 0  # Start fresh after compact

    # Accumulate tokens from message CONTENT (not full JSON line)
    accumulated_content_tokens = 0
    fixed_count = 0

    new_lines = lines[:compact_end_idx + 1]  # Keep everything up to compact

    for i in range(compact_end_idx + 1, len(lines)):
        line = lines[i]

        try:
            obj = json.loads(line)

            # Estimate tokens from content only (not JSON overhead)
            content = ""
            if obj.get("type") in ("user", "assistant"):
                msg = obj.get("message", {})
                msg_content = msg.get("content", "")
                if isinstance(msg_content, str):
                    content = msg_content
                elif isinstance(msg_content, list):
                    # Extract text from content blocks
                    for block in msg_content:
                        if isinstance(block, dict):
                            if "text" in block:
                                content += block["text"]
                            elif "input" in block:
                                content += str(block["input"])

            content_tokens = int(len(content) / CHARS_PER_TOKEN) if content else 0
            accumulated_content_tokens += content_tokens

            if obj.get("type") == "assistant":
                usage = obj.get("message", {}).get("usage", {})

                if usage:
                    old_cache_read = usage.get("cache_read_input_tokens", 0)
                    old_cache_creation = usage.get("cache_creation_input_tokens", 0)
                    input_tokens = usage.get("input_tokens", 10)

                    # Calculate new values using the formula:
                    # cache_read = previous cache_read + previous cache_creation
                    new_cache_read = prev_cache_read + prev_cache_creation

                    # cache_creation = new tokens since last assistant message
                    new_cache_creation = accumulated_content_tokens

                    if old_cache_read != new_cache_read or old_cache_creation != new_cache_creation:
                        if dry_run:
                            print(f"  Line {i}: cache_read {old_cache_read} → {new_cache_read}, "
                                  f"cache_creation {old_cache_creation} → {new_cache_creation}")

                        # Update values
                        set_nested_key(obj, "cache_read_input_tokens", new_cache_read)
                        set_nested_key(obj, "cache_creation_input_tokens", new_cache_creation)

                        # Also update cache_creation dict if present
                        cache_creation_dict = find_nested_key(obj, "cache_creation")
                        if isinstance(cache_creation_dict, dict):
                            if "ephemeral_1h_input_tokens" in cache_creation_dict:
                                cache_creation_dict["ephemeral_1h_input_tokens"] = new_cache_creation

                        line = json.dumps(obj, ensure_ascii=False)
                        fixed_count += 1

                    # Update for next iteration
                    prev_cache_read = new_cache_read
                    prev_cache_creation = new_cache_creation
                    accumulated_content_tokens = 0  # Reset accumulator

            new_lines.append(line)

        except json.JSONDecodeError:
            new_lines.append(line)

    print(f"\nMessages to fix: {fixed_count}")

    if dry_run:
        print("\n=== DRY RUN - no changes made ===")

        # Show final estimated context
        total_context = prev_cache_read + prev_cache_creation
        pct = int(total_context / 200000 * 100)
        print(f"\nEstimated final context: {total_context} tokens ({pct}%)")
        return True

    # Write updated file
    with open(session_path, 'w') as f:
        for line in new_lines:
            f.write(line + '\n')

    total_context = prev_cache_read + prev_cache_creation
    pct = int(total_context / 200000 * 100)
    print(f"\n✅ Fixed {fixed_count} messages")
    print(f"Final context: {total_context} tokens ({pct}%)")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fix token counts after manual session modification")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Show what would be done")
    parser.add_argument("--session", type=str, help="Path to specific session file")
    parser.add_argument("--session-dir", type=str, help="Session directory (or set CLAUDE_SESSION_DIR env)")

    args = parser.parse_args()

    if args.session:
        session_path = Path(args.session)
    else:
        session_dir = Path(args.session_dir) if args.session_dir else None
        session_path = find_current_session(session_dir)

    if not session_path or not session_path.exists():
        print("No session file found!", file=sys.stderr)
        print(f"Searched in: {args.session_dir or get_session_dir()}", file=sys.stderr)
        sys.exit(1)

    success = fix_token_counts(session_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
