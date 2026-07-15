"""
Doc Explainer: upload reference material (PDF/TXT/MD/image), get it explained
through the domain's hobby-analogy lens.

Text-based files (pdf/txt/md) are extracted to plain text and sent to Groq's
text model. Images go to Groq's vision model directly -- no OCR step needed.
"""
import os

from pypdf import PdfReader

import llm_common

IMAGE_MIME_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def detect_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".txt",):
        return "txt"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in IMAGE_MIME_TYPES:
        return "image"
    raise ValueError(f"Unsupported file type: {ext or '(no extension)'}")


def extract_text(file_path: str, file_type: str) -> str:
    if file_type == "pdf":
        reader = PdfReader(file_path)
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if file_type in ("txt", "md"):
        with open(file_path, "r", errors="replace") as f:
            return f.read()
    raise ValueError(f"extract_text doesn't handle file_type={file_type}")


def explain_document(file_path: str, file_type: str, hobby: str | None) -> str:
    hobby_line = (
        f" The learner's hobby is {hobby} -- weave in analogies from it where genuinely "
        f"helpful, but prioritize being clear and correct over forcing the theme."
        if hobby else ""
    )
    instruction = (
        f"Explain the attached material clearly, as a short study guide: what it covers, "
        f"the key ideas, and anything a learner is likely to find confusing.{hobby_line} "
        f"Keep it focused -- a few paragraphs, not an exhaustive rewrite of the source."
    )

    if file_type == "image":
        with open(file_path, "rb") as f:
            image_bytes = f.read()
        ext = os.path.splitext(file_path)[1].lower()
        mime_type = IMAGE_MIME_TYPES.get(ext, "image/png")
        return llm_common.generate_from_image(image_bytes, mime_type, instruction)

    text = extract_text(file_path, file_type)
    if not text.strip():
        raise RuntimeError(
            "Couldn't extract any text from this file -- it may be a scanned/image-only "
            "PDF. Try uploading it as an image instead if it's a single page."
        )
<<<<<<< HEAD
    # 40,000 chars was leftover from when this used Gemini (much larger free-tier
    # budget) and was never recalibrated for Groq's 12,000 TPM cap -- 40,000 chars
    # (~10,000 tokens) plus the default 2,000 output tokens totalled ~12,000+,
    # which is exactly what caused a real 413 ("Requested 12914... Limit 12000").
    # 24,000 chars (~6,850 tokens at a conservative 3.5 chars/token, since PDF
    # extraction can be less token-dense than plain prose) plus a 900-token
    # output cap totals under 8,000 -- real margin, not just squeaking under.
    MAX_CHARS = 24000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated, document is longer than this excerpt ...]"

    return llm_common.generate_text(f"{instruction}\n\n--- document content ---\n{text}", max_tokens=900)
=======
    MAX_CHARS = 40000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated, document is longer than this excerpt ...]"

    return llm_common.generate_text(f"{instruction}\n\n--- document content ---\n{text}")
>>>>>>> 4155d975e7f5c4eddb6af7d9eaf82afb98f1b115

