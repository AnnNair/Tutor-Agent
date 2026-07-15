"""
Database access layer. This is the only file that touches SQLite directly --
everything else (curriculum, cli) goes through these functions.
"""
import sqlite3
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from fsrs import Scheduler, Card as FSRSCard, Rating, State

from bkt import BKTParams, update_mastery

DB_PATH = Path(__file__).parent / "tutor.db"
_scheduler = Scheduler()  # uses library default parameters


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    with open(Path(__file__).parent / "schema.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """
    Adds columns introduced after the initial schema to any pre-existing
    database. CREATE TABLE IF NOT EXISTS (in schema.sql) only helps for
    brand-new tables -- existing tutor.db files need these added explicitly.
    Each ALTER is wrapped since SQLite has no "ADD COLUMN IF NOT EXISTS".
    """
    migrations = [
        ("concepts", "module_name", "TEXT"),
        ("concepts", "synopsis", "TEXT"),
        ("concepts", "technical_breakdown", "TEXT"),
        ("concepts", "hobby_analogy", "TEXT"),
        ("concepts", "worked_example", "TEXT"),
        ("cards", "options_json", "TEXT"),
        ("domain_settings", "hobby", "TEXT"),
        ("domain_settings", "course_level", "TEXT"),
        ("domains", "user_id", "TEXT"),
    ]
    for table, column, coltype in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise  # a real problem, not just "already migrated"


# ---------- auth ----------

def create_user(email: str, password: str) -> str:
    from auth import hash_password
    conn = get_conn()
    existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        conn.close()
        raise ValueError("An account with that email already exists.")
    user_id = str(uuid.uuid4())[:12]
    password_hash, salt = hash_password(password)
    conn.execute(
        "INSERT INTO users (id, email, password_hash, password_salt, created_at) VALUES (?,?,?,?,?)",
        (user_id, email, password_hash, salt, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return user_id


def verify_login(email: str, password: str) -> str | None:
    from auth import verify_password
    conn = get_conn()
    row = conn.execute("SELECT id, password_hash, password_salt FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row["password_hash"], row["password_salt"]):
        return None
    return row["id"]


def create_session(user_id: str, days_valid: int = 30) -> str:
    from auth import generate_session_token
    from datetime import timedelta
    token = generate_session_token()
    now = datetime.now(timezone.utc)
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, now.isoformat(), (now + timedelta(days=days_valid)).isoformat()),
    )
    conn.commit()
    conn.close()
    return token


def get_user_id_from_session(token: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE token=?", (token,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return row["user_id"]


def delete_session(token: str):
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()


def domain_belongs_to_user(domain_id: str, user_id: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM domains WHERE id=?", (domain_id,)).fetchone()
    conn.close()
    return row is not None and row["user_id"] == user_id


def concept_belongs_to_user(concept_id: str, user_id: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT d.user_id FROM domains d JOIN concepts c ON c.domain_id = d.id WHERE c.id=?", (concept_id,)
    ).fetchone()
    conn.close()
    return row is not None and row["user_id"] == user_id


def card_belongs_to_user(card_id: str, user_id: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT d.user_id FROM domains d JOIN cards c ON c.domain_id = d.id WHERE c.id=?", (card_id,)
    ).fetchone()
    conn.close()
    return row is not None and row["user_id"] == user_id


def document_belongs_to_user(doc_id: str, user_id: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT d.user_id FROM domains d JOIN documents doc ON doc.domain_id = d.id WHERE doc.id=?", (doc_id,)
    ).fetchone()
    conn.close()
    return row is not None and row["user_id"] == user_id


# ---------- content setup ----------

def get_or_create_cli_user() -> str:
    """
    The CLI is a personal terminal tool, not part of the multi-user web app's
    login system -- it operates as a fixed local pseudo-account so `cli.py`
    keeps working exactly as before without requiring a signup/login flow.
    """
    conn = get_conn()
    row = conn.execute("SELECT id FROM users WHERE email=?", ("cli@local",)).fetchone()
    if row:
        conn.close()
        return row["id"]
    conn.close()
    from auth import hash_password
    user_id = "cli-local-user"
    password_hash, salt = hash_password(secrets_token())
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (id, email, password_hash, password_salt, created_at) VALUES (?,?,?,?,?)",
        (user_id, "cli@local", password_hash, salt, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return user_id


def secrets_token() -> str:
    import secrets
    return secrets.token_urlsafe(24)


def add_domain(domain_id: str, name: str, user_id: str) -> str:
    """
    Returns the actual domain id used -- may differ from the requested domain_id
    if it collides with an existing one. This matters specifically because of
    multi-tenancy: domain ids are slugified names ("german"), and two different
    users independently naming a subject "German" would otherwise collide on the
    same primary key, silently failing the second user's creation (INSERT OR
    IGNORE would do nothing). Resolving the collision here, once, protects every
    caller (API, CLI, syllabus generator) instead of requiring each to handle it.
    """
    conn = get_conn()
    candidate = domain_id
    n = 2
    while conn.execute("SELECT 1 FROM domains WHERE id=?", (candidate,)).fetchone():
        candidate = f"{domain_id}_{n}"
        n += 1
    conn.execute("INSERT INTO domains (id, name, user_id) VALUES (?, ?, ?)", (candidate, name, user_id))
    conn.commit()
    conn.close()
    return candidate


def add_concept(concept_id: str, domain_id: str, name: str, prerequisites: list[str] | None = None,
                 module_name: str | None = None, synopsis: str | None = None,
                 technical_breakdown: str | None = None, hobby_analogy: str | None = None,
                 worked_example: str | None = None):
    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO concepts (id, domain_id, name, module_name, synopsis, technical_breakdown, hobby_analogy, worked_example)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (concept_id, domain_id, name, module_name, synopsis, technical_breakdown, hobby_analogy, worked_example),
    )
    conn.execute(
        """INSERT OR IGNORE INTO concept_mastery (concept_id, p_mastery, p_learn, p_slip, p_guess)
           VALUES (?, 0.1, 0.15, 0.1, 0.2)""",
        (concept_id,),
    )
    for prereq in (prerequisites or []):
        conn.execute(
            "INSERT OR IGNORE INTO concept_prerequisites (concept_id, prerequisite_id) VALUES (?, ?)",
            (concept_id, prereq),
        )
    conn.commit()
    conn.close()


def add_card(domain_id: str, content: str, answer_key: str, grading_type: str,
             concept_ids: list[str], is_remedial: bool = False, options: list[str] | None = None) -> str:
    card_id = str(uuid.uuid4())[:8]
    fsrs_card = FSRSCard()  # fresh card, due immediately, state=Learning
    options_json = json.dumps(options) if options else None
    conn = get_conn()
    conn.execute(
        """INSERT INTO cards (id, domain_id, content, answer_key, grading_type, options_json,
                               fsrs_state, fsrs_step, stability, difficulty, due, last_review, is_remedial)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (card_id, domain_id, content, answer_key, grading_type, options_json,
         fsrs_card.state.value, fsrs_card.step, fsrs_card.stability, fsrs_card.difficulty,
         fsrs_card.due.isoformat(), None, int(is_remedial)),
    )
    for cid in concept_ids:
        conn.execute("INSERT INTO card_concepts (card_id, concept_id) VALUES (?, ?)", (card_id, cid))
    conn.commit()
    conn.close()
    return card_id


