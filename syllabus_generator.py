"""
Generates a personalized curriculum for an arbitrary domain: Tavily does the
real web research, Groq synthesizes it into a structured concept graph --
concepts, prerequisites, modules, synopsis/breakdown/hobby-analogy per concept,
and starter cards -- that plugs directly into the existing schema.

Two-step pipeline (search, then synthesize) rather than one integrated tool
call, because Groq doesn't have native search grounding the way Gemini/Claude
do -- this is the real tradeoff of using Groq, documented here rather than
hidden.
"""
from db import add_domain, add_concept, add_card, set_hobby, set_course_level
import llm_common
import web_search

SYSTEM_PROMPT_TEMPLATE = """You are a university curriculum designer building a rigorous, academically \
sound course. You'll be given a learning goal and real web search results, including results \
specifically about how this subject is taught in actual courses. Use them to build a syllabus \
with the depth, correct terminology, and structured progression of a real university course \
-- not a beginner blog post.

Output ONLY valid JSON, no prose before or after, matching exactly this shape:

{{
  "domain_id": "short_snake_case_id",
  "domain_name": "Human-readable name",
  "course_level": "Beginner" | "Intermediate" | "Advanced",
  "concepts": [
    {{"id": "concept_id", "name": "Concept name", "module_name": "Module title",
     "prerequisites": ["other_concept_id"],
     "synopsis": "one precise sentence stating what this concept is, using correct terminology",
     "technical_breakdown": "5-7 sentences of real depth -- the actual mechanism, reasoning, or \
derivation, at the level a real course would teach it. Do not oversimplify.",
     "worked_example": "a concrete, specific example with real numbers/data/a real scenario \
showing the concept applied step by step -- not another abstract restatement"
     {hobby_field}}}
  ],
  "starter_cards": [
    {{"concept_id": "concept_id", "content": "question or prompt text",
     "grading_type": "exact_match", "answer_key": "expected answer"}},
    {{"concept_id": "concept_id", "content": "question or prompt text",
     "grading_type": "multiple_choice", "answer_key": "the exact correct option text",
     "options": ["option A text", "option B text", "option C text", "option D text"]}}
  ]
}}

Rules:
- 8-14 concepts, grouped into 3-5 modules by module_name (evocative, memorable titles, not
  "Module 1"). Sequence concepts the way an actual course would -- foundational concepts with
  no prerequisites first, each later concept genuinely building on earlier ones. The prerequisite
  graph should reflect real pedagogical dependency, not be decorative.
- course_level: judge honestly from the goal and search results whether this is an intro
  ("Beginner"), a course assuming some background ("Intermediate"), or genuinely advanced
  material ("Advanced").
- synopsis, technical_breakdown, and worked_example must be substantive, correct, and specific --
  use the actual terminology a textbook or course would use. Vague filler is not acceptable. If
  the search results give you a specific mechanism, number, or example, use it.
- 1-2 starter_cards per concept, testing real understanding, not just recall of the synopsis.
- concept ids and domain_id must be lowercase snake_case, no spaces.
{hobby_instruction}
- Ground the structure in the actual search results provided, especially the academic/course
  results, rather than generic assumptions about the topic."""


def _build_system_prompt(hobby: str | None) -> str:
    if hobby:
        hobby_instruction = (
            f'- The learner\'s hobby/interest is "{hobby}". Where it genuinely helps understanding, '
            f'phrase question content or add a short analogy drawing on {hobby} -- but never force it '
            f'somewhere it would make the question less clear or accurate. Correctness comes first. '
            f'For each concept, also fill "hobby_analogy" with one specific, concrete analogy connecting '
            f'the concept to {hobby} -- must be accurate to both the concept and the hobby, not generic.'
        )
        hobby_field = ',\n     "hobby_analogy": "a specific analogy connecting this concept to the learner\'s hobby"'
    else:
        hobby_instruction = "- No personalization hobby given -- write clear, direct content."
        hobby_field = ""
    return SYSTEM_PROMPT_TEMPLATE.format(hobby_instruction=hobby_instruction, hobby_field=hobby_field)


def generate_syllabus(goal_description: str, hobby: str | None = None) -> dict:
    # two searches: one for general grounding, one specifically aimed at how this
    # is actually taught academically -- a single "basics of X" query tends to
    # surface beginner blog posts, not real course structure or common pitfalls
    overview = web_search.search(f"{goal_description} fundamentals what to learn")
    academic = web_search.search(f"{goal_description} university course syllabus curriculum topics order")
    search_context = f"{overview}\n\n{academic}"

    system = _build_system_prompt(hobby)
    prompt = f"Learning goal: {goal_description}\n\nWeb search results:\n{search_context}"
    # measured: input ~1800 tokens, worst-case output for 14 deep concepts ~5100 tokens.
    # 7000 gives real headroom (measured worst-case retry ~10,830 vs the 12,000 TPM budget
    # on llama-3.3-70b-versatile) -- not a round number picked by feel.
    data = llm_common.generate_json(prompt, system=system, max_tokens=7000)

    _validate_syllabus(data)
    return data


EXPLAIN_PROMPT_TEMPLATE = """Explain the concept "{concept_name}" (part of learning "{domain_name}") \
clearly, accurately, and with real depth -- like a university course would, not a summary.

Output ONLY valid JSON, no prose before or after, no markdown code fences, matching exactly:
{{
  "synopsis": "one precise sentence stating what this concept is",
  "technical_breakdown": "5-7 sentences of real depth -- the actual mechanism, reasoning, or derivation",
  "worked_example": "a concrete, specific example with real numbers/data/a real scenario, step by step"{hobby_field}
}}

Substantive and correct, not vague filler. Use real terminology.{hobby_instruction}"""


