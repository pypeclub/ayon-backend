from typing import Any

from ayon_server.logging import logger


class AyonException(Exception):
    """Base class for all Ayon server exceptions."""

    detail: str = "Error"
    status: int = 500
    extra: dict[str, Any]

    def __init__(
        self,
        detail: str | None = None,
        log: bool | str = False,
        **kwargs,
    ) -> None:
        if detail is not None:
            self.detail = detail
        self.extra = kwargs
        if log is True:
            logger.error(f"EXCEPTION: {self.status} {self.detail}")
        elif isinstance(log, str):
            logger.error(f"EXCEPTION: {self.status} {log}")

        super().__init__(self.detail)


class BadRequestException(AyonException):
    """Raised when the request is malformed or missing required fields."""

    detail: str = "Bad request"
    status = 400


class UnauthorizedException(AyonException):
    """Raised when a user is not authorized.

    And tries to access a resource without the proper credentials.
    """

    detail: str = "Not logged in"
    status: int = 401


class ForbiddenException(AyonException):
    """Raised when a user is not permitted access to the resource.

    despite providing authentication such as insufficient
    permissions of the authenticated account.
    """

    detail: str = "Forbidden"
    status: int = 403


class NotFoundException(AyonException):
    """Exception raised when a resource is not found."""

    detail: str = "Not found"
    status: int = 404


class InvalidSettingsException(AyonException):
    """Exception raised when addon settings are invalid."""

    detail: str = "Invalid settings"
    status: int = 500


class ConflictException(AyonException):
    """Exception raised when a resource already exists."""

    detail: str = "Conflict"
    status: int = 409


class ConstraintViolationException(AyonException):
    """Exception raised when a DB constraint is violated."""

    detail: str = "Constraint violation"
    status: int = 409


class UnsupportedMediaException(AyonException):
    """Exception raised when a provided media is not supported."""

    detail: str = "Unsupported media"
    status: int = 415


class RangeNotSatisfiableException(AyonException):
    """Exception raised when a Range Request is not satisfiable."""

    detail: str = "Range Not Satisfiable"
    status: int = 416


class LowPasswordComplexityException(AyonException):
    """Exception raised when a new password doesn't meet the required complexity."""

    detail: str = "Password does not meet complexity requirements"
    status: int = 400


class NothingToDoException(AyonException):
    """Exception raised when there's nothing to do"""

    detail: str = "Nothing to do"
    status: int = 404


class NotImplementedException(AyonException):
    """Exception raised when a feature is not implemented."""

    detail: str = "Not implemented"
    status: int = 501


class ServiceUnavailableException(AyonException):
    """Exception raised when a service is unavailable.

    Request should be retried later.
    """

    detail: str = "Service unavailable"
    status: int = 503
