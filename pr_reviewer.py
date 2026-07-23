"""
PR Guardian — Pull Request Command Center
==========================================

Calm Engineering review engine. Fetches a pull request's diff, analyzes it
in file-safe chunks with Gemini, and posts the results back to GitHub as:

  1. Inline, line-level suggestions (via the Pull Request Review API),
     each with a one-click "suggestion" block where a fix is proposed.
  2. A single root-level Command Center summary (Merge Readiness Score,
     estimated review time saved, files-changed overview, and a short
     natural-language note on architectural impact) attached as the body
     of that same review.

Both are delivered in one API call: POST /pulls/{number}/reviews accepts
a `body` (the summary) alongside a `comments` array (the inline threads),
so the developer sees one cohesive review rather than a scattered set of
separate comments.
"""

import os
import re
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# Diff chunking configuration. Diffs are grouped by whole-file boundaries
# and never split mid-file unless a single file's diff alone exceeds the
# threshold, in which case it is analyzed on its own.
MAX_LINES_PER_CHUNK = 2000

# Point deduction applied per finding when computing the Merge Readiness
# Score. Kept deterministic and transparent (computed in Python, not by
# the model) so the score can't drift or be mis-stated by the LLM.
CATEGORY_WEIGHTS = {
    "Security Enhancement": 8,
    "Architectural Observation": 4,
    "Merge Readiness Suggestion": 2,
}
CATEGORY_EMOJI = {
    "Security Enhancement": "🌿",
    "Architectural Observation": "🧭",
    "Merge Readiness Suggestion": "💡",
}

GITHUB_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


# ---------------------------------------------------------------------------
# GitHub fetch helpers
# ---------------------------------------------------------------------------

def fetch_pr_metadata(owner, repo, pr_number):
    """Fetches the PR's JSON metadata (title, head sha, additions, etc.)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    resp = requests.get(url, headers=GITHUB_HEADERS)
    resp.raise_for_status()
    return resp.json()


def fetch_pr_diff(owner, repo, pr_number):
    """Fetches the raw unified diff for the PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = dict(GITHUB_HEADERS)
    headers["Accept"] = "application/vnd.github.v3.diff"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Diff parsing: split into per-file blocks, extract paths, and compute
# which line numbers on the "new file" (RIGHT) side are actually part of
# the diff. GitHub's Review API will reject a comment on any line that
# isn't part of a diff hunk, so this is also used as a safety filter.
# ---------------------------------------------------------------------------

def split_diff_into_file_blocks(diff_text):
    """Splits a full unified diff into a list of raw per-file diff blocks."""
    lines = diff_text.split("\n")
    blocks = []
    current = []
    for line in lines:
        if line.startswith("diff --git ") and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if b.strip()]


def extract_file_path(block):
    """Pulls the post-change file path out of a diff block's '+++' line."""
    match = re.search(r"^\+\+\+ (.+)$", block, re.MULTILINE)
    if not match:
        return None
    path = match.group(1).strip()
    if path == "/dev/null":
        return None  # file was deleted; nothing to annotate
    if path.startswith("b/"):
        path = path[2:]
    return path


def extract_display_name(block):
    """Pulls the a/... b/... file names for the human-readable overview,
    even for files (deletions, binaries) that can't receive comments."""
    match = re.search(r"^diff --git a/(.+?) b/(.+)$", block, re.MULTILINE)
    if match:
        return match.group(2)
    return "unknown file"


def is_binary_block(block):
    return "Binary files " in block or "GIT binary patch" in block


def parse_valid_lines(block):
    """Returns the set of new-file (RIGHT-side) line numbers that are part
    of the diff hunks in this block, i.e. lines GitHub will accept an
    inline review comment against."""
    valid = set()
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    new_line = None
    for line in block.split("\n"):
        hunk_match = hunk_re.match(line)
        if hunk_match:
            new_line = int(hunk_match.group(1))
            continue
        if new_line is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            valid.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            continue  # removed line does not exist in the new file
        elif line.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            valid.add(new_line)  # context line
            new_line += 1
    return valid


def build_file_blocks(diff_text):
    """Parses the full diff into structured blocks with path, commentable
    lines, and line count, ready for chunking."""
    raw_blocks = split_diff_into_file_blocks(diff_text)
    file_blocks = []
    changed_file_names = []
    for block in raw_blocks:
        changed_file_names.append(extract_display_name(block))
        path = extract_file_path(block)
        if path is None or is_binary_block(block):
            continue  # deleted or binary file: not eligible for inline comments
        file_blocks.append(
            {
                "path": path,
                "block": block,
                "line_count": len(block.split("\n")),
                "valid_lines": parse_valid_lines(block),
            }
        )
    return file_blocks, changed_file_names


