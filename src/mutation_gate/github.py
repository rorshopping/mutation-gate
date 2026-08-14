"""GitHub PR comment integration (works in Actions via GITHUB_TOKEN)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .model import Report


def render_pr_comment(report: Report, min_score: float | None = None) -> str:
    """Markdown summary suitable for a PR comment."""
    score = report.score
    lines = [
        "## 🧬 Mutation Gate",
        "",
        f"**Mutation score: {f'{score * 100:.1f}%' if score is not None else 'n/a'}** "
        f"({report.killed}/{report.total_counted} mutants killed)",
    ]
    if report.cached:
        lines.append(f"_Cache hits: {report.cached} replayed._")
    lines.append("")
    if report.baseline_failed:
        lines.append("❌ **Baseline test run failed** — fix the suite before trusting tests.")
        return "\n".join(lines)
    if min_score is not None and score is not None:
        ok = score >= min_score
        lines.append(f"Gate (min {min_score * 100:.0f}%): {'✅ **PASS**' if ok else '❌ **FAIL**'}")
        lines.append("")
    survivors = report.surviving()
    if survivors:
        lines.append(f"**{len(survivors)} surviving mutants** — behavior a test should have caught:")
        lines.append("")
        lines.append("| File | Line | Operator | Before → After |")
        lines.append("| --- | --- | --- | --- |")
        for r in survivors[:20]:
            m = r.mutant
            before = (m.before or "").replace("|", "\\|").replace("\n", " ")
            after = (m.after or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{m.file.as_posix()}` | {m.lineno} | `{m.operator}` | `{before}` → `{after}` |")
        if len(survivors) > 20:
            lines.append(f"\n_…and {len(survivors) - 20} more._")
    else:
        lines.append("✅ All mutants killed — the tests have real teeth.")
    return "\n".join(lines)


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def detect_pr() -> tuple[str, int] | None:
    """Return (owner/repo, issue_or_pr_number) from GitHub Actions env, or None."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    number = None
    if event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            event = {}
        number = event.get("pull_request", {}).get("number") or event.get("issue", {}).get("number")
    if number is None:
        number = _env_int("PR_NUMBER")
    if number is None:
        return None
    return repo, number


def post_comment(repo: str, issue_number: int, token: str, body: str) -> bool:
    """POST a comment to the GitHub API; True on success."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "mutation-gate",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        import sys

        print(f"⚠️  Failed to post PR comment: {exc}", file=sys.stderr)
        return False
