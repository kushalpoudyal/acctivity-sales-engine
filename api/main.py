import os
from pathlib import Path

import openai
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

load_dotenv(Path(__file__).with_name(".env"))

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"

app = FastAPI(title="Hack A API", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hack A API is running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/openai/test")
def openai_test() -> dict[str, str]:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set in .env")

    model = os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    try:
        response = openai.OpenAI().responses.create(
            model=model,
            input="Reply with exactly: OpenAI connection works",
        )
    except openai.APIError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc}") from exc

    return {"model": model, "reply": response.output_text}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=9001, reload=True)
