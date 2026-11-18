import time
from collections.abc import Callable


class ModelStreamError(ValueError):
    pass


def stream_response(client, progress: Callable[[str, str | None], None] | None = None, **kwargs):
    stages = {
        "response.created": "Request accepted by OpenAI",
        "response.web_search_call.in_progress": "Starting web search",
        "response.web_search_call.searching": "Searching public sources",
        "response.web_search_call.completed": "Writing sourced findings",
        "response.code_interpreter_call.in_progress": "Preparing document code",
        "response.code_interpreter_call_code.delta": "Writing document code",
        "response.code_interpreter_call.interpreting": "Running document code",
        "response.code_interpreter_call.completed": "Document code completed",
    }
    last_stage, last_sent, pending = "", 0.0, ""
    with client.responses.stream(**kwargs) as stream:
        for event in stream:
            if event.type in {"error", "response.failed", "response.incomplete"}:
                raise ModelStreamError("OpenAI did not complete the streamed response.")
            if event.type == "response.output_text.delta":
                pending += event.delta
                stage = "Writing response"
            else:
                stage = stages.get(event.type)
            if stage and progress and (stage != last_stage or time.monotonic() - last_sent >= 0.4):
                progress(stage, pending or None)
                pending = ""
                last_stage, last_sent = stage, time.monotonic()
        try:
            response = stream.get_final_response()
        except RuntimeError as exc:
            raise ModelStreamError("OpenAI stream ended without a completed response.") from exc
    if response.status != "completed":
        raise ModelStreamError("OpenAI did not complete the streamed response.")
    if progress:
        progress("Response complete", pending or None)
    return response
