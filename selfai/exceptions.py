"""Custom exceptions for SelfAI with contextual error information."""


class SelfAIException(Exception):
    """Base exception for all SelfAI errors."""

    def __init__(self, message: str, context: dict = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self):
        if self.context:
            context_str = ', '.join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} (Context: {context_str})"
        return self.message


class PlanValidationError(SelfAIException):
    """Raised when plan validation fails."""
    pass


class InvalidStatusTransitionError(SelfAIException):
    """Raised when attempting invalid status transition."""
    pass


class WorktreeConflictError(SelfAIException):
    """Raised when file conflicts detected between improvements."""
    pass


class ResourceLimitError(SelfAIException):
    """Raised when resource limits exceeded."""
    pass


class GitOperationError(SelfAIException):
    """Raised when git operations fail."""
    pass


class ValidationError(SelfAIException):
    """Raised when input validation fails."""
    pass


class DiscoveryError(SelfAIException):
    """Raised when discovery fails."""
    pass


class DiscoveryTimeoutError(DiscoveryError):
    """Raised when discovery scan times out."""
    pass


class DiscoveryParseError(DiscoveryError):
    """Raised when discovery output cannot be parsed."""
    pass
