"""Exit codes and the error that carries a hint for the agent."""

BAD_INPUT = 2
AUTH = 3
NOT_FOUND = 4
APPROVAL = 5
REMOTE = 6


class GcalError(Exception):
    def __init__(self, code, message, hint=None, candidates=None):
        super().__init__(message)
        self.code, self.message, self.hint, self.candidates = code, message, hint, candidates

    def payload(self):
        payload = {"error": self.message}
        if self.hint:
            payload["hint"] = self.hint
        if self.candidates:
            payload["candidates"] = self.candidates
        return payload


def bad_input(message, hint=None):
    return GcalError(BAD_INPUT, message, hint)


def approval(message, flag):
    return GcalError(APPROVAL, message, f"get approval from the user for this action, then add {flag}")