def chunk_file_blocks(file_blocks, max_lines=MAX_LINES_PER_CHUNK):
    """Groups file blocks into chunks that respect file boundaries and stay
    under max_lines, except a single oversized file is isolated in its own
    chunk rather than being split mid-hunk."""
    chunks = []
    current, current_lines = [], 0
    for fb in file_blocks:
        if current and current_lines + fb["line_count"] > max_lines:
            chunks.append(current)
            current, current_lines = [], 0
        current.append(fb)
        current_lines += fb["line_count"]
        if fb["line_count"] > max_lines:
            chunks.append(current)
            current, current_lines = [], 0
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Gemini calls
# ---------------------------------------------------------------------------

def call_gemini(payload, retries=2, backoff_seconds=2, json_mode=True):
    """Low-level Gemini call with light retry on transient failures.

    The API key is sent via the `x-goog-api-key` header rather than as a
    `?key=` query parameter, so it never ends up in logs, proxies, or
    error messages that echo back the request URL.

    When `json_mode` is True, Gemini's native structured-output mode is
    requested (`responseMimeType: application/json`), so the model itself
    is constrained to emit valid JSON instead of relying on prompt
    instructions alone. This is left False for calls (like the narrative
    generator) that intentionally want free-form prose back.
    """
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    request_payload = payload
    if json_mode:
        request_payload = dict(payload)
        generation_config = dict(request_payload.get("generationConfig", {}))
        generation_config["responseMimeType"] = "application/json"
        request_payload["generationConfig"] = generation_config

    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(GEMINI_URL, json=request_payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:  # noqa: BLE001 - we want to retry broadly here
            last_error = exc
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
    raise RuntimeError(f"Gemini request failed after retries: {last_error}")


def build_review_prompt(chunk_text):
    return (
        "You are a calm, supportive senior staff engineer reviewing a pull "
        "request. Your tone is objective, educational, and reassuring — "
        "never alarmist. Do not use words like 'danger' or 'critical'.\n\n"
        "Review the following diff, which may contain one or more files. "
        "For each observation, identify the exact file and the exact "
        "new-file line number it applies to (the line number after the "
        "change is applied, matching the '+' or unchanged lines in the "
        "diff — never a removed '-' line).\n\n"
        "Return ONLY a JSON array (no prose, no Markdown fences) of "
        "objects with exactly these fields:\n"
        '  - "file_path": the file path as shown after the "+++ b/" marker\n'
        '  - "line_number": integer new-file line number\n'
        '  - "category": one of "Security Enhancement", '
        '"Architectural Observation", "Merge Readiness Suggestion"\n'
        '  - "message": one or two calm, specific, educational sentences\n'
        '  - "suggested_fix": the corrected code for that line/snippet, '
        'or an empty string "" if no concrete fix applies\n\n'
        "If the diff has nothing worth noting, return an empty array [].\n\n"
        f"Diff:\n{chunk_text}"
    )


def build_narrative_prompt(pr_title, changed_file_names, findings):
    finding_lines = "\n".join(
        f"- [{f.get('category')}] {f.get('file_path')}:{f.get('line_number')} — {f.get('message')}"
        for f in findings
    ) or "(no notable findings)"
    files_preview = ", ".join(changed_file_names[:15])
    if len(changed_file_names) > 15:
        files_preview += f", and {len(changed_file_names) - 15} more"

    return (
        "You are a calm, supportive senior staff engineer writing a short "
        "executive summary for a pull request review. Write 2 to 4 "
        "sentences, plain prose, no headers, no bullet points, no emoji. "
        "Be objective and reassuring, describing the architectural impact "
        "of the change and how the observations below relate to it. Do "
        "not invent a numeric score; a score is shown separately.\n\n"
        f"PR title: {pr_title}\n"
        f"Files changed: {files_preview}\n"
        f"Observations:\n{finding_lines}\n"
    )


def analyze_chunk(chunk_text):
    """Sends one diff chunk to Gemini and returns a parsed list of findings.
    Returns an empty list (rather than raising) on failure, so one bad
    chunk cannot take down the whole review."""
    payload = {"contents": [{"parts": [{"text": build_review_prompt(chunk_text)}]}]}
    try:
        raw_text = call_gemini(payload)
        # Even with native JSON mode requested, be defensive: pull out the
        # first top-level JSON array from the response with a regex rather
        # than assuming the text is already clean. This means stray
        # markdown fences (```json ... ```) or preamble sentences the
        # model might still add can never crash json.loads().
        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if not match:
            print("⚠️  Chunk analysis failed: no JSON array found in response, skipping this chunk.")
            return []
        findings = json.loads(match.group(0))
        if not isinstance(findings, list):
            return []
        return findings
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Chunk analysis failed, skipping this chunk: {exc}")
        return []


def generate_narrative(pr_title, changed_file_names, findings):
    payload = {
        "contents": [
            {"parts": [{"text": build_narrative_prompt(pr_title, changed_file_names, findings)}]}
        ]
    }
    try:
        raw_text = call_gemini(payload, json_mode=False)
        return raw_text.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Narrative generation failed, falling back to a generic note: {exc}")
        return (
            "This change was reviewed automatically. See the inline notes "
            "below for specifics; nothing here should block a thoughtful "
            "human pass before merge."
        )


# ---------------------------------------------------------------------------
# Scoring & estimation (computed deterministically, not by the LLM)
# ---------------------------------------------------------------------------

def compute_merge_readiness_score(findings):
    score = 100
    for finding in findings:
        score -= CATEGORY_WEIGHTS.get(finding.get("category"), 3)
    return max(0, min(100, score))


def estimate_time_saved_minutes(additions, deletions):
    total_lines = (additions or 0) + (deletions or 0)
    minutes = round(total_lines / 12)  # heuristic: ~12 changed lines/minute of manual triage
    return max(2, minutes)


# ---------------------------------------------------------------------------
# Filtering: keep only findings that land on a line GitHub will accept
# ---------------------------------------------------------------------------

def filter_valid_findings(findings, valid_lines_by_path):
    valid, dropped = [], 0
    for finding in findings:
        path = finding.get("file_path")
        line = finding.get("line_number")
        valid_lines = valid_lines_by_path.get(path)
        if valid_lines is None and path:
            # Model may have echoed the path without the leading "a/"/"b/",
            # or included a partial match; try a light reconciliation pass.
            for known_path in valid_lines_by_path:
                if known_path.endswith(path) or path.endswith(known_path):
                    valid_lines = valid_lines_by_path[known_path]
                    finding["file_path"] = known_path
                    break
        if valid_lines is not None and isinstance(line, int) and line in valid_lines:
            valid.append(finding)
        else:
            dropped += 1
    if dropped:
        print(f"ℹ️  Dropped {dropped} finding(s) that didn't map onto a commentable diff line.")
    return valid


# ---------------------------------------------------------------------------
# Formatting: inline suggestions + Command Center summary
# ---------------------------------------------------------------------------

def format_inline_comment(finding):
    category = finding.get("category", "Merge Readiness Suggestion")
    emoji = CATEGORY_EMOJI.get(category, "📝")
    message = finding.get("message", "").strip()
    suggested_fix = (finding.get("suggested_fix") or "").strip()

    body = f"**{emoji} {category}**\n\n{message}"
    if suggested_fix:
        body += f"\n\n```suggestion\n{suggested_fix}\n```"

    return {
        "path": finding["file_path"],
        "line": finding["line_number"],
        "side": "RIGHT",
        "body": body,
    }


def build_summary_body(pr_title, score, minutes_saved, changed_file_names, findings, narrative):
    files_overview = ", ".join(changed_file_names[:20])
    if len(changed_file_names) > 20:
        files_overview += f", and {len(changed_file_names) - 20} more"

    counts = {}
    for finding in findings:
        category = finding.get("category", "Merge Readiness Suggestion")
        counts[category] = counts.get(category, 0) + 1

    if counts:
        breakdown_rows = "\n".join(
            f"| {CATEGORY_EMOJI.get(cat, '📝')} {cat} | {count} |"
            for cat, count in counts.items()
        )
        breakdown = f"| Observation Type | Count |\n| --- | --- |\n{breakdown_rows}"
    else:
        breakdown = "No observations this round — nothing stood out."

    return (
        f"## 🧭 PR Guardian — Command Center Summary\n\n"
        f"**Merge Readiness Score:** {score}/100\n"
        f"**Estimated Review Time Saved:** ~{minutes_saved} minutes\n"
        f"**Files Reviewed:** {len(changed_file_names)} ({files_overview})\n\n"
        f"{narrative}\n\n"
        f"{breakdown}\n\n"
        f"_Inline notes, where applicable, appear directly on the affected "
        f"lines below — each with a one-click suggestion where a concrete "
        f"fix was available._"
    )


# ---------------------------------------------------------------------------
# Posting the review
# ---------------------------------------------------------------------------

def post_review(owner, repo, pr_number, commit_id, body, comments):
    """Posts the review to GitHub's Review API.

    GitHub's Review API is atomic: if a single entry in `comments` maps
    onto a line it considers invalid (stale diff, race with a new push,
    an edge case our own line-validation missed), the API rejects the
    ENTIRE review with a 422 — inline notes and summary body alike. That
    would silently drop the Command Center summary even though it had
    nothing wrong with it, so on a 422 we retry once with a summary-only
    payload (no `comments`) to make sure the summary is never lost.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    payload = {"commit_id": commit_id, "body": body, "event": "COMMENT"}
    if comments:
        payload["comments"] = comments

    resp = requests.post(url, json=payload, headers=GITHUB_HEADERS)
    if resp.status_code in (200, 201):
        print("✅ Command Center review posted successfully.")
        return

    if resp.status_code == 422 and comments:
        print(
            "⚠️  Review with inline comments was rejected (422), likely due to "
            "a stale or invalid line mapping. Retrying with summary only so "
            "the Command Center summary isn't lost..."
        )
        fallback_payload = {"commit_id": commit_id, "body": body, "event": "COMMENT"}
        fallback_resp = requests.post(url, json=fallback_payload, headers=GITHUB_HEADERS)
        if fallback_resp.status_code in (200, 201):
            print("✅ Command Center summary posted successfully (inline comments dropped).")
        else:
            print(f"❌ Fallback summary-only post also failed. Code: {fallback_resp.status_code}")
            print(fallback_resp.text)
        return

    print(f"❌ Failed to post review. Code: {resp.status_code}")
    print(resp.text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    github_repo = os.getenv("GITHUB_REPOSITORY")
    pr_num = os.getenv("PR_NUMBER")

    if not github_repo or not pr_num:
        print("⚠️  Missing GitHub environment variables. Cannot run.")
        sys.exit(1)

    owner, repo = github_repo.split("/")

    print(f"🔍 Fetching metadata and diff for PR #{pr_num}...")
    pr_json = fetch_pr_metadata(owner, repo, pr_num)
    diff_text = fetch_pr_diff(owner, repo, pr_num)

    commit_id = pr_json.get("head", {}).get("sha")
    pr_title = pr_json.get("title", "this pull request")
    additions = pr_json.get("additions", 0)
    deletions = pr_json.get("deletions", 0)

    if not diff_text.strip():
        post_review(
            owner,
            repo,
            pr_num,
            commit_id,
            "## 🧭 PR Guardian — Command Center Summary\n\n"
            "This PR has no reviewable text diff (binary-only change, or "
            "nothing to compare). Nothing further to check here.",
            [],
        )
        return

    file_blocks, changed_file_names = build_file_blocks(diff_text)
    valid_lines_by_path = {fb["path"]: fb["valid_lines"] for fb in file_blocks}

    chunks = chunk_file_blocks(file_blocks)
    print(f"📦 Diff split into {len(chunks)} chunk(s) across {len(file_blocks)} file(s).")

    all_findings = []
    for i, chunk in enumerate(chunks, start=1):
        chunk_text = "\n".join(fb["block"] for fb in chunk)
        print(f"🤖 Analyzing chunk {i}/{len(chunks)} ({len(chunk)} file(s))...")
        all_findings.extend(analyze_chunk(chunk_text))

    valid_findings = filter_valid_findings(all_findings, valid_lines_by_path)

    score = compute_merge_readiness_score(valid_findings)
    minutes_saved = estimate_time_saved_minutes(additions, deletions)

    print("🧭 Drafting the Command Center narrative...")
    narrative = generate_narrative(pr_title, changed_file_names, valid_findings)

    inline_comments = [format_inline_comment(f) for f in valid_findings]
    summary_body = build_summary_body(
        pr_title, score, minutes_saved, changed_file_names, valid_findings, narrative
    )

    print("🚀 Posting the Command Center review to GitHub...")
    post_review(owner, repo, pr_num, commit_id, summary_body, inline_comments)


if __name__ == "__main__":
    main()
    