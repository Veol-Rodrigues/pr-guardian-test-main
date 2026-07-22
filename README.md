# 🧭 PR Guardian — Pull Request Command Center

**A calm, AI-powered co-reviewer that reads every Pull Request, leaves precise inline notes on the exact lines that matter, and gives you a one-glance Merge Readiness Score — so you can decide with confidence, not anxiety.**

Built as a lightweight **GitHub Action** in Python, powered by the **Google Gemini API**. No servers to run, no infrastructure to manage — drop two files into your repository and every future PR gets a quiet, thorough review.

---

## 🌿 Our Philosophy: Calm Engineering

Most automated review tools are built to alarm you — red banners, "CRITICAL" labels, a wall of warnings that all look equally urgent. That's noise, not signal, and it trains people to skim past the tool entirely.

PR Guardian is built on **Calm Engineering** instead: it reviews the way a good senior staff engineer does — objective, specific, educational, and unhurried. It never tells you something is "dangerous." It tells you what it noticed, why it matters, and — where possible — exactly what to change, with a button to change it.

- **No alarmist language.** You won't see "Critical" or "Danger" anywhere in a PR Guardian comment.
- **A Merge Readiness Score, not a Risk Score.** 95/100 tells you where you stand without triggering a fight-or-flight response.
- **Findings framed as observations, not accusations:** *Security Enhancement*, *Architectural Observation*, or *Merge Readiness Suggestion* — never "violation" or "error."

---

## ✨ Features

- **Zero-infrastructure deployment.** Runs entirely inside GitHub Actions — no servers, no webhooks, no hosting cost.
- **Automatic triggering.** Reviews fire on every `opened`, `synchronize`, and `reopened` Pull Request event.
- **Inline, line-level notes.** Findings are posted directly on the exact line they concern, using GitHub's Pull Request Review API — no more scrolling to find what a comment is about.
- **Click-to-commit fixes.** Where a concrete correction exists, PR Guardian proposes it as a GitHub `suggestion` block. Apply it with a single click, no copy-pasting.
- **Handles large PRs gracefully.** The diff is chunked cleanly along file boundaries and analyzed in safe-sized batches, so a big PR degrades gracefully instead of failing outright.
- **The Command Center Summary.** One root-level comment ties it all together: a Merge Readiness Score, an estimated review time saved, a files-changed overview, and a short natural-language note on the change's architectural impact.
- **Calm when clean.** If nothing stands out, PR Guardian still posts a short, reassuring note — silence would otherwise look like a broken workflow.
- **Secure by design.** API keys are stored as encrypted GitHub Secrets and never touch the codebase.
- **Free to run.** Uses GitHub Actions' free tier and the Gemini API's free tier.

---

## 🏗️ Architecture

PR Guardian is an event-driven, serverless pipeline:

```
Developer opens a PR
        │
        ▼
GitHub Actions triggers review.yml
        │
        ▼
Ubuntu runner starts
        │
        ▼
Python + dependencies installed
        │
        ▼
pr_reviewer.py executes
        │
        ├──▶ Fetches PR metadata + diff via GitHub REST API
        │
        ├──▶ Splits the diff into file-safe chunks
        │
        ├──▶ Sends each chunk to Gemini for structured, line-level findings
        │
        ├──▶ Filters findings to lines GitHub can actually annotate
        │
        ├──▶ Computes a Merge Readiness Score and time-saved estimate
        │
        ├──▶ Asks Gemini for a short architectural-impact narrative
        │
        └──▶ Posts one Review: inline suggestions + Command Center summary
```

**Components**

| Layer | Technology | Role |
|---|---|---|
| Trigger | GitHub Actions (`pull_request` event) | Detects new / updated PRs |
| Runtime | Ubuntu runner + Python 3.10 | Executes the review script |
| Data source | GitHub REST API | Provides PR metadata and the raw diff |
| AI engine | Google Gemini API | Analyzes diff chunks and drafts the narrative summary |
| Delivery | GitHub Pull Request Review API | Posts inline suggestions and the root summary in one review |
| Secrets | GitHub Actions Secrets | Stores `GEMINI_API_KEY` securely |

For the full design rationale — including how chunking, line-mapping, and scoring work — see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 📁 Project Structure

```
your-repo/
├── .github/
│   └── workflows/
│       └── review.yml       # GitHub Actions workflow definition
└── pr_reviewer.py           # The review engine
```

---

## 🚀 Setup Instructions

Setting up PR Guardian on your own repository takes about 5 minutes.

### Step 1 — Get a Gemini API key

