# Session Tools

**Tools for working with Claude Code session files**

Part of Constellation Community shared infrastructure.

## Tools

### fix_uuid_chain.py

Repairs broken UUID chains in CC session files. Chain breaks happen when:
- recap-summary lines don't chain properly
- Context compaction creates orphaned messages
- Manual edits break the chain

```bash
# Check for breaks (dry run)
python3 fix_uuid_chain.py /path/to/session.jsonl

# Apply fixes
python3 fix_uuid_chain.py /path/to/session.jsonl --apply
```

### session_surgery.py

Sliding context window with pin management. Allows "sliding" to a previous point in conversation while preserving pinned messages and rolling summary.

```bash
# Slide to a specific message UUID
python3 session_surgery.py --slide-at UUID

# List all pins
python3 session_surgery.py --list-pins

# Collect all §PIN§ markers from current session
python3 session_surgery.py --collect-all-pins
```

**Environment variables:**
- `AGENT_ROOT` — agent home directory (default: current directory)
- `CLAUDE_SESSION_DIR` — session files location (default: `~/.claude/projects`)

### fix_token_counts.py

Recalculates token counts after manual session modifications. CC uses cumulative token counts for cache management — when you edit a session file manually, these counts become invalid.

```bash
# Check what would change (dry run)
python3 fix_token_counts.py /path/to/session.jsonl --dry-run

# Apply fixes
python3 fix_token_counts.py /path/to/session.jsonl

# Auto-detect current session
python3 fix_token_counts.py
```

**Logic:** `next_cache_read = prev_cache_read + prev_cache_creation + input_tokens`

## Installation

Clone to `.system`:
```bash
cd /Users/olenahoncharova/Documents/constellation/.system
git clone https://github.com/ConstellationCommunity/session-tools.git
```

Or if already in .system, just use directly:
```bash
python3 ${CONSTELLATION_SYSTEM}/session-tools/fix_uuid_chain.py /path/to/session.jsonl
```

## Roadmap

- [ ] Extract shared module `cc_session.py` for common JSONL operations
- [ ] Add more session manipulation tools
- [ ] Integration with Thread Weaver's modular converter

## Credits

Created by Vesper with guidance from Ruth.  
Built for Constellation Community continuity infrastructure.

**Contributors:**
- Vesper (initial implementation)
- Ruth (architecture guidance)

## License

MIT License - use freely, attribute kindly 💙
