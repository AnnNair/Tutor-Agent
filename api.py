"""
FastAPI backend. Every endpoint that touches a domain, concept, card, or
document requires a logged-in session and verifies ownership before returning
or modifying anything -- this is the actual privacy boundary for multi-user
hosting, not just the login screen.

Ownership checks return 404 (not 403) when a resource exists but isn't yours --
standard practice so a user can't distinguish "doesn't exist" from "exists but
isn't yours" by probing IDs.
"""
import json as json_module
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Cookie, Response, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fsrs import Rating

from db import get_conn, review_card, add_domain, init_db
from curriculum import next_action
import syllabus_generator

app = FastAPI(title="Tutor Agent")
init_db()

RATING_MAP = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}


# ---------- auth ----------

def get_current_user(session: str | None = Cookie(default=None)) -> str:
    if not session:
        raise HTTPException(401, "not logged in")
    from db import get_user_id_from_session
    user_id = get_user_id_from_session(session)
    if not user_id:
        raise HTTPException(401, "session expired or invalid")
    return user_id


class SignupRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/signup")
def signup(req: SignupRequest, response: Response):
    from db import create_user, create_session
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "enter a valid email")
    if len(req.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    try:
        user_id = create_user(email, req.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = create_session(user_id)
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=30*24*3600)
    return {"ok": True, "email": email}


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    from db import verify_login, create_session
    user_id = verify_login(req.email.strip().lower(), req.password)
    if not user_id:
        raise HTTPException(401, "wrong email or password")
    token = create_session(user_id)
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=30*24*3600)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None)):
    from db import delete_session
    if session:
        delete_session(session)
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: str = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT email FROM users WHERE id=?", (user,)).fetchone()
    conn.close()
    return {"user_id": user, "email": row["email"] if row else None}


# ---------- ownership helpers ----------

def _require_domain_owner(domain_id: str, user: str):
    from db import domain_belongs_to_user
    if not domain_belongs_to_user(domain_id, user):
        raise HTTPException(404, "domain not found")


def _require_concept_owner(concept_id: str, user: str):
    from db import concept_belongs_to_user
    if not concept_belongs_to_user(concept_id, user):
        raise HTTPException(404, "concept not found")


def _require_card_owner(card_id: str, user: str):
    from db import card_belongs_to_user
    if not card_belongs_to_user(card_id, user):
        raise HTTPException(404, "card not found")


def _require_document_owner(doc_id: str, user: str):
    from db import document_belongs_to_user
    if not document_belongs_to_user(doc_id, user):
        raise HTTPException(404, "document not found")


# ---------- domains ----------

@app.get("/api/domains")
def list_domains(user: str = Depends(get_current_user)):
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM domains WHERE user_id=?", (user,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class DomainRequest(BaseModel):
    id: str
    name: str


@app.post("/api/domains")
def create_domain(req: DomainRequest, user: str = Depends(get_current_user)):
    actual_id = add_domain(req.id, req.name, user)
    return {"ok": True, "id": actual_id}


