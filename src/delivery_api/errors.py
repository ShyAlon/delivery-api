"""Human-readable failures: what happened, likely cause, exact next action."""
from __future__ import annotations


class UserFacingError(Exception):
    def __init__(self, what: str, cause: str | None = None, next_action: str | None = None):
        self.what, self.cause, self.next_action = what, cause, next_action
        super().__init__(what)

    def render(self) -> str:
        parts = [f"Error: {self.what}"]
        if self.cause:
            parts.append(f"Likely cause: {self.cause}")
        if self.next_action:
            parts.append(f"Next: {self.next_action}")
        return "\n".join(parts)
