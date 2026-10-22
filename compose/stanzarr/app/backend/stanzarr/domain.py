"""Errors the API answers with their sentence as is: 404, 409, 422."""


class NotFound(LookupError):
    """The message is shown as is."""


class Conflict(ValueError):
    """The message is shown as is."""


class Invalid(ValueError):
    """The message is shown as is."""
