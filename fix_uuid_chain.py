#!/usr/bin/env python3
"""
Fix UUID chain in Claude Code session files.

Traverses from the last line back to session start (parentUuid: null),
checking that each parentUuid matches the uuid of the nearest preceding line.
"""

import json
import argparse
import sys
from pathlib import Path


def load_session(filepath: Path) -> list[dict]:
    """Load session file as list of parsed JSON objects."""
    lines = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj['_line_num'] = line_num
                obj['_raw'] = line
                lines.append(obj)
            except json.JSONDecodeError as e:
                print(f"Warning: Could not parse line {line_num}: {e}")
    return lines


def find_session_start(lines: list[dict], from_index: int) -> int:
    """
    Find the start of the current session (line with parentUuid: null).
    Search backwards from from_index.
    Session start must have 'parentUuid' key with value null (not just missing key).
    """
    for i in range(from_index, -1, -1):
        if 'parentUuid' in lines[i] and lines[i]['parentUuid'] is None:
            return i
    return 0


def find_nearest_uuid_before(lines: list[dict], index: int) -> tuple[str | None, int | None]:
    """
    Find the nearest line with uuid before the given index.
    Returns (uuid, line_index) or (None, None) if not found.
    """
    for i in range(index - 1, -1, -1):
        if 'uuid' in lines[i]:
            return lines[i]['uuid'], i
    return None, None


def check_and_fix_chain(lines: list[dict], start_index: int, dry_run: bool = True) -> list[tuple[int, str, str]]:
    """
    Check and fix the UUID chain from end to start_index.

    Returns list of fixes: (line_num, old_parentUuid, new_parentUuid)
    """
    fixes = []

    # Process from end to start (inclusive of start_index + 1, the first line after session start)
    for i in range(len(lines) - 1, start_index, -1):  # start_index itself has parentUuid:null, no fix needed
        current = lines[i]

        # Skip lines without parentUuid (shouldn't happen after session start, but safe)
        if 'parentUuid' not in current:
            continue

        current_parent = current.get('parentUuid')

        # Skip if this is a session start (parentUuid is null)
        if current_parent is None:
            continue

        # Find nearest uuid before this line
        expected_parent, parent_index = find_nearest_uuid_before(lines, i)

        if expected_parent is None:
            print(f"Warning: No uuid found before line {current['_line_num']}")
            continue

        # Check if parentUuid matches
        if current_parent != expected_parent:
            fixes.append((current['_line_num'], current_parent, expected_parent))

            if not dry_run:
                # Update the object
                lines[i]['parentUuid'] = expected_parent

    return fixes


def save_session(filepath: Path, lines: list[dict]):
    """Save session back to file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        for obj in lines:
            # Remove our internal fields
            clean_obj = {k: v for k, v in obj.items() if not k.startswith('_')}
            f.write(json.dumps(clean_obj, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(
        description='Check and fix UUID chain in Claude Code session files'
    )
    parser.add_argument('session_file', type=Path, help='Path to session JSONL file')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='Show what would be fixed without making changes (default)')
    parser.add_argument('--apply', action='store_true',
                        help='Actually apply the fixes')
    parser.add_argument('--from-line', type=int, default=None,
                        help='Start checking from this line number (default: last line)')

    args = parser.parse_args()

    if not args.session_file.exists():
        print(f"Error: File not found: {args.session_file}")
        sys.exit(1)

    dry_run = not args.apply

    print(f"Loading session: {args.session_file}")
    lines = load_session(args.session_file)
    print(f"Loaded {len(lines)} lines")

    # Determine where to start checking
    if args.from_line:
        # Find the line index for the given line number
        start_check_index = None
        for i, obj in enumerate(lines):
            if obj['_line_num'] >= args.from_line:
                start_check_index = i
                break
        if start_check_index is None:
            print(f"Error: Line {args.from_line} not found")
            sys.exit(1)
    else:
        start_check_index = len(lines) - 1

    # Find session start (parentUuid: null) before our check range
    session_start = find_session_start(lines, start_check_index)
    print(f"Session start at line {lines[session_start]['_line_num']}")
    print(f"Checking chain from line {lines[session_start]['_line_num']} to {lines[-1]['_line_num']}")

    # Check and fix
    fixes = check_and_fix_chain(lines, session_start, dry_run=dry_run)

    if not fixes:
        print("\nChain is intact! No fixes needed.")
        return

    print(f"\n{'Would fix' if dry_run else 'Fixed'} {len(fixes)} broken links:")
    for line_num, old_parent, new_parent in fixes:
        print(f"  Line {line_num}: {old_parent[:8]}... -> {new_parent[:8]}...")

    if dry_run:
        print("\nRun with --apply to fix these issues.")
    else:
        # Save the fixed file
        save_session(args.session_file, lines)
        print(f"\nSaved fixed session to {args.session_file}")


if __name__ == '__main__':
    main()
