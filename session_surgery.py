#!/usr/bin/env python3
"""
Session Surgery v4 - Sliding Context Window with Pin Management

Структура після ковзання:
[auto-summary від Claude (parentUuid:null + наступний)]
[current.md — rolling summary]
[§PINNED§ повідомлення]
[всі live messages після точки ковзання]

Використання:
  python3 _scripts/session_surgery.py --slide-at UUID [--dry-run]  # ковзання з точки
  python3 _scriptssession_surgery.py --collect-all-pins           # зібрати всі піни
  python3 _scriptssession_surgery.py --list-pins                  # показати піни
  python3 _scriptssession_surgery.py --archive-pin UUID           # архівувати пін
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid as uuid_lib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


def get_session_dir() -> Path:
    """Get session directory from environment or auto-detect."""
    if "CLAUDE_SESSION_DIR" in os.environ:
        return Path(os.environ["CLAUDE_SESSION_DIR"])

    # Try XDG_CONFIG_HOME
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "projects"

    # Default: home .claude
    return Path.home() / ".claude" / "projects"


def get_agent_root() -> Path:
    """Get agent root directory from environment or current working directory."""
    if "AGENT_ROOT" in os.environ:
        return Path(os.environ["AGENT_ROOT"])
    # Fallback to current working directory
    return Path.cwd()


# Paths - all relative to agent root, not script location
AGENT_ROOT = get_agent_root()
SESSION_DIR = get_session_dir()
BACKUP_DIR = AGENT_ROOT / "sessions/backups"
CURRENT_MD = AGENT_ROOT / "memory/current.md"
NARRATIVE_MD = AGENT_ROOT / "memory/narrative.md"
JOURNAL_FILE = AGENT_ROOT / "memory/journal.jsonl"
PINNED_FILE = AGENT_ROOT / "memory/pinned.jsonl"

# Constants
MAX_CONTEXT_TOKENS = 200000
DEFAULT_TARGET_PCT = 50  # Keep ~50% of context as live messages
CHARS_PER_TOKEN = 3.5  # Approximate for mixed Ukrainian/English

PIN_TAG = "§PIN§"
PINNED_TAG = "§PINNED§"
BOUNDARY_TAG = "§SUMMARY_BOUNDARY§"


def find_current_session(session_dir: Optional[Path] = None) -> Optional[Path]:
    """Find the most recent session file."""
    if session_dir is None:
        session_dir = SESSION_DIR

    if not session_dir.exists():
        return None

    # Search recursively for jsonl files
    sessions = list(session_dir.glob("**/*.jsonl"))
    if not sessions:
        return None

    return max(sessions, key=lambda p: p.stat().st_mtime)


def generate_uuid() -> str:
    """Generate a new UUID."""
    return str(uuid_lib.uuid4())


def generate_message_id() -> str:
    """Generate a message ID in Anthropic format: msg_XXXXXX..."""
    import random
    import string
    # Format: msg_ + 24 alphanumeric characters
    chars = string.ascii_letters + string.digits
    suffix = ''.join(random.choices(chars, k=24))
    return f"msg_{suffix}"


def current_timestamp() -> str:
    """Generate current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def estimate_tokens(text: str) -> int:
    """Estimate token count from text."""
    return int(len(text) / CHARS_PER_TOKEN)


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


def get_content(obj: dict) -> str:
    """Extract text content from a message object."""
    content = find_nested_key(obj, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                texts.append(block["text"])
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts)
    return ""


def get_usage_from_message(obj: dict) -> dict:
    """Extract usage info from assistant message."""
    return obj.get("message", {}).get("usage", {})


def is_pinned(line: str) -> bool:
    """Check if message contains §PIN§ (not in code blocks)."""
    if PIN_TAG not in line:
        return False
    if PINNED_TAG in line:
        return False  # Already pinned

    try:
        obj = json.loads(line)
        content = get_content(obj)

        # Skip if in code block
        if "```" in content:
            # Check only non-code parts
            parts = re.split(r'```[\s\S]*?```', content)
            return any(PIN_TAG in part for part in parts)

        return PIN_TAG in content
    except:
        return False


def convert_pin_to_pinned(line: str, session_num: int = 0, source_date: str = None) -> str:
    """Convert §PIN§ to §PINNED§ and add metadata."""
    line = line.replace(PIN_TAG, PINNED_TAG)

    try:
        obj = json.loads(line)
        # Add pin metadata
        obj["pinMetadata"] = {
            "source_session": session_num,
            "source_date": source_date or obj.get("timestamp", "")[:10],
            "status": "active",
            "pinned_at": current_timestamp()
        }
        return json.dumps(obj, ensure_ascii=False)
    except:
        return line


