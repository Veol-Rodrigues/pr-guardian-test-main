import os
import re
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"

class CodePayload(BaseModel):
    code: str

class PRPayload(BaseModel):
    pr_url: str

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open("index.html", "r") as f:
        return f.read()

@app.post("/api/analyze")
async def analyze_code(payload: CodePayload):
    return await _analyze_text(payload.code, is_pr=False)

@app.post("/api/analyze-pr")
async def analyze_pr(payload: PRPayload):
    # Extract GitHub PR details from URL (e.g., https://github.com/facebook/react/pull/12345)
    match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", payload.pr_url)
    if not match:
        return {"status": "error", "message": "Invalid GitHub PR URL. Make sure it looks like https://github.com/owner/repo/pull/123"}
    
    owner, repo, pr_number = match.groups()
    
    # Fetch diff from GitHub API
    github_api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {"Accept": "application/vnd.github.v3.diff"}
    
    try:
        gh_resp = requests.get(github_api_url, headers=headers)
        gh_resp.raise_for_status()
        diff_text = gh_resp.text
        
        if not diff_text.strip():
            return {"status": "error", "message": "Pull request has no diff or is not accessible."}
            
        # Truncate massive PRs to prevent context overload
        diff_lines = diff_text.split('\n')
        if len(diff_lines) > 2000:
            diff_text = '\n'.join(diff_lines[:2000]) + "\n\n... [Diff truncated for demo preview] ..."
            
        return await _analyze_text(diff_text, is_pr=True)
    except Exception as e:
        return {"status": "error", "message": f"Failed to fetch PR diff: {str(e)}"}

async def _analyze_text(text: str, is_pr: bool):
    context_type = "pull request diff" if is_pr else "code snippet"
    prompt = (
        "You are a Calm Engineering code reviewer. Review this code. "
        "Provide a Merge Readiness Score (0-100) and a brief, calm architectural summary. "
        f"Here is the {context_type}:\n{text}"
    )

    gemini_payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    try:
        resp = requests.post(GEMINI_URL, json=gemini_payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        ai_response = data["candidates"][0]["content"]["parts"][0]["text"]
        return {"status": "success", "analysis": ai_response}
    except Exception as e:
        return {"status": "error", "message": str(e)}
