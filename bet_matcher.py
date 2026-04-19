"""
Bet matching logic.
Compares a target bet against scraped bets using fuzzy string matching and odds tolerance.
"""

import logging
from rapidfuzz import fuzz
from typing import Optional

logger = logging.getLogger(__name__)


class BetMatcher:
    def __init__(self, similarity_threshold: int = 80, odds_tolerance: float = 0.05):
        """
        similarity_threshold: minimum fuzzy match score (0-100) to consider a match
        odds_tolerance: fraction allowed deviation from target odds (e.g. 0.05 = ±5%)
        """
        self.similarity_threshold = similarity_threshold
        self.odds_tolerance = odds_tolerance

    def match(self, target: dict, candidate: dict) -> tuple[bool, bool, float]:
        """
        Compare target bet against a candidate bet.

        Returns:
            (is_exact, is_similar, similarity_score)
        """
        event_score = self._score_text(
            target.get("event", ""),
            candidate.get("event", "")
        )
        market_score = self._score_text(
            target.get("market", ""),
            candidate.get("market", "")
        )
        selection_score = self._score_text(
            target.get("selection", ""),
            candidate.get("selection", "")
        )

        # Weight event heavily; market and selection contribute if provided
        if target.get("market") and target.get("selection"):
            overall = event_score * 0.5 + market_score * 0.25 + selection_score * 0.25
        elif target.get("market"):
            overall = event_score * 0.6 + market_score * 0.4
        elif target.get("selection"):
            overall = event_score * 0.6 + selection_score * 0.4
        else:
            overall = float(event_score)

        odds_match = self._odds_match(
            target.get("odds"),
            candidate.get("odds")
        )

        is_similar = overall >= self.similarity_threshold
        is_exact = overall >= 95 and (odds_match or target.get("odds") is None)

        return is_exact, is_similar, round(overall, 1)

    def _score_text(self, a: str, b: str) -> float:
        """Fuzzy score between two strings (0-100)."""
        if not a or not b:
            return 50.0  # neutral when either is missing
        a = a.lower().strip()
        b = b.lower().strip()
        token_score = fuzz.token_set_ratio(a, b)
        partial_score = fuzz.partial_ratio(a, b)
        return max(token_score, partial_score)

    def _odds_match(self, target_odds: Optional[float], candidate_odds: Optional[float]) -> bool:
        """Check if odds are within tolerance."""
        if target_odds is None or candidate_odds is None:
            return True  # can't compare, don't penalise
        lower = target_odds * (1 - self.odds_tolerance)
        upper = target_odds * (1 + self.odds_tolerance)
        return lower <= candidate_odds <= upper

    def filter_results(self, target: dict, candidates: list[dict]) -> list[dict]:
        """
        Filter and annotate a list of candidates.
        Returns list with added keys: is_exact, is_similar, similarity_score.
        Sorted by similarity descending.
        """
        annotated = []
        for c in candidates:
            is_exact, is_similar, score = self.match(target, c)
            if is_exact or is_similar:
                annotated.append({
                    **c,
                    "is_exact": is_exact,
                    "is_similar": is_similar,
                    "similarity_score": score,
                })

        annotated.sort(key=lambda x: x["similarity_score"], reverse=True)
        return annotated
