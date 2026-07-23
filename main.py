import os
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
class CodePayload(BaseModel):
    code: str

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open("index.html", "r") as f:
        return f.read()

@app.post("/api/analyze")
async def analyze_code(payload: CodePayload):
    prompt = (
        "You are a Calm Engineering code reviewer. Review this code. "
        "Provide a Merge Readiness Score (0-100) and a brief, calm architectural summary. "
        f"Code to review:\n{payload.code}"
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