1. Go to **[Google AI Studio](https://aistudio.google.com/app/apikey)** and sign in.
2. Click **Create API key** and copy the key. Keep this tab open for the next step.

> ⚠️ Treat this key like a password. Never paste it into your code or commit it to Git.

### Step 2 — Add the key as a repository secret

1. Open your repository on GitHub.
2. Click **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. Set the following:
   - **Name:** `GEMINI_API_KEY` (must match this spelling exactly)
   - **Secret:** paste your key from Step 1
5. Click **Add secret**.

### Step 3 — Add the two project files

Copy these two files into your repository at these exact paths:

- `.github/workflows/review.yml`
- `pr_reviewer.py`

Commit and push them to your `main` branch.

### Step 4 — Confirm workflow permissions

PR Guardian needs permission to post reviews (including inline comments) on Pull Requests.

1. Go to **Settings** → **Actions** → **General**.
2. Under **Workflow permissions**, ensure **Read and write permissions** is selected.
3. Save.

*(The `review.yml` file already declares the specific permissions it needs — `contents: read` and `pull-requests: write` — this global setting simply allows those permissions to be granted.)*

### Step 5 — Test it

1. Create a new branch: `git checkout -b test-pr-guardian`
2. Make a small code change (bonus: paste a fake API key like `api_key = "sk-fake123abcxyz"` to see the review in action).
3. Push the branch and open a Pull Request against `main`.
4. Watch the **Actions** tab — the workflow will run in under a minute for typical PRs.
5. Refresh the PR page. You'll see a Command Center summary at the top of the conversation, plus inline notes on the specific lines PR Guardian noticed something about.

✅ You're done. PR Guardian will now review every future Pull Request automatically.

---

## 📖 Usage

Once set up, PR Guardian requires **no ongoing action** — it runs itself. Every time a Pull Request is opened, updated, or reopened, it posts one review made up of two parts:

**The Command Center summary**, at the top:

> ### 🧭 PR Guardian — Command Center Summary
>
> **Merge Readiness Score:** 92/100
> **Estimated Review Time Saved:** ~14 minutes
> **Files Reviewed:** 4 (`auth/session.py`, `api/routes.py`, ...)
>
> This change extends session handling with a new token-refresh path. The additions are well-isolated and don't touch existing call sites, though one endpoint would benefit from explicit input validation before it reaches production traffic.
>
> | Observation Type | Count |
> | --- | --- |
> | 🌿 Security Enhancement | 1 |
> | 💡 Merge Readiness Suggestion | 2 |

**Inline notes**, directly on the affected lines:

> **🌿 Security Enhancement**
>
> This value is read directly from the request without validation. Confirming its shape before use would prevent a malformed request from reaching the token store.
>
> ```suggestion
> token = validate_token_format(request.args.get("token"))
> ```

Or, if the diff is clean:

> ### 🧭 PR Guardian — Command Center Summary
>
> **Merge Readiness Score:** 100/100
>
> Nothing stood out in this change — it's ready when you are.

To **skip** a review for a specific PR, close and reopen it after adding `[skip ci]` to the commit message, or disable the workflow temporarily under **Actions**.

---

## 🔧 Configuration

The default setup works out of the box, but you can customize:

- **Change the model.** Set the `GEMINI_MODEL` environment variable in `review.yml`, or edit the default in `pr_reviewer.py`.
- **Refine the review tone or focus.** Edit `build_review_prompt()` in `pr_reviewer.py` to focus reviews on your team's priorities (e.g., "focus on Python type hints and docstrings").
- **Adjust chunk size.** Edit `MAX_LINES_PER_CHUNK` in `pr_reviewer.py` if your PRs are unusually large or your model's context window differs.
- **Restrict trigger events.** Edit the `types` list in `review.yml` — for example, remove `synchronize` if you only want reviews on the initial PR open.
- **Add path filters.** Add a `paths` key under `pull_request` in `review.yml` to only review changes to specific folders.
- **Retune the Merge Readiness Score.** Edit `CATEGORY_WEIGHTS` in `pr_reviewer.py` if you want certain observation types to weigh more or less heavily.

---

## 🐛 Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Workflow doesn't run | Actions disabled on repo | Settings → Actions → General → enable |
| "Resource not accessible by integration" | Workflow lacks write permission | Settings → Actions → General → allow read/write |
| Summary appears but no inline notes | Findings didn't map onto a commentable diff line | Check the Actions log for "Dropped N finding(s)" — this is expected occasionally and is handled safely |
| `RuntimeError: Gemini request failed after retries` | Gemini API key invalid, over quota, or a transient outage | Regenerate key; check Google AI Studio for quota |
| Review posts with an empty body only | All chunks failed analysis | Check the Actions log for `⚠️ Chunk analysis failed` messages |
| Empty diff | PR contains only binary changes | Expected — PR Guardian reviews text diffs only |

---

## 🔐 Security Notes

- The `GEMINI_API_KEY` is stored as a GitHub Secret, encrypted at rest, and never logged in workflow output.
- The workflow uses the built-in `GITHUB_TOKEN`, which is automatically scoped to the current repository and expires when the job ends.
- Permissions are minimized in `review.yml`: `contents: read` and `pull-requests: write` — nothing more.
- The AI receives only code diffs, in bounded chunks — never the full repository.

---

## 🛠️ Tech Stack

- **Python 3.10** — script runtime
- **GitHub Actions** — CI/CD trigger and runner
- **GitHub REST API** — PR metadata, diff retrieval, and the Pull Request Review API for delivery
- **Google Gemini API** — AI code analysis and narrative summarization
- **`requests`** — HTTP client
- **`python-dotenv`** — local environment variable loading

---

## 📈 Roadmap Ideas

- Multi-language rule packs (Python, JavaScript, Go, etc.)
- Dashboard tracking review history, Merge Readiness trends, and time saved over time
- Team-configurable tone presets (e.g., stricter for `main`, gentler for draft PRs)
- Fallback to another LLM provider when Gemini is rate-limited
- Optional `REQUEST_CHANGES` review event for teams that want a hard gate on the lowest scores

---

## 📄 License

MIT — free to use, modify, and adapt for your own team.

---

## 👥 Credits

Built by Team EchoForge as an intermediate-to-advanced Gen AI × Cloud capstone project.
