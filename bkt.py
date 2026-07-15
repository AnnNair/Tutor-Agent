"""
Bayesian Knowledge Tracing.

Each concept has a hidden mastery probability P(know). Every review is an
observation (correct/incorrect) that updates this probability via Bayes' rule,
then accounts for the chance that practicing just now taught you the concept.

This module is pure math -- no DB access -- so it's easy to unit test in isolation.
"""
from dataclasses import dataclass


@dataclass
class BKTParams:
    p_mastery: float   # current P(know)
    p_learn: float      # P(unknown -> known per practice)
    p_slip: float        # P(known but answered wrong)
    p_guess: float       # P(unknown but answered right)


def update_mastery(params: BKTParams, correct: bool) -> float:
    """Returns the updated P(mastery) after one observed review."""
    p, learn, slip, guess = (
        params.p_mastery, params.p_learn, params.p_slip, params.p_guess
    )

    if correct:
        numerator = p * (1 - slip)
        denominator = numerator + (1 - p) * guess
    else:
        numerator = p * slip
        denominator = numerator + (1 - p) * (1 - guess)

    # guard against a degenerate denominator (shouldn't happen with sane params)
    p_given_obs = numerator / denominator if denominator > 0 else p

    # learning transition: practicing can teach you the concept even if you
    # didn't know it going in
    p_next = p_given_obs + (1 - p_given_obs) * learn

    return min(max(p_next, 0.0), 1.0)


if __name__ == "__main__":
    # sanity check: repeated correct answers should push mastery toward 1,
    # repeated wrong answers should push it toward the slip/guess floor
    params = BKTParams(p_mastery=0.1, p_learn=0.15, p_slip=0.1, p_guess=0.2)
    p = params.p_mastery
    for i in range(6):
        p = update_mastery(BKTParams(p, params.p_learn, params.p_slip, params.p_guess), correct=True)
        print(f"after correct #{i+1}: P(mastery) = {p:.3f}")
