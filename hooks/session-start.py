"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

Configure in .claude/settings.json:
{
    "hooks": {
        "SessionStart": [{
            "matcher": "",
            "command": "uv run python hooks/session-start.py"
        }]
    }
}
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Paths relative to project root
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
DAILY_DIR = ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"

MAX_CONTEXT_CHARS = 20_000
MAX_LOG_LINES = 30
MAX_EXCERPT_CHARS = 200


def get_recent_log() -> str:
    """Read the most recent daily log (today or yesterday)."""
    today = datetime.now(timezone.utc).astimezone()

    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            # Return last N lines to keep context small
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)

    return "(no recent daily log)"


def get_article_excerpt(path: Path) -> str:
    """Extract the first meaningful paragraph after the title heading."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Strip YAML frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()

    # Skip the title heading (first # line)
    lines = content.split("\n")
    body_started = False
    excerpt_lines = []
    for line in lines:
        if not body_started:
            if line.startswith("# "):
                body_started = True
            continue
        # Skip section headings and empty lines at the start
        if not excerpt_lines and (not line.strip() or line.startswith("#")):
            if line.startswith("## "):
                continue  # skip Key Points etc
            if not line.strip():
                continue
        if line.startswith("#"):
            break  # stop at next heading
        if line.strip().startswith("- "):
            break  # stop at bullet lists
        excerpt_lines.append(line)
        if len(" ".join(excerpt_lines)) > MAX_EXCERPT_CHARS:
            break

    excerpt = " ".join(l.strip() for l in excerpt_lines).strip()
    return excerpt[:MAX_EXCERPT_CHARS]


def build_context() -> str:
    """Assemble the context to inject into the conversation."""
    parts = []

    # Today's date
    today = datetime.now(timezone.utc).astimezone()
    parts.append(f"## Today\n{today.strftime('%A, %B %d, %Y')}")

    # Knowledge base index with article excerpts
    if INDEX_FILE.exists():
        index_content = INDEX_FILE.read_text(encoding="utf-8")

        # Build enriched index with excerpts
        excerpts = []
        for subdir in [CONCEPTS_DIR, CONNECTIONS_DIR]:
            if not subdir.exists():
                continue
            for md_file in sorted(subdir.glob("*.md")):
                rel = md_file.relative_to(KNOWLEDGE_DIR)
                slug = str(rel).replace(".md", "")
                excerpt = get_article_excerpt(md_file)
                if excerpt:
                    excerpts.append(f"- **[[{slug}]]**: {excerpt}")

        enriched = index_content
        if excerpts:
            enriched += "\n\n## Article Summaries\n\n" + "\n".join(excerpts)

        parts.append(f"## Knowledge Base Index\n\n{enriched}")
    else:
        parts.append("## Knowledge Base Index\n\n(empty - no articles compiled yet)")

    # Retrieval instructions
    kb_path = KNOWLEDGE_DIR.resolve()
    compiler_root = ROOT.resolve()
    parts.append(f"""## Knowledge Base Retrieval Instructions

You have a compiled knowledge base at `{kb_path}/`. The index above lists every article with a one-line summary.

**When to retrieve:** Before answering questions that overlap with topics in the index, READ the relevant article files in full. The index is a table of contents — the actual knowledge (gotchas, decisions, patterns, details) lives in the articles.

**How to retrieve:**
1. Scan the index for articles relevant to the current topic
2. Use the Read tool to read the full article(s) from `{kb_path}/concepts/` or `{kb_path}/connections/`
3. Use the article content to inform your answer

**When to file back:** If a conversation produces a novel answer worth keeping, run:
```
cd "{compiler_root}" && uv run python scripts/query.py "question" --file-back
```
This creates a Q&A article in `{kb_path}/qa/` and updates the index — compounding the knowledge base.""")

    # Recent daily log
    recent_log = get_recent_log()
    parts.append(f"## Recent Daily Log\n\n{recent_log}")

    context = "\n\n---\n\n".join(parts)

    # Truncate if too long
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"

    return context


def main():
    # Skip injection when running via pi-claude-bridge (pi-kb handles memory there)
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1":
        print(json.dumps({}))
        return

    context = build_context()

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
