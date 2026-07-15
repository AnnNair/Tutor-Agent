# Micro-learning tutor agent

A spaced-repetition tutor: FSRS scheduling + Bayesian Knowledge Tracing mastery,
module-grouped roadmaps, hobby-personalized AI content, a live chat mentor, and
a Doc Explainer that reads your own notes/PDFs/photos through the same
personalization lens.

**AI provider: Groq (text) + Tavily (search)**, not Gemini -- switched after Gemini's
free tier turned out to have a real, currently-unresolved quota-enforcement bug
(documented on Google's own developer forum). Groq's free tier is a plain rate
limit, not grounding-quota weirdness, and Tavily's free tier (1,000 searches/month)
is generous for this app's usage pattern.

## Architecture

- `schema.sql` -- domains, concepts (module/synopsis/breakdown/hobby_analogy), cards
  (with MCQ options), documents, prerequisites, mastery, review logs, hobby settings
- `bkt.py` -- Bayesian Knowledge Tracing math
- `db.py` -- data access layer; auto-migrates older databases on startup
- `curriculum.py` -- due-card selection, mastery trend detection, remediate-vs-continue
- `llm_common.py` -- shared Groq client helper (text, JSON, and vision generation)
- `web_search.py` -- Tavily search helper, used only by syllabus generation
- `syllabus_generator.py` -- full syllabus generation (Tavily search + Groq synthesis)
  and on-demand single-concept explanation generation
- `mentor_chat.py` -- stateless live chat, personalized to the domain's hobby
- `doc_explainer.py` -- PDF/TXT/MD/image upload, text extraction, hobby-personalized explanation
- `api.py` -- FastAPI backend
- `cli.py` -- command-line interface
- `static/index.html` -- the web app

## Setup

```bash
pip install -r requirements.txt
```

## Getting API keys (both free, no card required)

1. **Groq** (powers chat, explanations, syllabus writing, Doc Explainer): go to
   console.groq.com, sign up, create an API key.
2. **Tavily** (powers the live web research for syllabus generation only): go to
   tavily.com, sign up, grab your API key from the dashboard. 1,000 free searches/month.

Set both before starting the server:

```bash
# macOS/Linux
export GROQ_API_KEY=your-groq-key
export TAVILY_API_KEY=your-tavily-key

# Windows PowerShell
$env:GROQ_API_KEY="your-groq-key"
$env:TAVILY_API_KEY="your-tavily-key"
```

To make them permanent (PowerShell), so you don't retype them every session:
```powershell
[System.Environment]::SetEnvironmentVariable("GROQ_API_KEY", "your-groq-key", "User")
[System.Environment]::SetEnvironmentVariable("TAVILY_API_KEY", "your-tavily-key", "User")
```
Close and reopen PowerShell before it takes effect.

Without these set, the relevant features show a clear in-UI error instead of
crashing -- manual add-concept/add-card, FSRS scheduling, and mastery tracking
all work fine without either key. Chat/explanations/Doc Explainer only need
`GROQ_API_KEY`; only full syllabus generation needs `TAVILY_API_KEY` too (the
search step).

## Running it

```bash
python -m uvicorn api:app --reload
```
Open `http://localhost:8000` in a browser.

## Syllabus generation reliability

- **Real, measured token budget** -- the previous version hit a genuine 413 error
  ("Request too large... Requested 11439... Limit 8000") because it requested 8,000
  completion tokens on `openai/gpt-oss-120b`, which has an 8,000 TPM free-tier cap --
  the request alone could exceed the entire per-minute budget. Fixed by switching to
  `llama-3.3-70b-versatile` (12,000 TPM free tier, comparable quality) and reducing
  the actual request size to what a syllabus realistically needs (~3,000 completion
  tokens, trimmed search context) rather than an arbitrary large number. Measured the
  real token count of a representative request before shipping this fix -- worst case
  (including the automatic retry) sits at roughly 6,800 tokens against a 12,000 budget,
  not just "should be fine."
- **Groq's native JSON mode** (`response_format=json_object`) instead of hoping the
  model follows "output only JSON" instructions.
- **One automatic retry** if the JSON still comes back truncated/invalid, tested with
  a mocked truncated-then-valid response sequence to confirm it actually recovers.
- **Two search queries, "advanced" depth** -- one for fundamentals, one specifically
  for how the subject is taught as a real university course, so the model has actual
  academic structure to ground on, not just beginner blog content. Snippet length is
  deliberately kept short (350-400 chars) since this text feeds directly into the
  token-constrained Groq prompt.
- **`course_level`** (Beginner/Intermediate/Advanced) generated honestly from the
  topic and search results, shown as a pill in the sidebar.

## Multi-user accounts (new)

Every domain now belongs to a specific user. Sign up or log in from the screen
that appears on first load -- everything after that (subjects, concepts, cards,
review history, uploaded documents) is scoped to your account and invisible to
anyone else, enforced on every single API endpoint, not just hidden in the UI.

- Passwords are hashed with PBKDF2-HMAC-SHA256 (260,000 iterations), never stored
  in plain text.
- Sessions are server-tracked tokens in an HTTP-only cookie (30-day expiry) --
  logging out immediately revokes that specific session, unlike a stateless JWT
  that would still be valid until it expired.
- Two different users naming a subject the same thing (e.g. both create "German")
  get distinct IDs automatically (`german`, `german_2`) -- tested explicitly,
  since a naive implementation would silently fail the second user's creation
  on the ID collision.
- The CLI (`cli.py`) still works exactly as before, no login required -- it
  operates under a fixed local pseudo-account separate from the web app's real
  user accounts, since it's a personal terminal tool, not part of the hosted
  multi-user product.

### The one thing I deliberately didn't do here: fix SQLite for real hosted persistence

This app still uses SQLite. Fine for local/personal use, but genuinely not fine
for hosting real user accounts on Render's free tier (or most free hosts) --
**free web services there have ephemeral disk**, meaning `tutor.db`, and every
account/password/subject in it, gets wiped on every redeploy or restart.
Shipping real signups on top of that would be actively misleading -- people
would create accounts that quietly vanish.

Fixing this properly means migrating to Render's free PostgreSQL (or another
free managed Postgres) instead of SQLite -- a real rewrite of `db.py`'s query
layer, not a config change. I scoped this pass to the auth/isolation layer
itself, since that had to be correct before persistence is worth solving.
**Don't deploy this for real strangers to sign up to yet** -- the Postgres
migration is the next step.

