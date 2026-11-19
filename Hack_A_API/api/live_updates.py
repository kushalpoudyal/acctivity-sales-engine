import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from prospects import get_firm

router = APIRouter(prefix="/api/v1/firms", tags=["Live updates"])


async def firm_events(request: Request, firm_id: int):
    previous, heartbeat = None, 0.0
    while not await request.is_disconnected():
        try:
            firm = await asyncio.to_thread(get_firm, firm_id)
        except HTTPException:
            return  # The firm was deleted.
        payload = json.dumps(firm, separators=(",", ":"))
        if payload != previous:
            yield f"data: {payload}\n\n"
            previous = payload
            heartbeat = time.monotonic()
        elif time.monotonic() - heartbeat >= 10:
            yield 'event: heartbeat\ndata: {}\n\n'
            heartbeat = time.monotonic()
        active = firm["status"] == "researching" or any(
            job["status"] in {"queued", "running"} for job in firm["outputs"])
        await asyncio.sleep(0.5 if active else 2)


@router.get("/{firm_id}/events")
def events(firm_id: int, request: Request):
    get_firm(firm_id)
    return StreamingResponse(firm_events(request, firm_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