def generate_concept_explanation(concept_name: str, domain_name: str, hobby: str | None = None) -> dict:
    """
    Generates synopsis/technical_breakdown/worked_example/hobby_analogy for a single
    existing concept -- used when a concept was added manually and has no explanation
    yet. No search needed here, just direct generation.
    """
    if hobby:
        hobby_field = ',\n  "hobby_analogy": "a specific analogy connecting this concept to the hobby"'
        hobby_instruction = (
            f' The learner\'s hobby is {hobby} -- include one specific, accurate analogy to it '
            f'in hobby_analogy, but only if it genuinely clarifies the concept.'
        )
    else:
        hobby_field = ""
        hobby_instruction = ""

    prompt = EXPLAIN_PROMPT_TEMPLATE.format(
        concept_name=concept_name, domain_name=domain_name,
        hobby_field=hobby_field, hobby_instruction=hobby_instruction,
    )
    return llm_common.generate_json(prompt, system="You are a precise, thorough university-level tutor.", max_tokens=1200)


def _validate_syllabus(data: dict):
    required = {"domain_id", "domain_name", "concepts", "starter_cards"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"Generated syllabus missing fields: {missing}")
    concept_ids = {c["id"] for c in data["concepts"]}
    for c in data["concepts"]:
        for prereq in c.get("prerequisites", []):
            if prereq not in concept_ids:
                raise RuntimeError(f"Concept '{c['id']}' references unknown prerequisite '{prereq}'")
    for card in data["starter_cards"]:
        if card["concept_id"] not in concept_ids:
            raise RuntimeError(f"Starter card references unknown concept '{card['concept_id']}'")
        if card.get("grading_type") == "multiple_choice":
            options = card.get("options") or []
            if len(options) < 2:
                raise RuntimeError(f"Multiple-choice card for '{card['concept_id']}' needs at least 2 options")
            if card.get("answer_key") not in options:
                raise RuntimeError(
                    f"Multiple-choice card for '{card['concept_id']}': answer_key doesn't exactly "
                    f"match any option (model likely paraphrased instead of copying verbatim)"
                )


QUIZ_PROMPT_TEMPLATE = """Generate a {count}-question multiple-choice quiz testing real understanding \
of this module, based on the concepts below. Questions should test comprehension and application, \
not just recall of definitions. Distractors should be plausible mistakes, not obviously wrong filler.

Module: {module_name}

Concepts covered:
{concepts_text}

Output ONLY valid JSON, no prose before or after, matching exactly:
{{
  "questions": [
    {{"question": "question text", "options": ["A", "B", "C", "D"], "correct_answer": "exact text of the correct option"}}
  ]
}}"""


def generate_module_quiz(module_name: str, concepts: list[dict], count: int = 7) -> list[dict]:
    """
    Generates a fresh N-question quiz for a module. Deliberately not persisted or
    wired into FSRS/BKT -- this is a distinct "check my progress on this whole
    module" self-test, not a spaced-repetition review item, so it doesn't affect
    mastery tracking or scheduling.
    """
    concepts_text = "\n".join(
        f"- {c['name']}: {c.get('synopsis', '')} {c.get('technical_breakdown', '')}"[:400]
        for c in concepts
    )
    prompt = QUIZ_PROMPT_TEMPLATE.format(count=count, module_name=module_name, concepts_text=concepts_text)
    data = llm_common.generate_json(prompt, system="You are a rigorous exam writer.", max_tokens=2500)

    questions = data.get("questions", [])
    if not questions:
        raise RuntimeError("Model returned no questions.")
    for q in questions:
        if q.get("correct_answer") not in (q.get("options") or []):
            raise RuntimeError(f"Quiz question's correct_answer doesn't match any option: {q.get('question', '')[:60]}")
    return questions


def apply_syllabus(data: dict, user_id: str, hobby: str | None = None) -> str:
    """Writes a generated syllabus into the database, owned by user_id. Returns
    the actual domain_id used -- may differ from data["domain_id"] if that slug
    was already taken (add_domain resolves collisions itself)."""
    domain_id = add_domain(data["domain_id"], data["domain_name"], user_id)
    if hobby:
        set_hobby(domain_id, hobby)
    if data.get("course_level"):
        set_course_level(domain_id, data["course_level"])

    added = set()
    remaining = list(data["concepts"])
    while remaining:
        progressed = False
        for c in list(remaining):
            prereqs = c.get("prerequisites", [])
            if all(p in added for p in prereqs):
                add_concept(c["id"], domain_id, c["name"], prerequisites=prereqs,
                            module_name=c.get("module_name"), synopsis=c.get("synopsis"),
                            technical_breakdown=c.get("technical_breakdown"),
                            hobby_analogy=c.get("hobby_analogy"), worked_example=c.get("worked_example"))
                added.add(c["id"])
                remaining.remove(c)
                progressed = True
        if not progressed:
            for c in remaining:
                add_concept(c["id"], domain_id, c["name"], prerequisites=[],
                            module_name=c.get("module_name"), synopsis=c.get("synopsis"),
                            technical_breakdown=c.get("technical_breakdown"),
                            hobby_analogy=c.get("hobby_analogy"), worked_example=c.get("worked_example"))
                added.add(c["id"])
            break

    for card in data["starter_cards"]:
        add_card(domain_id, card["content"], card["answer_key"],
                  card["grading_type"], [card["concept_id"]], options=card.get("options"))

    return domain_id
