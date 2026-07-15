"""
Shared Groq client helper. All four AI features (syllabus generation, chat
mentor, on-demand concept explanation, Doc Explainer) go through this instead
of each hand-rolling their own client setup.

Groq specifically because its free tier is a straightforward rate-limited
allowance (not the flaky grounding-quota bug Gemini's free tier hit), and
it's fast. Model names are env-overridable since Groq has deprecated model
names before (see README) -- if this breaks, check console.groq.com/docs/models
for current model IDs rather than assuming these are still valid.
"""
import os
import json
import re

from groq import Groq

TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Get a free key (no card required) at "
            "console.groq.com to use this feature."
        )
    return Groq(api_key=api_key)


def generate_text(prompt: str, system: str | None = None, history: list[dict] | None = None,
                   max_tokens: int = 2000) -> str:
    """Plain text generation. history, if given, is [{role, content}, ...]."""
    client = _client()  # raises RuntimeError with the real "not set" message if no key
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL, messages=messages, max_completion_tokens=max_tokens,
        )
    except Exception as e:
        raise RuntimeError(f"Groq request failed: {e}")

    return (response.choices[0].message.content or "").strip() or "(no response)"


def generate_json(prompt: str, system: str, max_tokens: int = 2500) -> dict:
    """
    Generation where the model must return only JSON. Uses Groq's native JSON
    mode (response_format=json_object) rather than relying on prompt instructions
    alone -- this is a real reliability difference, not a style choice: without
    it, a long response (like a full syllabus) can get wrapped in prose or
    truncated mid-structure, causing exactly the kind of "generation failed"
    error this app has hit before. One retry on parse failure, since a single
    truncated response is usually a transient issue, not a systemic one.
    """
    client = _client()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    last_error = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=TEXT_MODEL, messages=messages, max_completion_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            raise RuntimeError(f"Groq request failed: {e}")

        raw = (response.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)  # defensive, shouldn't be needed with json_object mode
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = e
            if attempt == 0:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": (
                    "That wasn't valid JSON (or was cut off). Reply again with the complete, "
                    "valid JSON object only -- nothing else, and make sure every field is finished."
                )})
                continue

    raise RuntimeError(f"Model output wasn't valid JSON after a retry: {last_error}")


def generate_from_image(image_bytes: bytes, mime_type: str, instruction: str) -> str:
    import base64
    client = _client()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
        ],
    }]
    try:
        response = client.chat.completions.create(model=VISION_MODEL, messages=messages)
    except Exception as e:
        raise RuntimeError(f"Groq vision request failed: {e}")
    return (response.choices[0].message.content or "").strip() or "(no response)"
