"""Explicit label-only exercise, using real Groq inference and the same policy core."""
from pathlib import Path
from .groq_provider import GroqProposer, ProviderError


class LabelProposer(GroqProposer):
    prompt_version = "labels-v1"
    system = (Path(__file__).parent / "prompts" / "labels-v1.txt").read_text()

    def propose(self, email):
        p = super().propose(email)
        # A task-specific prompt cannot grant the exercise other mail actions.
        if p.action not in {"label", "none"} or p.text or p.recipient:
            raise ProviderError("Label review mode permits only a category label or no mail action")
        return p
