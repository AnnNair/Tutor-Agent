"""
Curriculum policy: decides what the user sees next.

This file contains no ML -- it's the decision layer that sits on top of
FSRS (scheduling) and BKT (mastery), reading both and choosing between
"give a remedial exercise" and "continue the normal sequence".
"""
from datetime import datetime, timezone
from db import get_conn

STRUGGLE_THRESHOLD = 0.4   # below this mastery, the concept counts as "struggling"
TREND_WINDOW = 5           # how many recent reviews we look at to judge a trend


def due_cards(domain_id: str, limit: int = 10):
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT * FROM cards WHERE domain_id=? AND due<=? ORDER BY due ASC LIMIT ?""",
        (domain_id, now, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mastery_trend(concept_id: str) -> float:
    """
    Returns the slope of correctness over the last TREND_WINDOW reviews for
    a concept. Negative or flat-near-zero means the user isn't improving --
    that's the signal for remediation, not a single low mastery score, since
    someone new to a concept is *supposed* to start low.
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT correct FROM review_logs WHERE concept_id=?
           ORDER BY reviewed_at DESC LIMIT ?""",
        (concept_id, TREND_WINDOW),
    ).fetchall()
    conn.close()
    if len(rows) < 2:
        return 0.0  # not enough data to call a trend yet
    values = [r["correct"] for r in reversed(rows)]  # oldest -> newest
    n = len(values)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


def prerequisites_met(concept_id: str, threshold: float = 0.6) -> bool:
    conn = get_conn()
    prereqs = [r["prerequisite_id"] for r in
               conn.execute("SELECT prerequisite_id FROM concept_prerequisites WHERE concept_id=?",
                             (concept_id,))]
    if not prereqs:
        conn.close()
        return True
    ok = True
    for pid in prereqs:
        m = conn.execute("SELECT p_mastery FROM concept_mastery WHERE concept_id=?", (pid,)).fetchone()
        if not m or m["p_mastery"] < threshold:
            ok = False
            break
    conn.close()
    return ok


def next_action(domain_id: str) -> dict:
    """
    This is the branch from the diagram. Returns either:
      {"action": "remediate", "concept_id": ..., "reason": ...}
      {"action": "continue", "card": {...}}
      {"action": "idle"}   -- nothing due and nothing struggling
    """
    conn = get_conn()
    concepts = conn.execute(
        """SELECT cm.concept_id, cm.p_mastery, c.domain_id FROM concept_mastery cm
           JOIN concepts c ON c.id = cm.concept_id WHERE c.domain_id=?""",
        (domain_id,),
    ).fetchall()
    conn.close()

    # branch: struggling check first -- a flat/negative trend while mastery
    # is still low takes priority over the normal queue. Requires at least
    # 2 reviews so a never-attempted concept isn't mistaken for a stuck one.
    conn = get_conn()
    for row in concepts:
        cid, mastery = row["concept_id"], row["p_mastery"]
        review_count = conn.execute(
            "SELECT COUNT(*) c FROM review_logs WHERE concept_id=?", (cid,)
        ).fetchone()["c"]
        if review_count >= 2 and mastery < STRUGGLE_THRESHOLD and mastery_trend(cid) <= 0.05:
            return {
                "action": "remediate",
                "concept_id": cid,
                "mastery": mastery,
                "reason": f"mastery is {mastery:.2f} and not improving over last reviews",
            }
    conn.close()

    # branch: continue planned curriculum -- next due card, respecting prerequisites
    for card in due_cards(domain_id):
        conn = get_conn()
        card_concepts = [r["concept_id"] for r in
                          conn.execute("SELECT concept_id FROM card_concepts WHERE card_id=?", (card["id"],))]
        conn.close()
        if all(prerequisites_met(cid) for cid in card_concepts):
            return {"action": "continue", "card": card}

    return {"action": "idle"}