def set_concept_explanation(concept_id: str, synopsis: str, technical_breakdown: str,
                             hobby_analogy: str | None, worked_example: str | None = None):
    conn = get_conn()
    conn.execute(
        "UPDATE concepts SET synopsis=?, technical_breakdown=?, hobby_analogy=?, worked_example=? WHERE id=?",
        (synopsis, technical_breakdown, hobby_analogy, worked_example, concept_id),
    )
    conn.commit()
    conn.close()


def add_document(domain_id: str, filename: str, file_type: str, file_path: str) -> str:
    doc_id = str(uuid.uuid4())[:8]
    conn = get_conn()
    conn.execute(
        "INSERT INTO documents (id, domain_id, filename, file_type, file_path, uploaded_at) VALUES (?,?,?,?,?,?)",
        (doc_id, domain_id, filename, file_type, file_path, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return doc_id


def set_document_explanation(doc_id: str, explanation: str):
    conn = get_conn()
    conn.execute("UPDATE documents SET explanation=? WHERE id=?", (explanation, doc_id))
    conn.commit()
    conn.close()


def set_course_level(domain_id: str, level: str):
    conn = get_conn()
    conn.execute(
        """INSERT INTO domain_settings (domain_id, course_level) VALUES (?, ?)
           ON CONFLICT(domain_id) DO UPDATE SET course_level=excluded.course_level""",
        (domain_id, level),
    )
    conn.commit()
    conn.close()


def get_course_level(domain_id: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT course_level FROM domain_settings WHERE domain_id=?", (domain_id,)).fetchone()
    conn.close()
    return row["course_level"] if row else None


def set_hobby(domain_id: str, hobby: str):
    conn = get_conn()
    conn.execute(
        """INSERT INTO domain_settings (domain_id, hobby) VALUES (?, ?)
           ON CONFLICT(domain_id) DO UPDATE SET hobby=excluded.hobby""",
        (domain_id, hobby),
    )
    conn.commit()
    conn.close()


def get_hobby(domain_id: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT hobby FROM domain_settings WHERE domain_id=?", (domain_id,)).fetchone()
    conn.close()
    return row["hobby"] if row else None


# ---------- review cycle ----------

def _row_to_fsrs_card(row: sqlite3.Row) -> FSRSCard:
    return FSRSCard(
        card_id=0,
        state=State(row["fsrs_state"]),
        step=row["fsrs_step"],
        stability=row["stability"],
        difficulty=row["difficulty"],
        due=datetime.fromisoformat(row["due"]),
        last_review=datetime.fromisoformat(row["last_review"]) if row["last_review"] else None,
    )


def review_card(card_id: str, correct: bool, rating: Rating):
    """
    Records one review: advances FSRS scheduling for the card, and updates
    BKT mastery for every concept that card is tagged with.
    """
    conn = get_conn()
    card_row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    fsrs_card = _row_to_fsrs_card(card_row)

    updated_card, _log = _scheduler.review_card(fsrs_card, rating)

    conn.execute(
        """UPDATE cards SET fsrs_state=?, fsrs_step=?, stability=?, difficulty=?, due=?, last_review=?
           WHERE id=?""",
        (updated_card.state.value, updated_card.step, updated_card.stability, updated_card.difficulty,
         updated_card.due.isoformat(), updated_card.last_review.isoformat(), card_id),
    )

    concept_ids = [r["concept_id"] for r in
                   conn.execute("SELECT concept_id FROM card_concepts WHERE card_id=?", (card_id,))]

    now = datetime.now(timezone.utc).isoformat()
    for cid in concept_ids:
        m = conn.execute("SELECT * FROM concept_mastery WHERE concept_id=?", (cid,)).fetchone()
        params = BKTParams(m["p_mastery"], m["p_learn"], m["p_slip"], m["p_guess"])
        new_p = update_mastery(params, correct)
        conn.execute(
            "UPDATE concept_mastery SET p_mastery=?, last_updated=? WHERE concept_id=?",
            (new_p, now, cid),
        )
        conn.execute(
            "INSERT INTO review_logs (card_id, concept_id, correct, rating, reviewed_at) VALUES (?,?,?,?,?)",
            (card_id, cid, int(correct), rating.value, now),
        )

    conn.commit()
    conn.close()
    return concept_ids
