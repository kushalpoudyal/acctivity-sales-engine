import os
from contextlib import asynccontextmanager
from pathlib import Path

import openai
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from find_firms import router as find_firms_router
from prospects import initialize, router
from outputs import router as outputs_router
from live_updates import router as live_router
from demo_logins import router as demo_router

load_dotenv(Path(__file__).with_name(".env"))

DEFAULT_OPENAI_MODEL = "gpt-6.1-sol"

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield


app = FastAPI(title="Hack A API", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(find_firms_router)
app.include_router(outputs_router)
app.include_router(live_router)
app.include_router(demo_router)


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