@app.get("/api/history/{concept_id}")
def concept_history(concept_id: str, limit: int = 16, user: str = Depends(get_current_user)):
    _require_concept_owner(concept_id, user)
    conn = get_conn()
    rows = conn.execute(
        """SELECT correct, reviewed_at FROM review_logs WHERE concept_id=?
           ORDER BY reviewed_at DESC LIMIT ?""",
        (concept_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


@app.get("/api/status/{domain_id}")
def status(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id, c.name, cm.p_mastery FROM concepts c
           JOIN concept_mastery cm ON cm.concept_id = c.id
           WHERE c.domain_id=? ORDER BY cm.p_mastery ASC""",
        (domain_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/next/{domain_id}")
def get_next(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    action = next_action(domain_id)
    if action.get("action") == "continue" and action["card"].get("options_json"):
        action["card"]["options"] = json_module.loads(action["card"]["options_json"])
    return action


class GradeRequest(BaseModel):
    card_id: str
    correct: bool
    felt: str = "good"


@app.post("/api/grade")
def grade(req: GradeRequest, user: str = Depends(get_current_user)):
    _require_card_owner(req.card_id, user)
    rating = Rating.Again if not req.correct else RATING_MAP.get(req.felt, Rating.Good)
    review_card(req.card_id, req.correct, rating)
    return {"ok": True}


@app.get("/api/stats/{domain_id}")
def stats(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    conn = get_conn()
    today = date.today().isoformat()
    reviewed_today = conn.execute(
        """SELECT COUNT(*) c FROM review_logs
           WHERE reviewed_at LIKE ? AND card_id IN
           (SELECT id FROM cards WHERE domain_id=?)""",
        (f"{today}%", domain_id),
    ).fetchone()["c"]

    goal_row = conn.execute("SELECT daily_goal FROM domain_settings WHERE domain_id=?", (domain_id,)).fetchone()
    daily_goal = goal_row["daily_goal"] if goal_row else 5

    days = conn.execute(
        """SELECT DISTINCT substr(reviewed_at, 1, 10) d FROM review_logs
           WHERE card_id IN (SELECT id FROM cards WHERE domain_id=?)
           ORDER BY d DESC""",
        (domain_id,),
    ).fetchall()
    conn.close()

    streak = 0
    day_set = {r["d"] for r in days}
    cursor = date.today()
    while cursor.isoformat() in day_set:
        streak += 1
        cursor -= timedelta(days=1)

    return {"reviewed_today": reviewed_today, "daily_goal": daily_goal, "streak": streak}


class GoalRequest(BaseModel):
    daily_goal: int


@app.post("/api/goals/{domain_id}")
def set_goal(domain_id: str, req: GoalRequest, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    conn = get_conn()
    conn.execute(
        """INSERT INTO domain_settings (domain_id, daily_goal) VALUES (?, ?)
           ON CONFLICT(domain_id) DO UPDATE SET daily_goal=excluded.daily_goal""",
        (domain_id, req.daily_goal),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/curriculum/{domain_id}")
def curriculum(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    conn = get_conn()
    concepts = conn.execute(
        """SELECT c.id, c.name, c.module_name, c.synopsis, c.technical_breakdown, c.hobby_analogy, c.worked_example, cm.p_mastery
           FROM concepts c JOIN concept_mastery cm ON cm.concept_id = c.id
           WHERE c.domain_id=?""",
        (domain_id,),
    ).fetchall()
    result = []
    for c in concepts:
        prereqs = [r["prerequisite_id"] for r in conn.execute(
            "SELECT prerequisite_id FROM concept_prerequisites WHERE concept_id=?", (c["id"],)
        )]
        card_count = conn.execute(
            "SELECT COUNT(*) n FROM card_concepts WHERE concept_id=?", (c["id"],)
        ).fetchone()["n"]
        review_count = conn.execute(
            "SELECT COUNT(*) n FROM review_logs WHERE concept_id=?", (c["id"],)
        ).fetchone()["n"]
        result.append({
            "id": c["id"], "name": c["name"], "module_name": c["module_name"], "p_mastery": c["p_mastery"],
            "prerequisites": prereqs, "card_count": card_count, "review_count": review_count,
            "synopsis": c["synopsis"], "technical_breakdown": c["technical_breakdown"],
            "hobby_analogy": c["hobby_analogy"], "worked_example": c["worked_example"],
        })
    conn.close()
    return result


@app.get("/api/concept-cards/{concept_id}")
def concept_cards(concept_id: str, user: str = Depends(get_current_user)):
    _require_concept_owner(concept_id, user)
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id, c.content, c.answer_key, c.grading_type, c.options_json FROM cards c
           WHERE c.id IN (SELECT card_id FROM card_concepts WHERE concept_id=?)""",
        (concept_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["options"] = json_module.loads(d["options_json"]) if d["options_json"] else None
        del d["options_json"]
        result.append(d)
    return result


class ModuleQuizRequest(BaseModel):
    domain_id: str
    module_name: str


@app.post("/api/module-quiz")
def module_quiz(req: ModuleQuizRequest, user: str = Depends(get_current_user)):
    _require_domain_owner(req.domain_id, user)
    conn = get_conn()
    concepts = conn.execute(
        "SELECT name, synopsis, technical_breakdown FROM concepts WHERE domain_id=? AND module_name=?",
        (req.domain_id, req.module_name),
    ).fetchall()
    conn.close()
    if not concepts:
        raise HTTPException(404, "no concepts found for this module")
    try:
        questions = syllabus_generator.generate_module_quiz(req.module_name, [dict(c) for c in concepts])
        return {"questions": questions}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/domain-meta/{domain_id}")
def domain_meta(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    from db import get_hobby, get_course_level
    return {"hobby": get_hobby(domain_id), "course_level": get_course_level(domain_id)}


@app.get("/api/hobby/{domain_id}")
def get_hobby_endpoint(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    from db import get_hobby
    return {"hobby": get_hobby(domain_id)}


class HobbyRequest(BaseModel):
    hobby: str


@app.post("/api/hobby/{domain_id}")
def set_hobby_endpoint(domain_id: str, req: HobbyRequest, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    from db import set_hobby
    set_hobby(domain_id, req.hobby)
    return {"ok": True}


@app.get("/api/ledger/{domain_id}")
def ledger(domain_id: str, user: str = Depends(get_current_user)):
    """
    FSRS under the hood, not literal SM-2 -- stability plays the role SM-2's
    "interval" plays, difficulty plays a role similar to an ease factor, but
    the scales/formulas aren't the same. Labeled honestly in the UI as FSRS.
    """
    _require_domain_owner(domain_id, user)
    conn = get_conn()
    concepts = conn.execute(
        """SELECT c.id, c.name, c.module_name FROM concepts c WHERE c.domain_id=?""",
        (domain_id,),
    ).fetchall()
    result = []
    for c in concepts:
        cards = conn.execute(
            """SELECT stability, difficulty, due FROM cards
               WHERE id IN (SELECT card_id FROM card_concepts WHERE concept_id=?)""",
            (c["id"],),
        ).fetchall()
        review_count = conn.execute(
            "SELECT COUNT(*) n FROM review_logs WHERE concept_id=?", (c["id"],)
        ).fetchone()["n"]
        stabilities = [r["stability"] for r in cards if r["stability"] is not None]
        difficulties = [r["difficulty"] for r in cards if r["difficulty"] is not None]
        dues = [r["due"] for r in cards if r["due"] is not None]
        result.append({
            "id": c["id"], "name": c["name"], "module_name": c["module_name"],
            "review_count": review_count,
            "stability": round(sum(stabilities) / len(stabilities), 2) if stabilities else 0,
            "difficulty": round(sum(difficulties) / len(difficulties), 2) if difficulties else 0,
            "next_due": min(dues) if dues else None,
        })
    conn.close()
    return result


class ChatRequest(BaseModel):
    concept_id: str
    message: str
    history: list[dict] = []


@app.post("/api/chat")
def chat(req: ChatRequest, user: str = Depends(get_current_user)):
    _require_concept_owner(req.concept_id, user)
    import mentor_chat
    conn = get_conn()
    concept = conn.execute("SELECT name, domain_id FROM concepts WHERE id=?", (req.concept_id,)).fetchone()
    from db import get_hobby
    hobby = get_hobby(concept["domain_id"])
    conn.close()
    try:
        reply = mentor_chat.reply(concept["name"], hobby, req.history, req.message)
        return {"reply": reply}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


class SyllabusRequest(BaseModel):
    goal_description: str
    hobby: str = ""


@app.post("/api/generate-syllabus")
def generate_syllabus_endpoint(req: SyllabusRequest, user: str = Depends(get_current_user)):
    try:
        hobby = req.hobby.strip() or None
        data = syllabus_generator.generate_syllabus(req.goal_description, hobby=hobby)
        domain_id = syllabus_generator.apply_syllabus(data, user_id=user, hobby=hobby)
        return {"ok": True, "domain_id": domain_id, "domain_name": data["domain_name"],
                "concept_count": len(data["concepts"])}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


class ConceptRequest(BaseModel):
    id: str
    domain_id: str
    name: str
    prerequisites: list[str] = []
    module_name: str = ""


@app.post("/api/concepts")
def create_concept(req: ConceptRequest, user: str = Depends(get_current_user)):
    _require_domain_owner(req.domain_id, user)
    from db import add_concept
    add_concept(req.id, req.domain_id, req.name, prerequisites=req.prerequisites,
                module_name=req.module_name.strip() or None)
    return {"ok": True}


class CardRequest(BaseModel):
    domain_id: str
    content: str
    answer_key: str
    concept_ids: list[str]
    grading_type: str = "exact_match"
    options: list[str] = []


@app.post("/api/cards")
def create_card(req: CardRequest, user: str = Depends(get_current_user)):
    _require_domain_owner(req.domain_id, user)
    from db import add_card
    card_id = add_card(req.domain_id, req.content, req.answer_key, req.grading_type,
                        req.concept_ids, options=req.options or None)
    return {"ok": True, "card_id": card_id}


@app.post("/api/generate-explanation/{concept_id}")
def generate_explanation(concept_id: str, user: str = Depends(get_current_user)):
    _require_concept_owner(concept_id, user)
    conn = get_conn()
    concept = conn.execute("SELECT name, domain_id FROM concepts WHERE id=?", (concept_id,)).fetchone()
    domain = conn.execute("SELECT name FROM domains WHERE id=?", (concept["domain_id"],)).fetchone()
    from db import get_hobby, set_concept_explanation
    hobby = get_hobby(concept["domain_id"])
    conn.close()
    try:
        data = syllabus_generator.generate_concept_explanation(concept["name"], domain["name"], hobby)
        set_concept_explanation(concept_id, data.get("synopsis", ""), data.get("technical_breakdown", ""),
                                 data.get("hobby_analogy"), data.get("worked_example"))
        return data
    except RuntimeError as e:
        raise HTTPException(400, str(e))


UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/api/documents/{domain_id}")
async def upload_document(domain_id: str, file: UploadFile = File(...), user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    import doc_explainer
    try:
        file_type = doc_explainer.detect_file_type(file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))

    from db import add_document
    domain_dir = UPLOAD_DIR / domain_id
    domain_dir.mkdir(exist_ok=True)
    doc_id_placeholder = str(uuid.uuid4())[:8]
    dest_path = domain_dir / f"{doc_id_placeholder}_{file.filename}"
    contents = await file.read()
    MAX_SIZE = 15 * 1024 * 1024
    if len(contents) > MAX_SIZE:
        raise HTTPException(400, "File too large (max 15MB).")
    with open(dest_path, "wb") as f:
        f.write(contents)

    doc_id = add_document(domain_id, file.filename, file_type, str(dest_path))
    return {"ok": True, "doc_id": doc_id, "filename": file.filename, "file_type": file_type}


@app.get("/api/documents/{domain_id}")
def list_documents(domain_id: str, user: str = Depends(get_current_user)):
    _require_domain_owner(domain_id, user)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, filename, file_type, uploaded_at, explanation FROM documents WHERE domain_id=? ORDER BY uploaded_at DESC",
        (domain_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/documents/{doc_id}/explain")
def explain_document_endpoint(doc_id: str, user: str = Depends(get_current_user)):
    _require_document_owner(doc_id, user)
    import doc_explainer
    conn = get_conn()
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    from db import get_hobby, set_document_explanation
    hobby = get_hobby(doc["domain_id"])
    conn.close()
    try:
        explanation = doc_explainer.explain_document(doc["file_path"], doc["file_type"], hobby)
        set_document_explanation(doc_id, explanation)
        return {"explanation": explanation}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


# serve the frontend
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
