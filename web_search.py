"""
Tavily web search -- the "research the topic" step for syllabus generation.
Free tier: 1,000 searches/month, no card required. Get a key at tavily.com.
"""
import os

from tavily import TavilyClient


def search(query: str, max_results: int = 4) -> str:
    """Returns a single text blob of search results, ready to drop into a prompt."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY is not set. Get a free key (no card required, 1,000 "
            "searches/month) at tavily.com to use syllabus generation."
        )

    client = TavilyClient(api_key=api_key)
    try:
        results = client.search(query, search_depth="advanced", max_results=max_results, include_answer=True)
    except Exception as e:
        raise RuntimeError(f"Tavily search failed: {e}")

    # snippets kept short deliberately -- this text goes straight into the Groq prompt,
    # and Groq's free tier has a tight per-minute token budget (see llm_common.py).
    # Rich per-page content isn't worth risking a 413 over; the model needs enough to
    # ground the structure, not the full article.
    parts = []
    if results.get("answer"):
        parts.append(f"Summary: {results['answer'][:400]}")
    for r in results.get("results", []):
        parts.append(f"- {r.get('title', '')}: {r.get('content', '')[:350]}")
    return "\n".join(parts) if parts else "(no search results found)"
