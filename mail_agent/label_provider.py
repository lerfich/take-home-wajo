"""Explicit label-only exercise, using real Groq inference and the same policy core."""
from dataclasses import replace
from pathlib import Path
from .groq_provider import GroqProposer, ProviderError


def _validate(proposal):
    # A task-specific prompt cannot grant the exercise other mail actions.
    if proposal.action not in {"label", "none"} or proposal.text or proposal.recipient:
        raise ProviderError("Label review mode permits only a category label or no mail action")
    # Re-reviewing a category must not duplicate or alter the independent
    # calendar proposal created by the original triage pass.
    return replace(
        proposal, event_change="none", event_kind="none", event_semantic_kind="",
        event_title="", event_original_text="", event_start="", event_end="",
        event_all_day=False, event_timezone="", event_confidence="none",
        event_ambiguity_reason="", event_evidence="")


class LabelProposer(GroqProposer):
    prompt_version = "labels-v1"
    system = (Path(__file__).parent / "prompts" / "labels-v1.txt").read_text()

    def propose(self, email):
        return _validate(super().propose(email))


class LabelReviewProposer:
    """Apply the label-only prompt and guard to any selected model adapter."""

    def __init__(self, provider):
        self.provider = provider
        self.provider.prompt_version = LabelProposer.prompt_version
        self.provider.system = LabelProposer.system

    @property
    def calls(self):
        return self.provider.calls

    def propose(self, email):
        return _validate(self.provider.propose(email))

    def propose_with_context(self, email, trusted_context):
        return _validate(self.provider.propose_with_context(email, trusted_context))