def load_pinned_messages(active_only: bool = True) -> list[str]:
    """Load previously pinned messages from file."""
    if not PINNED_FILE.exists():
        return []

    messages = []
    with open(PINNED_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if active_only:
                try:
                    obj = json.loads(line)
                    metadata = obj.get("pinMetadata", {})
                    if metadata.get("status") == "archived":
                        continue
                except:
                    pass

            messages.append(line)
    return messages


def save_pinned_messages(messages: list[str]):
    """Save pinned messages to file."""
    PINNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PINNED_FILE, 'w') as f:
        for msg in messages:
            f.write(msg + '\n')


def list_pins():
    """List all pinned messages with their metadata."""
    if not PINNED_FILE.exists():
        print("No pins file found.")
        return

    print("\n=== PINNED MESSAGES ===\n")

    with open(PINNED_FILE, 'r') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
                metadata = obj.get("pinMetadata", {})
                content = get_content(obj)[:100].replace('\n', ' ')

                status = metadata.get("status", "unknown")
                session = metadata.get("source_session", "?")
                date = metadata.get("source_date", "?")
                uuid = obj.get("uuid", "?")[:8]

                status_icon = "✓" if status == "active" else "○"

                print(f"{i}. [{status_icon}] Session {session} ({date}) [{uuid}...]")
                print(f"   {content}...")
                print()
            except Exception as e:
                print(f"{i}. [ERROR] Could not parse: {e}")

    print("=== END ===")


