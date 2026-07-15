"""
Live chat mentor for the Practice Shell. Stateless on the server side -- the
frontend keeps conversation history and sends it with each message.

This is framed as a full AI tutor, not just a Q&A box: it can go deep on
explanations, walk through worked examples, and quiz the learner conversationally
when asked (a plain-text question/answer exchange, distinct from the structured
module quiz feature -- both exist because sometimes you want a quick "quiz me on
this" in the middle of a conversation, and sometimes you want a real N-question
assessment with a score at the end).
"""
import llm_common


def reply(concept_name: str, hobby: str | None, history: list[dict], message: str) -> str:
    hobby_line = (
        f" The learner's hobby is {hobby} -- lean on analogies from it where genuinely helpful, "
        f"but prioritize being clear and correct over forcing the theme."
        if hobby else ""
    )
    system = (
        f"You are a thorough, patient AI tutor helping someone learn '{concept_name}'.{hobby_line} "
        f"You're capable of real depth: explain concepts from a different angle if the first explanation "
        f"didn't land, walk through concrete worked examples step by step, and answer follow-up questions "
        f"in detail rather than staying surface-level. If asked to quiz them, ask one question at a time, "
        f"wait for their answer, then tell them if they're right before moving to the next question -- "
        f"don't dump a list of questions at once. Keep replies focused and conversational, not a wall of "
        f"text, unless real depth is specifically what's being asked for. If they ask something unrelated "
        f"to the concept, gently steer back."
    )
    # convert our {role, text} shape to Groq's OpenAI-style {role, content}.
    # Normalize defensively rather than passing the frontend's role through
    # verbatim -- this exact bug already happened once (a leftover 'model'
    # role from before the Gemini->Groq switch caused every second chat
    # message to 400), so anything that isn't literally 'user' is treated
    # as an assistant turn instead of trusting the caller.
    groq_history = [
        {"role": "user" if t.get("role") == "user" else "assistant", "content": t.get("text", "")}
        for t in history
    ]
    return llm_common.generate_text(message, system=system, history=groq_history, max_tokens=1200)
