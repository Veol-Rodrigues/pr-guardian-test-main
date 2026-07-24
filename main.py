import os
import re
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"

class CodePayload(BaseModel):
    code: str

class PRPayload(BaseModel):
    pr_url: str

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open("index.html", "r") as f:
        return f.read()

async def _call_gemini(prompt: str):
    gemini_payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    resp = requests.post(GEMINI_URL, json=gemini_payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

@app.post("/api/analyze")
async def analyze_code(payload: CodePayload):
    prompt = (
        "You are a Calm Engineering code reviewer. Review this code. "
        "Provide a Merge Readiness Score (0-100) and a brief, calm architectural summary. "
        f"Code to review:\n{payload.code}"
    )
    try:
        ai_response = await _call_gemini(prompt)
        return {"status": "success", "analysis": ai_response}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/analyze-pr")
async def analyze_pr(payload: PRPayload):
    match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", payload.pr_url)
    if not match:
        return {"status": "error", "message": "Invalid GitHub PR URL. Example format: https://github.com/owner/repo/pull/123"}
    
    owner, repo, pr_number = match.groups()
    github_api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {"Accept": "application/vnd.github.v3.diff"}
    
    try:
        gh_resp = requests.get(github_api_url, headers=headers)
        gh_resp.raise_for_status()
        diff_text = gh_resp.text
        
        if not diff_text.strip():
            return {"status": "error", "message": "Pull request diff is empty or inaccessible."}
            
        diff_lines = diff_text.split('\n')
        if len(diff_lines) > 2000:
            diff_text = '\n'.join(diff_lines[:2000]) + "\n\n... [Diff truncated for analysis] ..."
            
        prompt = (
            "You are a Calm Engineering code reviewer. Review this pull request diff. "
            "Provide a Merge Readiness Score (0-100) and a brief, calm architectural summary. "
            f"Diff to review:\n{diff_text}"
        )
        ai_response = await _call_gemini(prompt)
        return {"status": "success", "analysis": ai_response}
    except Exception as e:
        return {"status": "error", "message": f"PR analysis failed: {str(e)}"}
