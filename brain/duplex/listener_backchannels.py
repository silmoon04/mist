"""Conservative, local policy for optional listener acknowledgements.

The policy consumes only already available speech timestamps and transcript
text. It does not call an intent model or own any audio/playback resources.
Callers should revalidate a returned candidate immediately before playback and
call :meth:`playback_started` only when the cue really starts playing.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


_WORD_RE = re.compile(r"[\w]+(?:['’][\w]+)?", re.UNICODE)
_FINAL_MARK_RE = re.compile(r"[.!?…][\"'’”)]*\s*$")
_AUXILIARY = r"is|are|was|were|do|does|did|can|could|would|will|should|have|has|had"
_QUESTION_SUBJECT = r"i|you|we|they|he|she|it|that|this|there|a|an|the|my|your|our|their|anyone|someone"
_DIRECT_QUESTION_RE = re.compile(
    rf"^\s*(?:(?:who|what|when|where|why|how|which|whose)\s+(?:{_AUXILIARY})\b|"
    rf"(?:{_AUXILIARY})\s+(?:{_QUESTION_SUBJECT})\b)",
    re.IGNORECASE,
)
_TAG_QUESTION_RE = re.compile(
    rf",\s*(?:(?:{_AUXILIARY})\s+(?:{_QUESTION_SUBJECT})|"
    r"isn't it|aren't they|isn't that|right|okay|you know|don't you think)\s*[.!…]*\s*$",
    re.IGNORECASE,
)
_QUIET_RE = re.compile(
    r"\b(?:please\s+)?(?:be\s+quiet|stay\s+quiet|stop\s+talking|"
    r"don't\s+(?:talk|speak|say anything)|do\s+not\s+(?:talk|speak)|"
    r"let\s+me\s+(?:think|practice|speak)|i(?:'m| am)\s+practicing)\b",
    re.IGNORECASE,
)
_DISTRESS_RE = re.compile(
    r"\b(?:help me|i need help|i(?:'m| am) (?:scared|afraid|panicking|terrified|"
    r"devastated|heartbroken|in danger|not safe)|can't breathe|cannot breathe|"
    r"want to die|hurt myself|going to hurt|someone is hurt|someone got hurt|"
    r"there(?:'s| is) a fire|bad news|terrible news|my (?:mum|mom|mother|dad|"
    r"father|brother|sister|friend|partner) (?:died|has died|passed away)|"
    r"i (?:lost|have lost) (?:my job|my home|someone)|i got (?:fired|laid off)|"
    r"i(?:'m| am) (?:really )?(?:worried|upset|distressed)|(?:crash|accident)\b)",
    re.IGNORECASE,
)
_OPEN_END_RE = re.compile(
    r"\b(?:and|but|or|so|because|although|though|if|when|while|unless|"
    r"as|since|which|that|who|where|to|from|with|about|for|at|in|on|"
    r"into|through|the|a|an|this|that|these|those|my|your|our|their|"
    r"is|are|was|were|am|be|been|being|have|has|had|do|does|did|"
    r"could|would|should|might|may|will|can|must|think|guess|wonder|"
    r"know|mean|remember|realize|feel|seem|look)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BackchannelDecision:
    """One short-lived cue candidate; it is not proof that audio was played."""

    emit: bool
    cue: Optional[str]
    reason: str
    trigger: str
    floor_id: str
    issued_at: float
    expires_at: float
    transcript_key: str
    urgency: str = "low"
    cancel_if_user_resumes: bool = True


class ListenerBackchannelPolicy:
    """Deterministic gate for a single brief, floor-preserving ``mhm`` cue.

    ``speech_active`` is a *recent voiced-PCM* veto supplied by the caller,
    not the STT endpoint's utterance-open flag. ``now`` values are caller-owned
    monotonic seconds, which keeps this object deterministic and fake-clock
    friendly.
    """

    def __init__(
        self,
        *,
        min_floor_seconds: float = 5.0,
        min_words: int = 8,
        min_pause_ms: int = 350,
        max_pause_ms: int = 1100,
        cooldown_seconds: float = 18.0,
        candidate_ttl_seconds: float = 0.35,
    ) -> None:
        if min_floor_seconds < 0 or min_words < 1:
            raise ValueError("floor duration and word minimum must be positive")
        if min_pause_ms < 0 or max_pause_ms < min_pause_ms:
            raise ValueError("pause window is invalid")
        if cooldown_seconds < 0 or candidate_ttl_seconds <= 0:
            raise ValueError("cooldown must be nonnegative and candidate TTL positive")
        self.min_floor_seconds = float(min_floor_seconds)
        self.min_words = int(min_words)
        self.min_pause_ms = int(min_pause_ms)
        self.max_pause_ms = int(max_pause_ms)
        self.cooldown_seconds = float(cooldown_seconds)
        self.candidate_ttl_seconds = float(candidate_ttl_seconds)
        self._floor_id: Optional[str] = None
        self._floor_started_at: Optional[float] = None
        self._floor_used = False
        self._last_playback_at: Optional[float] = None
        self._pending: Optional[BackchannelDecision] = None

    def evaluate(
        self,
        now: float,
        *,
        floor_id: str,
        transcript: str,
        speech_active: bool,
        pause_ms: int,
        finalized: bool = False,
        quiet_requested: bool = False,
        speaking_practice: bool = False,
        sensitive: bool = False,
    ) -> BackchannelDecision:
        """Return a candidate only at a suitable pause in a sustained floor.

        A new ``floor_id`` marks a new user floor and starts its timer, even if
        the caller announces acoustic speech-start before any transcript exists.
        Re-evaluation with an unchanged partial is harmless;
        changed text invalidates an outstanding candidate before eligibility
        is reconsidered.
        """
        now = float(now)
        floor_id = str(floor_id)
        transcript = transcript.strip()
        key = self._transcript_key(transcript)

        if floor_id != self._floor_id:
            self._floor_id = floor_id
            # The caller may announce a floor at acoustic speech-start before
            # the first transcript partial exists. Start elapsed time there.
            self._floor_started_at = now
            self._floor_used = False
            self._pending = None

        pending = self._pending
        if pending is not None:
            if not self.is_valid(
                pending, now, floor_id=floor_id, transcript=transcript,
                voiced_now=speech_active, finalized=finalized,
                quiet_requested=quiet_requested or speaking_practice or bool(_QUIET_RE.search(transcript)),
                sensitive=sensitive or bool(_DISTRESS_RE.search(transcript)),
            ):
                self._pending = None
            else:
                return pending

        def reject(reason: str, trigger: str = "eligibility_gate") -> BackchannelDecision:
            return BackchannelDecision(
                False, None, reason, trigger, floor_id, now, now, key,
            )

        if not key:
            return reject("no_transcript")
        if quiet_requested or speaking_practice or _QUIET_RE.search(transcript):
            return reject("user_requested_quiet")
        if sensitive or _DISTRESS_RE.search(transcript):
            return reject("sensitive_or_distress_context")
        if finalized:
            return reject("turn_finalized")
        if self._looks_like_question(transcript):
            return reject("question_or_question_boundary")
        if speech_active:
            return reject("voiced_audio_active")
        if pause_ms < self.min_pause_ms:
            return reject("pause_too_short")
        if pause_ms > self.max_pause_ms:
            return reject("pause_too_long")
        if self._floor_started_at is None or now - self._floor_started_at < self.min_floor_seconds:
            return reject("floor_not_sustained")
        words = _WORD_RE.findall(transcript)
        if len(words) < self.min_words:
            return reject("too_few_words")
        if not self._has_continuation_signal(transcript):
            return reject("no_continuation_signal")
        if self._floor_used:
            return reject("floor_quota_used")
        if self._last_playback_at is not None and now - self._last_playback_at < self.cooldown_seconds:
            return reject("global_cooldown")

        decision = BackchannelDecision(
            True,
            "mhm",
            "sustained_floor_at_natural_pause",
            "sustained_floor+short_pause+unfinished_nonquestion_partial",
            floor_id,
            now,
            now + self.candidate_ttl_seconds,
            key,
        )
        self._pending = decision
        return decision

    def is_valid(
        self,
        decision: BackchannelDecision,
        now: float,
        *,
        floor_id: str,
        transcript: str,
        voiced_now: bool = False,
        finalized: bool = False,
        quiet_requested: bool = False,
        sensitive: bool = False,
    ) -> bool:
        """Revalidate immediately before playback; partial changes cancel it."""
        return bool(
            decision.emit
            and decision.cue == "mhm"
            and self._pending == decision
            and decision.floor_id == str(floor_id)
            and float(now) <= decision.expires_at
            and not voiced_now
            and not finalized
            and not quiet_requested
            and not sensitive
            and not _QUIET_RE.search(transcript)
            and not _DISTRESS_RE.search(transcript)
            and not self._looks_like_question(transcript)
            and decision.transcript_key == self._transcript_key(transcript)
        )

    def playback_started(self, decision: BackchannelDecision, now: float) -> bool:
        """Commit the per-floor quota and cooldown after audio really begins."""
        if self._pending != decision or not decision.emit:
            return False
        if float(now) > decision.expires_at:
            self._pending = None
            return False
        self._floor_used = True
        self._last_playback_at = float(now)
        self._pending = None
        return True

    def cancel(self, decision: Optional[BackchannelDecision] = None) -> bool:
        """Drop a pending cue (for cancellation or a playback failure)."""
        if self._pending is None or (decision is not None and self._pending != decision):
            return False
        self._pending = None
        return True

    @staticmethod
    def _transcript_key(text: str) -> str:
        return " ".join(text.casefold().split())

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        cleaned = text.strip()
        if _FINAL_MARK_RE.search(cleaned):
            terminal = _FINAL_MARK_RE.search(cleaned).group(0).lstrip()
            if terminal.startswith("?"):
                return True
            # A complete declarative sentence is a likely floor ending. Do not
            # create acknowledgements after it; continuation commas and
            # ellipses remain eligible below.
            if terminal.startswith((".", "!")):
                return True
        # A question mark anywhere is decisive. Without one, only a direct
        # question at the start or a conventional comma-delimited tag at the
        # end is enough evidence. Wh-auxiliary phrases inside a statement
        # ("what is changing, because …") must not be mistaken for questions.
        return "?" in cleaned or bool(_DIRECT_QUESTION_RE.search(cleaned) or _TAG_QUESTION_RE.search(cleaned))

    @staticmethod
    def _has_continuation_signal(text: str) -> bool:
        """Prefer a held/open clause or comma-marked continuation boundary."""
        cleaned = text.strip()
        if re.search(r"(?:,|…|\.\.\.)[\"'’”)]*\s*$", cleaned):
            return True
        if "," in cleaned and not _FINAL_MARK_RE.search(cleaned):
            return True
        return bool(_OPEN_END_RE.search(cleaned))


__all__ = ["BackchannelDecision", "ListenerBackchannelPolicy"]
