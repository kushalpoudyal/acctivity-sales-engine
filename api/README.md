# FastAPI server

Run these PowerShell commands from this folder.

## Initial setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## OpenAI configuration

Put your OpenAI API key in `.env`:

```dotenv
OPENAI_API_KEY=your-api-key
```

The server loads `.env` at startup. `.env` is ignored by Git; `.env.example`
shows the required variable without a secret. Set `OPENAI_MODEL` to override the
default model, `gpt-5.4-mini`.

## Start the development server

```powershell
.\.venv\Scripts\python.exe main.py
```

The explicit Python path uses this server's virtual environment without requiring
activation. The server runs on port 9001 with auto-reload. Stop it with Ctrl+C.

- API: http://127.0.0.1:9001/
- Health check: http://127.0.0.1:9001/health
- OpenAI connection test: http://127.0.0.1:9001/openai/test
- Interactive documentation: http://127.0.0.1:9001/docs
- OpenAPI schema: http://127.0.0.1:9001/openapi.json
