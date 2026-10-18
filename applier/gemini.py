import json
import re
import threading
from functools import lru_cache

from google import genai
from google.genai import types

from . import config


_lock = threading.Lock()
_client: genai.Client | None = None


def client() -> genai.Client:
    # Locked: research runs in threads, and a duplicate Client that gets garbage-collected
    # closes its HTTP connection while still in use ("client has been closed").
    global _client
    with _lock:
        if _client is None:
            if not config.GEMINI_API_KEY:
                raise SystemExit("GEMINI_API_KEY is not set in .env")
            _client = genai.Client(api_key=config.GEMINI_API_KEY)
        return _client


def list_models() -> list[str]:
    return [
        m.name.removeprefix("models/")
        for m in client().models.list()
        if "generateContent" in (m.supported_actions or [])
    ]


@lru_cache(maxsize=1)
def model() -> str:
    """GEMINI_MODEL if set, else the newest non-lite 'flash' model the key can use."""
    if config.GEMINI_MODEL:
        return config.GEMINI_MODEL
    flash = [m for m in list_models()
             if re.fullmatch(r"gemini-[\d.]+-flash(-preview)?", m)]
    if not flash:
        raise SystemExit("Could not auto-pick a Gemini model; set GEMINI_MODEL in .env "
                         "(see `python run.py models`).")

    def rank(name):
        version = tuple(int(x) for x in re.search(r"gemini-([\d.]+)", name)[1].split("."))
        return version, "preview" not in name  # prefer stable at the same version
    return max(flash, key=rank)


def grounded_json(task: str, json_spec: str) -> tuple[object, list[dict]]:
    """Research `task` with Google Search, then convert the findings to JSON matching `json_spec`.

    Two calls on purpose: when asked for JSON-only output, Gemini answers from memory and
    skips searching. Asking for cited notes makes it search; a second call does the formatting.
    Returns (parsed_json, sources), where sources are the pages Gemini actually read.
    """
    for attempt in range(2):
        resp = client().models.generate_content(
            model=model(),
            contents=task + "\n\nRun several Google searches before answering. Write your findings "
                            "as detailed notes and cite the source URL for every fact.",
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        cand = (resp.candidates or [None])[0]
        meta = getattr(cand, "grounding_metadata", None)
        sources = [{"title": ch.web.title or ch.web.domain or "", "url": ch.web.uri}
                   for ch in (getattr(meta, "grounding_chunks", None) or []) if ch.web and ch.web.uri]
        if sources and resp.text:
            break
    else:
        raise RuntimeError("Gemini did not search the web; refusing to save unsourced research")

    resp = client().models.generate_content(
        model=model(),
        contents=f"Convert these research notes to JSON. Use only facts stated in the notes; "
                 f"use null or [] when the notes don't say.\n\nSchema:\n{json_spec}\n\nNotes:\n{resp.text}",
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    return extract_json(resp.text or ""), sources


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text
    start = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError(f"No JSON in Gemini response: {text[:300]}")
    return json.JSONDecoder().raw_decode(raw[start:])[0]
