"""
Command-line interface for the tutor agent.

    python cli.py init
    python cli.py add-domain dsa "Data Structures & Algorithms"
    python cli.py add-concept two_pointers dsa "Two pointers"
    python cli.py add-concept sliding_window dsa "Sliding window" --prereq two_pointers
    python cli.py add-card dsa "Reverse array in place" "two pointers from both ends" exact_match --concepts two_pointers
    python cli.py review dsa
    python cli.py status dsa
"""
import argparse
import sys

from fsrs import Rating

from db import init_db, add_domain, add_concept, add_card, review_card, get_conn, get_or_create_cli_user
from curriculum import next_action


def cmd_init(args):
    init_db()
    print("Database initialized: tutor.db")


def cmd_add_domain(args):
    user_id = get_or_create_cli_user()
    actual_id = add_domain(args.id, args.name, user_id)
    print(f"Domain '{actual_id}' added.")


def cmd_add_concept(args):
    add_concept(args.id, args.domain, args.name, prerequisites=args.prereq or [])
    print(f"Concept '{args.id}' added to domain '{args.domain}'.")


def cmd_add_card(args):
    concept_ids = args.concepts.split(",")
    card_id = add_card(args.domain, args.content, args.answer, args.grading_type, concept_ids)
    print(f"Card added: {card_id}")


def _grade(card: dict) -> tuple[bool, Rating]:
    """
    Presents a card, collects a response, and returns (correct, fsrs_rating).
    exact_match is graded automatically. Everything else (code_test, rubric_llm)
    is a placeholder self-report until the real grading layer is built --
    printed explicitly so it's never mistaken for automated grading.
    """
    print("\n" + "-" * 60)
    print(card["content"])
    if card["grading_type"] == "exact_match":
        response = input("Your answer: ").strip()
        correct = response.lower() == (card["answer_key"] or "").strip().lower()
        print("Correct!" if correct else f"Incorrect. Answer: {card['answer_key']}")
        if correct:
            felt = input("How did that feel? (hard/good/easy) [good]: ").strip().lower() or "good"
            rating = {"hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}.get(felt, Rating.Good)
        else:
            rating = Rating.Again
    else:
        print(f"[grading_type='{card['grading_type']}' has no automated grader yet -- self-report]")
        input("Work through it, then press enter...")
        correct = input("Did you get it right? (y/n): ").strip().lower().startswith("y")
        rating = Rating.Good if correct else Rating.Again
    return correct, rating


def cmd_review(args):
    seen_remedial_for = set()
    count = 0
    while count < args.limit:
        action = next_action(args.domain)

        if action["action"] == "idle":
            print("\nNothing due and nothing struggling. Session complete.")
            break

        if action["action"] == "remediate":
            cid = action["concept_id"]
            if cid in seen_remedial_for:
                # avoid an infinite loop if remediation isn't implemented yet
                print(f"\n[Would generate a custom exercise for '{cid}' here -- "
                      f"remedial generation isn't wired up yet, skipping to avoid a loop.]")
                seen_remedial_for.add(cid)
                continue
            print(f"\nStruggling on concept '{cid}' ({action['reason']}).")
            seen_remedial_for.add(cid)
            continue

        card = action["card"]
        correct, rating = _grade(card)
        review_card(card["id"], correct, rating)
        count += 1

    show_status(args.domain)


def show_status(domain_id: str):
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id, c.name, cm.p_mastery FROM concepts c
           JOIN concept_mastery cm ON cm.concept_id = c.id
           WHERE c.domain_id=? ORDER BY cm.p_mastery ASC""",
        (domain_id,),
    ).fetchall()
    conn.close()
    print("\nMastery status:")
    for r in rows:
        bar = "#" * int(r["p_mastery"] * 20)
        print(f"  {r['name']:<20} {r['p_mastery']:.2f} {bar}")


def cmd_status(args):
    show_status(args.domain)


def main():
    parser = argparse.ArgumentParser(description="Micro-learning tutor agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    p = sub.add_parser("add-domain")
    p.add_argument("id")
    p.add_argument("name")
    p.set_defaults(func=cmd_add_domain)

    p = sub.add_parser("add-concept")
    p.add_argument("id")
    p.add_argument("domain")
    p.add_argument("name")
    p.add_argument("--prereq", action="append")
    p.set_defaults(func=cmd_add_concept)

    p = sub.add_parser("add-card")
    p.add_argument("domain")
    p.add_argument("content")
    p.add_argument("answer")
    p.add_argument("grading_type", choices=["exact_match", "code_test", "rubric_llm"])
    p.add_argument("--concepts", required=True, help="comma-separated concept ids")
    p.set_defaults(func=cmd_add_card)

    p = sub.add_parser("review")
    p.add_argument("domain")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("status")
    p.add_argument("domain")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
