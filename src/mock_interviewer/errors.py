"""Typed application errors with safe, user-facing messages."""


class InterviewError(Exception):
    """Base for expected interview failures."""


class SessionNotFound(InterviewError):
    pass


class InvalidSessionTransition(InterviewError):
    pass


class ConcurrentSessionUpdate(InterviewError):
    pass


class ResumeValidationError(InterviewError, ValueError):
    pass


class ResumeParseError(InterviewError):
    pass


class ModelProviderError(InterviewError):
    def __init__(self):
        super().__init__("The interview model is temporarily unavailable. Please retry.")


class InvalidModelOutput(InterviewError):
    pass


class InvalidReport(InterviewError):
    pass
