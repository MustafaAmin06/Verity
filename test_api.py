"""Troubleshoot GitHub Models API — mimics the exact scoring call."""
import asyncio
import json
import os

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("GITHUB_TOKEN", "")
MODEL = os.getenv("GITHUB_MODEL", "DeepSeek-V3-0324")
URL = "https://models.inference.ai.azure.com/chat/completions"

SYSTEM = (
    "You are a rigorous source credibility analyst. You read web content and score "
    "how well it supports a specific AI-generated claim. You output ONLY valid JSON — "
    "no explanations, no markdown, no preamble."
)

PROMPT = """CLAIM (the statement the AI made when citing this source):
Cancer is a disease caused by uncontrolled cell growth.

ORIGINAL QUESTION (what the user asked):
why is cancer bad

SOURCE CONTENT (truncated to first 2000 chars):
Cancer is a leading cause of death worldwide, accounting for nearly 10 million deaths in 2020.

TASK: Score this source on two dimensions, then explain.

Respond with ONLY valid JSON:
{
  "relevance_score": <integer 0-100>,
  "alignment_score": <integer 0-100>,
  "claim_aligned": true,
  "reason": "explanation",
  "implication": "what user should do"
}"""


async def main():
    print(f"Token   : {TOKEN[:20]}...{TOKEN[-6:]}" if TOKEN else "Token: NOT SET")
    print(f"Model   : {MODEL}")
    print(f"Endpoint: {URL}")
    print()

    if not TOKEN:
        print("GITHUB_TOKEN is not set in .env")
        return

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT},
        ],
        "temperature": 0.1,
        "max_tokens": 400,
    }

    print("=== REQUEST PAYLOAD ===")
    print(json.dumps(payload, indent=2)[:1000])
    print()

    print("Sending request...")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                URL,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        print(f"=== RESPONSE ===")
        print(f"Status : {r.status_code}")
        print(f"Headers: {dict(r.headers)}")
        print()
        print(f"Body   :")
        print(r.text[:2000])

        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print()
            print("=== PARSED CONTENT ===")
            print(content)
            print()
            try:
                parsed = json.loads(content)
                print("=== JSON PARSE: OK ===")
                print(json.dumps(parsed, indent=2))
            except json.JSONDecodeError as e:
                print(f"=== JSON PARSE FAILED: {e} ===")
    except Exception as e:
        print(f"Exception: {e}")


asyncio.run(main())
