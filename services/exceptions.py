class ServiceError(Exception):
    """Base exception for all service-layer errors."""


class NotFoundError(ServiceError):
    """Resource not found."""


class AlreadyExistsError(ServiceError):
    """Resource already exists."""


class ConflictError(ServiceError):
    """Request conflicts with current resource state (HTTP 409). Used when
    the conflict is *stateful* rather than uniqueness-driven (e.g. the user
    has an active attempt for a different exam than the one requested)."""


class ValidationError(ServiceError):
    """Input failed validation."""


class PermissionDeniedError(ServiceError):
    """User lacks permission for this action."""


class InvalidCredentialsError(ServiceError):
    """Login credentials are invalid."""


class InsufficientCreditsError(ServiceError):
    """User does not have enough credits."""