## What's new in this round

1. **Deeper content**: `technical_breakdown` is now 5-7 sentences (was 3-5), and every
   concept gets a `worked_example` -- a concrete, specific example, not another
   abstract restatement. This raised realistic output size, so `max_completion_tokens`
   for syllabus generation went from 3,000 to 7,000 -- measured to stay safely under
   the 12,000 TPM budget even in the worst-case retry (~10,830 tokens), not just
   assumed. Concept count range was trimmed slightly (8-14, was 10-16) to help keep
   output bounded.
2. **Home vs. Subject split**: the AI syllabus generator and "create manually" panel
   only appear on a dedicated home screen now (reachable via "+ new subject..." in
   the sidebar dropdown) -- not repeated under every single concept you view. Once
   you're inside a subject, you see that subject's content; adding more concepts/cards
   to it happens via a small "+ manage content" toggle, not a permanent AI-generation
   panel.
3. **Module quizzes**: click "📝 quiz this module" on any concept to get a fresh
   5-10 question multiple-choice quiz covering every concept in that module, with a
   question-by-question flow and a score at the end. Deliberately not wired into
   FSRS/BKT -- this is a "check my progress on the whole module" self-test, separate
   from the spaced-repetition review system, so taking one doesn't affect scheduling
   or mastery tracking.
4. **Practice Shell reframed as a full AI tutor**: the chat is now the dominant,
   larger element (not a small panel below the due card), and the system prompt
   explicitly supports going deeper on explanations, working through examples, and
   conversational quizzing ("quiz me on this") one question at a time. Distinct from
   the structured module quiz -- freeform in chat vs. a real N-question assessment
   with a tracked score.
5. **Doc Explainer unchanged**, per explicit request.

## Full walkthrough

**1. First launch.** You'll land on Curriculum Space with no domains yet. Either:
   - Click **+ new domain** and name it (e.g. "German") -- creates an empty domain
     you fill in yourself (manually or via AI), or
   - Fill in **"What do you want to learn?"** (e.g. "Basics of German") and optionally
     a hobby (e.g. "baking"), then click **generate syllabus**. Tavily researches the
     topic, Groq turns that into the whole roadmap -- modules, concepts, prerequisites,
     cards, and per-concept explanations -- in one shot. Takes 20-40 seconds.

**2. Curriculum Space.** Left rail shows your roadmap grouped into modules. Click a
   concept to see its synopsis, technical breakdown, hobby analogy (if set), and
   practice cards. Missing an explanation (e.g. manually-added concept)? Click
   **generate explanation** -- cheaper/faster than a full regeneration since it
   skips the search step.

**3. Practice Shell.** Shows whatever's actually due, per the FSRS scheduler. Answer
   it, get told if you're right, and a live chat mentor is open below for whatever
   concept is in focus.

**4. Doc Explainer.** Upload a PDF, text file, markdown file, or photo of notes.
   Click it, then **explain this document** for a study-guide-style explanation,
   personalized to your hobby the same way generated concepts are.

**5. Review Schedule.** Per-concept FSRS stats and a jump-to-practice button.

**6. Multiple domains.** Switch domains via the pills under the top nav -- each has
   its own roadmap, hobby, and progress.

## CLI (unchanged, still works)

```bash
python cli.py init
python cli.py add-domain dsa "Data Structures & Algorithms"
python cli.py add-concept two_pointers dsa "Two pointers"
python cli.py add-card dsa "Technique: shrink array from both ends" "two pointers" exact_match --concepts two_pointers
python cli.py review dsa
python cli.py status dsa
```

## Known limitations (honest, not accidental)

- **Groq model names have churned before** (Groq deprecated several Llama chat/vision
  model names in 2026). Defaults are set in `llm_common.py` (`GROQ_TEXT_MODEL`,
  `GROQ_VISION_MODEL` env vars to override) -- if a request starts failing with a
  "model not found" style error, check console.groq.com/docs/models for current IDs.
- **Syllabus generation is a two-step pipeline** (Tavily search, then Groq synthesis),
  not one integrated tool call -- Groq doesn't have native search grounding the way
  Gemini/Claude do. This is the real tradeoff of using Groq; documented here rather
  than hidden. Chat, on-demand explanations, and Doc Explainer don't need search, so
  they're single-step and only need `GROQ_API_KEY`.
- **Grading**: `exact_match` and `multiple_choice` are automated. `code_test` and
  `rubric_llm` exist in the schema but have no grader built yet.
- **Chat mentor is stateless server-side**: history lives in the browser tab, resets on refresh.
- **Mind graph** shows the selected concept's direct prerequisites/dependents (up to 5
  nodes), not a full graph layout.
- **Doc Explainer**: text files capped at ~40,000 characters per explanation call
  (longer docs get a truncated excerpt with a clear note). Images go straight to
  Groq's vision model, capped only by the 15MB upload limit.
- **Uploaded files live on disk** under `uploads/{domain}/`, not in the database.