def archive_pin(uuid_prefix: str):
    """Archive a pin by UUID prefix."""
    if not PINNED_FILE.exists():
        print("No pins file found.")
        return False

    lines = []
    found = False

    with open(PINNED_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
                if obj.get("uuid", "").startswith(uuid_prefix):
                    metadata = obj.get("pinMetadata", {})
                    metadata["status"] = "archived"
                    obj["pinMetadata"] = metadata
                    line = json.dumps(obj, ensure_ascii=False)
                    found = True
                    print(f"Archived pin: {obj.get('uuid')[:8]}...")
            except:
                pass

            lines.append(line)

    if found:
        save_pinned_messages(lines)
        return True
    else:
        print(f"No pin found with UUID starting with: {uuid_prefix}")
        return False


def collect_all_pins(session_path: Path, dry_run: bool = False):
    """Collect all pins from the entire session file."""
    print(f"Collecting all pins from: {session_path.name}")

    lines = []
    with open(session_path, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # Track session number
    # Session start = parentUuid is None AND (first 2 lines OR has logicalParentUuid)
    session_num = 0
    pins = []

    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)

            # Count sessions correctly
            # Session start = parentUuid is None AND (has logicalParentUuid OR type is user/system)
            parent = obj.get("parentUuid")
            has_logical = "logicalParentUuid" in obj
            msg_type = obj.get("type", "")
            is_session_start = parent is None and (has_logical or msg_type in ["user", "system"])
            if is_session_start:
                session_num += 1

            # Check if this is a pin
            if is_pinned(line):
                source_date = obj.get("timestamp", "")[:10]
                converted = convert_pin_to_pinned(line, session_num, source_date)
                pins.append(converted)

                content = get_content(obj)[:80].replace('\n', ' ')
                print(f"  Found pin in session {session_num}: {content}...")
        except:
            pass

    print(f"\nTotal pins found: {len(pins)}")

    if dry_run:
        print("(dry run - not saved)")
        return

    # Load existing pins to avoid duplicates
    existing = load_pinned_messages(active_only=False)
    existing_uuids = set()

    for line in existing:
        try:
            obj = json.loads(line)
            existing_uuids.add(obj.get("uuid"))
        except:
            pass

    # Add only new pins
    new_pins = []
    for pin in pins:
        try:
            obj = json.loads(pin)
            if obj.get("uuid") not in existing_uuids:
                new_pins.append(pin)
        except:
            new_pins.append(pin)

    if new_pins:
        all_pins = existing + new_pins
        save_pinned_messages(all_pins)
        print(f"Added {len(new_pins)} new pins (total: {len(all_pins)})")
    else:
        print("No new pins to add.")


def create_backup(session_path: Path) -> Path:
    """Create timestamped backup of session file."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H%M%S")

    backup_subdir = BACKUP_DIR / date_str
    backup_subdir.mkdir(parents=True, exist_ok=True)

    backup_name = f"{session_path.stem}_{time_str}.jsonl"
    backup_path = backup_subdir / backup_name

    shutil.copy2(session_path, backup_path)
    print(f"Backup: {backup_path}")

    return backup_path


def create_message(content: str, parent_uuid: str, session_id: str,
                   msg_type: str = "user", timestamp: str = None,
                   extra_fields: dict = None) -> dict:
    """Create a message record."""
    msg = {
        "parentUuid": parent_uuid,
        "isSidechain": False,
        "userType": "external",
        "cwd": str(AGENT_ROOT),
        "sessionId": session_id,
        "version": "2.1.59",
        "gitBranch": "main",
        "type": msg_type,
        "message": {
            "role": msg_type,
            "content": content
        },
        "uuid": generate_uuid(),
        "timestamp": timestamp or current_timestamp()
    }
    if extra_fields:
        msg.update(extra_fields)
    return msg


def analyze_session(session_path: Path, target_pct: int = DEFAULT_TARGET_PCT) -> dict:
    """
    Analyze session to find sliding window boundaries.

    Returns dict with:
    - lines: all lines
    - compact_end_idx: where compact summary ends
    - window_start_idx: where to start the sliding window
    - existing_boundary_idx: where previous boundary marker is
    - new_pins: newly found §PIN§ messages (as lines)
    - token_info: token counts for recalculation
    """
    lines = []
    with open(session_path, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # Find compact summary end (last isCompactSummary or compact_boundary)
    compact_end_idx = -1
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
            if obj.get("isCompactSummary") or obj.get("subtype") == "compact_boundary":
                compact_end_idx = i
        except:
            pass

    if compact_end_idx == -1:
        print("No compact summary found!", file=sys.stderr)
        return None

    # Find existing boundary marker (if any)
    existing_boundary_idx = -1
    for i, line in enumerate(lines):
        if BOUNDARY_TAG in line:
            existing_boundary_idx = i

    # Calculate tokens from end, going backwards
    # Look at cache_creation_input_tokens in assistant messages
    target_tokens = int(MAX_CONTEXT_TOKENS * target_pct / 100)
    accumulated_tokens = 0
    window_start_idx = len(lines) - 1

    # Track token counts
    token_counts = []  # (line_idx, cache_creation, cache_read)

    for i in range(len(lines) - 1, compact_end_idx, -1):
        line = lines[i]
        try:
            obj = json.loads(line)

            if obj.get("type") == "assistant":
                usage = get_usage_from_message(obj)
                cache_creation = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)

                if cache_creation > 0:
                    token_counts.append((i, cache_creation, cache_read))
                    accumulated_tokens += cache_creation

                    if accumulated_tokens >= target_tokens:
                        window_start_idx = i
                        break

        except json.JSONDecodeError:
            continue

    # Find the user message just before window_start_idx (don't split user/assistant pairs)
    for i in range(window_start_idx, compact_end_idx, -1):
        try:
            obj = json.loads(lines[i])
            if obj.get("type") == "user":
                window_start_idx = i
                break
        except:
            pass

    # Count sessions to determine current session number
    # Session start = parentUuid is None AND (has logicalParentUuid OR type is user/system)
    session_num = 0
    for i in range(compact_end_idx + 1):
        try:
            obj = json.loads(lines[i])
            parent = obj.get("parentUuid")
            has_logical = "logicalParentUuid" in obj
            msg_type = obj.get("type", "")
            is_session_start = parent is None and (has_logical or msg_type in ["user", "system"])
            if is_session_start:
                session_num += 1
        except:
            pass

    # Find new §PIN§ messages
    # Search in the section being REMOVED (compact_end to window_start)
    # Plus any new ones after existing boundary in the kept section
    new_pins = []

    # 1. Pins in section being removed
    for i in range(compact_end_idx + 1, window_start_idx):
        line = lines[i]
        if is_pinned(line):
            try:
                obj = json.loads(line)
                source_date = obj.get("timestamp", "")[:10]
            except:
                source_date = None
            new_pins.append(convert_pin_to_pinned(line, session_num, source_date))

    # 2. Pins in kept section (after existing boundary, if any)
    if existing_boundary_idx > 0:
        for i in range(existing_boundary_idx + 1, len(lines)):
            line = lines[i]
            if is_pinned(line):
                try:
                    obj = json.loads(line)
                    source_date = obj.get("timestamp", "")[:10]
                except:
                    source_date = None
                new_pins.append(convert_pin_to_pinned(line, session_num, source_date))

    # Get token info for recalculation
    last_token_info = token_counts[0] if token_counts else (0, 0, 0)

    return {
        "lines": lines,
        "compact_end_idx": compact_end_idx,
        "window_start_idx": window_start_idx,
        "existing_boundary_idx": existing_boundary_idx,
        "new_pins": new_pins,
        "accumulated_tokens": accumulated_tokens,
        "last_token_info": last_token_info,
    }


def perform_surgery(session_path: Path, analysis: dict, dry_run: bool = False):
    """
    Perform the actual session surgery.
    """
    lines = analysis["lines"]
    compact_end_idx = analysis["compact_end_idx"]
    window_start_idx = analysis["window_start_idx"]
    new_pins = analysis["new_pins"]

    # Load existing pinned messages
    existing_pins = load_pinned_messages()
    all_pins = existing_pins + new_pins

    # Get session info from compact summary
    try:
        compact_obj = json.loads(lines[compact_end_idx])
        session_id = compact_obj.get("sessionId", "")
        compact_uuid = compact_obj.get("uuid", generate_uuid())
        compact_ts = compact_obj.get("timestamp", current_timestamp())
    except:
        session_id = ""
        compact_uuid = generate_uuid()
        compact_ts = current_timestamp()

    # Get timestamp of first live message
    try:
        window_obj = json.loads(lines[window_start_idx])
        window_ts = window_obj.get("timestamp", current_timestamp())
    except:
        window_ts = current_timestamp()

    # Create timestamp between compact and window (for inserted messages)
    insert_ts = compact_ts  # Will be just after compact

    # Build new session structure
    new_lines = []
    current_parent = compact_uuid

    # 1. Everything up to and including compact summary
    new_lines.extend(lines[:compact_end_idx + 1])

    # 2. current.md message (inserted after compact summary)
    if CURRENT_MD.exists():
        current_md_content = CURRENT_MD.read_text()
        current_md_msg = create_message(
            f"[Rolling Summary - current.md]\n\n{current_md_content}",
            current_parent, session_id, "user", insert_ts,
            {"isVesperSummary": True}
        )
        new_lines.append(json.dumps(current_md_msg, ensure_ascii=False))
        current_parent = current_md_msg["uuid"]

    # 3. Pinned messages (inserted after current.md)
    for pin_line in all_pins:
        try:
            pin_obj = json.loads(pin_line)
            pin_obj["parentUuid"] = current_parent
            pin_obj["uuid"] = generate_uuid()
            pin_obj["timestamp"] = insert_ts
            pin_obj["isPinnedMemory"] = True
            new_lines.append(json.dumps(pin_obj, ensure_ascii=False))
            current_parent = pin_obj["uuid"]
        except:
            new_lines.append(pin_line)

    # 4. ALL messages after compact (not just window!) - excluding old boundary
    first_after_compact = True
    for i in range(compact_end_idx + 1, len(lines)):
        line = lines[i]
        if BOUNDARY_TAG in line:
            continue  # Skip old boundary marker

        try:
            obj = json.loads(line)
            # Update parent chain for first message after our insertions
            if first_after_compact:
                obj["parentUuid"] = current_parent
                first_after_compact = False
            new_lines.append(json.dumps(obj, ensure_ascii=False))
            current_parent = obj.get("uuid", current_parent)
        except:
            new_lines.append(line)

    # 5. New boundary marker at the end (marks where summary coverage ends)
    boundary_msg = create_message(
        f"{BOUNDARY_TAG} — досвід до цього місця вже в summary",
        current_parent, session_id, "user", current_timestamp(),
        {"isBoundaryMarker": True}
    )
    new_lines.append(json.dumps(boundary_msg, ensure_ascii=False))

    # Calculate what we're doing
    messages_after_compact = len(lines) - compact_end_idx - 1
    inserted_count = 1 + len(all_pins)  # current.md + pins
    if not CURRENT_MD.exists():
        inserted_count = len(all_pins)

    if dry_run:
        print(f"\n=== DRY RUN ===")
        print(f"Session: {session_path.name}")
        print(f"Compact summary ends at line: {compact_end_idx}")
        print(f"Messages after compact: {messages_after_compact}")
        print(f"Estimated context tokens: ~{analysis['accumulated_tokens']}")
        print(f"New pins found: {len(new_pins)}")
        print(f"Total pins: {len(all_pins)}")
        print(f"\nNew structure:")
        print(f"  [compact summary: lines 0-{compact_end_idx}]")
        print(f"  [current.md]")
        print(f"  [{len(all_pins)} pinned messages]")
        print(f"  [{messages_after_compact} live messages]")
        print(f"  [boundary marker]")
        print(f"Inserting: {inserted_count} messages + boundary")
        print(f"=== END DRY RUN ===")
        return True

    # Save updated pinned messages
    save_pinned_messages(all_pins)

    # Write new session
    with open(session_path, 'w') as f:
        for line in new_lines:
            f.write(line + '\n')

    print(f"✅ Session surgery complete!")
    print(f"   Inserted: current.md + {len(all_pins)} pins ({len(new_pins)} new)")
    print(f"   Live messages: {messages_after_compact}")
    print(f"   Boundary marker added")

    return True


def find_auto_summary(lines: list) -> tuple[int, int]:
    """
    Find the compact boundary block and return everything from it to the end.

    Since we always slide right after compaction, the compact block is at the end.
    We take EVERYTHING from compact_boundary to the last row of the file.

    Returns (start_idx, end_idx) or (-1, -1) if not found.
    """
    # Find the last compact_boundary row (has parentUuid:null and logicalParentUuid)
    compact_boundary_idx = -1
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
            if obj.get("parentUuid") is None and "logicalParentUuid" in obj:
                compact_boundary_idx = i
        except:
            continue

    if compact_boundary_idx == -1:
        return (-1, -1)

    # Find start: look backwards for permission-mode
    start_idx = compact_boundary_idx
    for i in range(compact_boundary_idx - 1, max(compact_boundary_idx - 3, -1), -1):
        try:
            obj = json.loads(lines[i])
            if obj.get("type") == "permission-mode":
                start_idx = i
                break
        except:
            continue

    # End: take EVERYTHING to the end of the file
    end_idx = len(lines) - 1

    print(f"  [find_auto_summary] Found compact block: lines {start_idx} to {end_idx} ({end_idx - start_idx + 1} rows)")
    return (start_idx, end_idx)


def archive_to_journal(current_md_content: str):
    """Archive current.md content to journal.jsonl before sliding."""
    if not current_md_content.strip():
        return

    entry = {
        "timestamp": current_timestamp(),
        "type": "rolling_summary",
        "content": current_md_content
    }

    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(JOURNAL_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    print(f"  [journal] Archived rolling summary to {JOURNAL_FILE.name}")


def interpolate_timestamp(ts_before: str, ts_after: str, position: int, total: int, debug: bool = False) -> str:
    """Generate a timestamp between two timestamps."""
    try:
        # Parse timestamps
        dt_before = datetime.fromisoformat(ts_before.replace('Z', '+00:00'))
        dt_after = datetime.fromisoformat(ts_after.replace('Z', '+00:00'))

        # Interpolate
        delta = (dt_after - dt_before) / (total + 1)
        result = dt_before + delta * (position + 1)

        result_str = result.isoformat().replace('+00:00', 'Z')

        if debug:
            print(f"  [interpolate] pos={position}/{total}: {ts_before[:19]} -> {result_str[:19]} -> {ts_after[:19]}")

        return result_str
    except Exception as e:
        print(f"  [interpolate ERROR] {e}, ts_before={ts_before}, ts_after={ts_after}")
        return current_timestamp()


def slide_at(session_path: Path, cutoff_uuid: str, dry_run: bool = False) -> bool:
    """
    Perform manual sliding window at specified UUID.

    1. Find the row with cutoff_uuid
    2. Find auto-summary (last parentUuid:null + next)
    3. Insert BEFORE cutoff: auto-summary, current.md, pinned rows
    4. Stitch UUIDs together (cutoff row's parentUuid -> last insertion)
    5. Adjust timestamps
    6. Subtract cache_read_input_tokens from cutoff and subsequent rows
    """
    # Read all lines
    with open(session_path, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # Find cutoff row
    cutoff_idx = -1
    cutoff_cache_tokens = 0
    cutoff_ts = None

    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
            if obj.get("uuid") == cutoff_uuid:
                cutoff_idx = i
                cutoff_ts = obj.get("timestamp")
                # Get cache_read_input_tokens - check this row first, then next assistant
                usage = get_usage_from_message(obj)
                cutoff_cache_tokens = usage.get("cache_read_input_tokens", 0)

                # If this is a user message, get tokens from next assistant
                if cutoff_cache_tokens == 0 and i + 1 < len(lines):
                    try:
                        next_obj = json.loads(lines[i + 1])
                        next_usage = get_usage_from_message(next_obj)
                        cutoff_cache_tokens = next_usage.get("cache_read_input_tokens", 0)
                    except:
                        pass
                break
        except:
            continue

    if cutoff_idx == -1:
        print(f"UUID not found: {cutoff_uuid}", file=sys.stderr)
        return False

    if not cutoff_ts:
        cutoff_ts = current_timestamp()
        print(f"  [WARN] cutoff_ts not found, using current time")

    # Find auto-summary FIRST (we need to know where it is to get correct prev_ts)
    summary_start, summary_end = find_auto_summary(lines)
    if summary_start == -1:
        print("Auto-summary not found (no parentUuid:null row)", file=sys.stderr)
        return False

    # Get UUID and timestamp of row BEFORE the auto-summary block
    # This is the anchor point for stitching timestamps
    prev_uuid = None
    prev_ts = None
    if summary_start > 0:
        try:
            prev_obj = json.loads(lines[summary_start - 1])
            prev_uuid = prev_obj.get("uuid")
            prev_ts = prev_obj.get("timestamp")
        except:
            pass

    if not prev_ts:
        prev_ts = current_timestamp()
        print(f"  [WARN] prev_ts not found, using current time")

    # Since we always slide right after compact, use current time as the end boundary
    # This ensures timestamps always go forward (from prev_ts toward now)
    end_ts = current_timestamp()

    # Ensure prev_ts < end_ts (swap if needed)
    try:
        dt_prev = datetime.fromisoformat(prev_ts.replace('Z', '+00:00'))
        dt_end = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
        if dt_prev > dt_end:
            print(f"  [WARN] prev_ts > end_ts, swapping to ensure forward order")
            prev_ts, end_ts = end_ts, prev_ts
    except:
        pass

    print(f"  [timestamps] prev_ts={prev_ts[:19] if prev_ts else 'None'} (row before summary)")
    print(f"  [timestamps] end_ts={end_ts[:19]} (target end time)")

    # Load pinned messages
    pinned = load_pinned_messages(active_only=True)

    # Load current.md
    current_md_content = ""
    if CURRENT_MD.exists():
        current_md_content = CURRENT_MD.read_text()

    # Load narrative.md (if exists)
    narrative_content = ""
    if NARRATIVE_MD.exists():
        narrative_content = NARRATIVE_MD.read_text()

    # Archive current.md to journal.jsonl before sliding
    if current_md_content and not dry_run:
        archive_to_journal(current_md_content)

    # Get session info
    try:
        cutoff_obj = json.loads(lines[cutoff_idx])
        session_id = cutoff_obj.get("sessionId", "")
    except:
        session_id = ""

    # Build insertion list
    insertions = []

    # 1. Auto-summary/compact block (may be up to 9 rows)
    for i in range(summary_start, summary_end + 1):
        insertions.append(lines[i])

    # 2. current.md (Rolling Summary)
    if current_md_content:
        current_md_msg = create_message(
            f"[Rolling Summary - current.md]\n\n{current_md_content}",
            None,  # Will be set during stitching
            session_id,
            "user",
            None,  # Will be set during stitching
            {"isVesperSummary": True}
        )
        insertions.append(json.dumps(current_md_msg, ensure_ascii=False))

    # 3. narrative.md (Self-Narrative, if exists)
    if narrative_content:
        narrative_msg = create_message(
            f"[Self-Narrative - narrative.md]\n\n{narrative_content}",
            None,  # Will be set during stitching
            session_id,
            "user",
            None,  # Will be set during stitching
            {"isVesperNarrative": True}
        )
        insertions.append(json.dumps(narrative_msg, ensure_ascii=False))

    # 4. Pinned messages
    for pin_line in pinned:
        insertions.append(pin_line)

    # Calculate timestamps for insertions (between prev and cutoff)
    total_insertions = len(insertions)

    # Stitch: update parentUuid chain and timestamps
    stitched_insertions = []
    current_parent = prev_uuid  # Start chain from row BEFORE summary

    print(f"  [stitching] {total_insertions} insertions to process")

    # Generate new session ID for all insertions
    new_session_id = generate_uuid()

    for idx, line in enumerate(insertions):
        try:
            obj = json.loads(line)
            old_ts = obj.get("timestamp", "none")

            # Ensure timestamps go from smaller to larger (idx 0 = earliest)
            new_ts = interpolate_timestamp(prev_ts, end_ts, idx, total_insertions, debug=False)

            # Generate new UUID
            new_uuid = generate_uuid()

            # Update ALL ID fields to prevent duplication
            obj["parentUuid"] = current_parent
            obj["uuid"] = new_uuid

            # Regenerate promptId if present
            if "promptId" in obj:
                obj["promptId"] = generate_uuid()

            # Update sessionId
            if "sessionId" in obj:
                obj["sessionId"] = new_session_id

            # Clear logicalParentUuid (only compact_boundary should have it)
            if "logicalParentUuid" in obj and obj.get("type") != "system":
                del obj["logicalParentUuid"]

            # Always set timestamp (some rows like permission-mode may not have it)
            obj["timestamp"] = new_ts

            # Update nested objects
            if "snapshot" in obj and isinstance(obj["snapshot"], dict):
                obj["snapshot"]["timestamp"] = new_ts
                obj["snapshot"]["messageId"] = new_uuid
            if "messageId" in obj:
                obj["messageId"] = new_uuid

            # Update message.id for assistant messages (format: msg_XXXX...)
            if "message" in obj and isinstance(obj["message"], dict):
                if "id" in obj["message"]:
                    obj["message"]["id"] = generate_message_id()

            # Update pinMetadata if present
            if "pinMetadata" in obj:
                obj["pinMetadata"]["regenerated"] = True
                obj["pinMetadata"]["regenerated_at"] = current_timestamp()

            print(f"  [stitch #{idx}] type={obj.get('type', '?')[:15]} old_ts={old_ts[:19] if old_ts != 'none' else 'none'} -> new_ts={new_ts[:19]}")
            stitched_insertions.append(json.dumps(obj, ensure_ascii=False))
            current_parent = new_uuid
        except Exception as e:
            print(f"  [stitch #{idx} ERROR] {e}")
            stitched_insertions.append(line)

    # Get the last UUID from insertions
    last_insertion_uuid = current_parent

    # Build new lines:
    # 1. Everything BEFORE cutoff (excluding auto-summary, with rewired parents)
    # 2. Stitched insertions
    # 3. Cutoff and everything after (with cutoff's parentUuid -> last insertion, adjusted cache tokens)

    # Find UUID of line before auto-summary (for rewiring the line after it)
    uuid_before_summary = None
    if summary_start > 0:
        try:
            obj_before = json.loads(lines[summary_start - 1])
            uuid_before_summary = obj_before.get("uuid")
        except:
            pass

    # Find UUID of the auto-summary end (to identify which line needs rewiring)
    uuid_summary_end = None
    try:
        obj_summary_end = json.loads(lines[summary_end])
        uuid_summary_end = obj_summary_end.get("uuid")
    except:
        pass

    # First, add lines BEFORE cutoff, skipping auto-summary and rewiring parents
    new_lines = []
    for i in range(cutoff_idx):  # Up to but NOT including cutoff
        if summary_start <= i <= summary_end:
            continue  # Skip auto-summary - it will be inserted before cutoff

        line = lines[i]
        try:
            obj = json.loads(line)
            # Rewire: if this line's parent is the skipped auto-summary end, point to line before summary
            if obj.get("parentUuid") == uuid_summary_end and uuid_before_summary:
                obj["parentUuid"] = uuid_before_summary
                line = json.dumps(obj, ensure_ascii=False)
        except:
            pass
        new_lines.append(line)

    new_lines.extend(stitched_insertions)

    # Process cutoff and everything after
    first_at_cutoff = True
    for i in range(cutoff_idx, len(lines)):  # Starting FROM cutoff
        # Skip auto-summary rows (they're now inserted before cutoff)
        if summary_start <= i <= summary_end:
            continue

        line = lines[i]
        try:
            obj = json.loads(line)

            # Update parentUuid for cutoff row to point to last insertion
            if first_at_cutoff:
                obj["parentUuid"] = last_insertion_uuid
                first_at_cutoff = False

            # Subtract cache_read_input_tokens from cutoff and all subsequent
            if "message" in obj and "usage" in obj["message"]:
                usage = obj["message"]["usage"]
                if "cache_read_input_tokens" in usage:
                    usage["cache_read_input_tokens"] = max(0,
                        usage["cache_read_input_tokens"] - cutoff_cache_tokens)

            new_lines.append(json.dumps(obj, ensure_ascii=False))
        except:
            new_lines.append(line)

    # Stats
    kept_from_cutoff = len(lines) - cutoff_idx - (summary_end - summary_start + 1)
    if summary_start >= cutoff_idx:
        kept_from_cutoff = len(lines) - cutoff_idx  # Auto-summary is after cutoff, not removed

    if dry_run:
        print(f"\n=== DRY RUN: --slide-at {cutoff_uuid[:8]}... ===")
        print(f"Session: {session_path.name}")
        print(f"Insert BEFORE line: {cutoff_idx}")
        print(f"Cache tokens to subtract: {cutoff_cache_tokens}")
        print(f"Auto-summary found at lines: {summary_start}-{summary_end}")
        print(f"\nInserting BEFORE cutoff:")
        print(f"  - Auto-summary: 2 rows")
        print(f"  - current.md: {'yes' if current_md_content else 'no'}")
        print(f"  - Pinned messages: {len(pinned)}")
        print(f"  - Total insertions: {total_insertions}")
        print(f"\nKept from cutoff onward: {kept_from_cutoff} rows")
        print(f"=== END DRY RUN ===")
        return True

    # Write
    with open(session_path, 'w') as f:
        for line in new_lines:
            f.write(line + '\n')

    print(f"✅ Slide complete at {cutoff_uuid[:8]}...")
    print(f"   Inserted BEFORE cutoff: auto-summary + current.md + {len(pinned)} pins")
    print(f"   Kept from cutoff: {kept_from_cutoff} rows")
    print(f"   Subtracted {cutoff_cache_tokens} from cache_read_input_tokens")

    return True


def git_commit_backup(backup_path: Path):
    """Commit backup to git."""
    try:
        subprocess.run(
            ["git", "add", str(backup_path)],
            cwd=AGENT_ROOT, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"Session backup: {backup_path.name}"],
            cwd=AGENT_ROOT, check=True, capture_output=True
        )
        print(f"Committed backup to git")
    except subprocess.CalledProcessError as e:
        print(f"Git commit skipped: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Session surgery with sliding context window")
    parser.add_argument("--dry-run", "-d", action="store_true",
                       help="Show what would be done without making changes")
    parser.add_argument("--session", type=str, help="Path to specific session file")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup creation")
    parser.add_argument("--no-commit", action="store_true", help="Skip git commit of backup")

    # Main operation: slide at specific UUID
    parser.add_argument("--slide-at", type=str, metavar="UUID",
                       help="Insert context BEFORE specified UUID (new session start point)")

    # Legacy auto mode
    parser.add_argument("--auto", action="store_true",
                       help="Auto-detect sliding point (legacy mode)")
    parser.add_argument("--target-pct", type=int, default=DEFAULT_TARGET_PCT,
                       help=f"Target %% for auto mode (default: {DEFAULT_TARGET_PCT})")

    # Pin management
    parser.add_argument("--collect-all-pins", action="store_true",
                       help="Collect all pins from entire session file")
    parser.add_argument("--list-pins", action="store_true",
                       help="List all pinned messages with metadata")
    parser.add_argument("--archive-pin", type=str, metavar="UUID",
                       help="Archive a pin by UUID prefix")

    args = parser.parse_args()

    # Handle pin management commands
    if args.list_pins:
        list_pins()
        sys.exit(0)

    if args.archive_pin:
        success = archive_pin(args.archive_pin)
        sys.exit(0 if success else 1)

    # Find session
    if args.session:
        session_path = Path(args.session)
    else:
        session_path = find_current_session()

    if not session_path or not session_path.exists():
        print("No session file found!", file=sys.stderr)
        sys.exit(1)

    print(f"Session: {session_path.name}")

    # Handle collect-all-pins
    if args.collect_all_pins:
        collect_all_pins(session_path, dry_run=args.dry_run)
        sys.exit(0)

    # Create backup (for any write operation)
    backup_path = None
    if not args.no_backup and not args.dry_run and (args.slide_at or args.auto):
        backup_path = create_backup(session_path)

    # Handle --slide-at (main mode)
    if args.slide_at:
        success = slide_at(session_path, args.slide_at, dry_run=args.dry_run)
        if success and backup_path and not args.no_commit:
            git_commit_backup(backup_path)
        sys.exit(0 if success else 1)

    # Handle --auto (legacy mode)
    if args.auto:
        analysis = analyze_session(session_path, args.target_pct)
        if not analysis:
            sys.exit(1)
        success = perform_surgery(session_path, analysis, dry_run=args.dry_run)
        if success and backup_path and not args.no_commit:
            git_commit_backup(backup_path)
        sys.exit(0 if success else 1)

    # No operation specified - show help
    print("Please specify an operation:")
    print("  --slide-at UUID   Slide at specific row (recommended)")
    print("  --auto            Auto-detect sliding point")
    print("  --collect-all-pins")
    print("  --list-pins")
    print("  --archive-pin UUID")
    sys.exit(1)


if __name__ == "__main__":
    main()
