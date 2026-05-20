class ServiceError(Exception):
    """Base exception for all service-layer errors."""


class NotFoundError(ServiceError):
    """Resource not found."""


class AlreadyExistsError(ServiceError):
    """Resource already exists."""


class ValidationError(ServiceError):
    """Input failed validation."""


class PermissionDeniedError(ServiceError):
    """User lacks permission for this action."""


class InvalidCredentialsError(ServiceError):
    """Login credentials are invalid."""


class InsufficientCreditsError(ServiceError):
    """User does not have enough credits."""
