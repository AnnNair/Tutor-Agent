-- Accounts. Password is PBKDF2-HMAC hashed with a per-user salt, never plain text.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Session tokens. A row per active login (not a stateless JWT) specifically so
-- logout can revoke a single session immediately rather than waiting for expiry.
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Domains keep content separated (dsa, rust, spanish, macro_history, ...)
-- user_id scopes ownership -- every domain-related query must filter by it.
CREATE TABLE IF NOT EXISTS domains (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    user_id TEXT REFERENCES users(id)
);

-- Concepts are the actual skills being learned, not the questions themselves.
-- module_name groups related concepts for the roadmap view (e.g. "The Quantum
-- Pantry"). synopsis/technical_breakdown/hobby_analogy are optional explanatory
-- content -- populated by AI generation (full syllabus or on-demand single-concept),
-- left null for manually-added concepts until/unless generated.
CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL REFERENCES domains(id),
    name TEXT NOT NULL,
    module_name TEXT,
    synopsis TEXT,
    technical_breakdown TEXT,
    hobby_analogy TEXT,
    worked_example TEXT
);

-- A concept can depend on another (e.g. "sliding window" requires "two pointers").
-- Used so the curriculum never quizzes a concept whose prerequisite is still weak.
CREATE TABLE IF NOT EXISTS concept_prerequisites (
    concept_id TEXT NOT NULL REFERENCES concepts(id),
    prerequisite_id TEXT NOT NULL REFERENCES concepts(id),
    PRIMARY KEY (concept_id, prerequisite_id)
);

-- Live BKT state per concept. Updated after every review, not recomputed from scratch.
CREATE TABLE IF NOT EXISTS concept_mastery (
    concept_id TEXT PRIMARY KEY REFERENCES concepts(id),
    p_mastery REAL NOT NULL DEFAULT 0.1,   -- P(concept is known right now)
    p_learn REAL NOT NULL DEFAULT 0.15,    -- P(transition unknown -> known per practice)
    p_slip REAL NOT NULL DEFAULT 0.1,      -- P(known but answered wrong)
    p_guess REAL NOT NULL DEFAULT 0.2,     -- P(unknown but answered right)
    last_updated TEXT
);

-- Practice items. grading_type determines how a raw answer becomes correct/incorrect.
-- options_json holds a JSON list of choices when grading_type='multiple_choice';
-- answer_key stores the correct option's exact text either way, so grading logic
-- doesn't need to branch on index vs text.
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL REFERENCES domains(id),
    content TEXT NOT NULL,          -- the prompt/question shown to the user
    answer_key TEXT,                -- reference answer / test spec / rubric, grader-dependent
    grading_type TEXT NOT NULL,     -- 'exact_match' | 'multiple_choice' | 'code_test' | 'rubric_llm'
    options_json TEXT,              -- JSON array of choices, only for multiple_choice
    -- FSRS state, mirrors the library's Card fields
    fsrs_state INTEGER,
    fsrs_step INTEGER,
    stability REAL,
    difficulty REAL,
    due TEXT,
    last_review TEXT,
    -- flags a card as agent-generated for a struggling concept, vs. part of the base curriculum
    is_remedial INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS card_concepts (
    card_id TEXT NOT NULL REFERENCES cards(id),
    concept_id TEXT NOT NULL REFERENCES concepts(id),
    PRIMARY KEY (card_id, concept_id)
);

CREATE TABLE IF NOT EXISTS review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES cards(id),
    concept_id TEXT NOT NULL REFERENCES concepts(id),
    correct INTEGER NOT NULL,       -- 1 or 0, feeds BKT
    rating INTEGER NOT NULL,        -- 1-4, feeds FSRS (again/hard/good/easy)
    reviewed_at TEXT NOT NULL
);

-- Per-domain daily practice goal, used for the Goals tab and streak tracking.
-- hobby drives analogy personalization in AI-generated content (e.g. "baking"
-- makes generated explanations and cards use baking analogies).
CREATE TABLE IF NOT EXISTS domain_settings (
    domain_id TEXT PRIMARY KEY REFERENCES domains(id),
    daily_goal INTEGER NOT NULL DEFAULT 5,
    hobby TEXT,
    course_level TEXT
);

-- Uploaded reference material for the Doc Explainer feature. file_path points
-- to disk (under uploads/), not stored in the DB directly. explanation is
-- null until generated (upload and explanation are separate steps/costs).
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL REFERENCES domains(id),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,  -- 'pdf' | 'txt' | 'md' | 'image'
    file_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    explanation TEXT
);

