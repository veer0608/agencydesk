from fastapi import HTTPException, status


class NotFound(HTTPException):
    """Used for 'does not exist' *and* 'exists but is not yours'.

    Returning 403 for the second case would confirm that an id is real, turning
    any endpoint into an oracle for enumerating another tenant's data. Rows the
    caller cannot see simply do not exist as far as the API is concerned -- which
    is also literally true, because row-level security filtered them out before
    the query returned.
    """

    def __init__(self, what: str = "Resource") -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


class Forbidden(HTTPException):
    """Only for actions on rows the caller can legitimately see."""

    def __init__(self, detail: str = "Not permitted") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class BadRequest(HTTPException):
    def __init__(self, detail: str = "Invalid request") -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class Unauthorized(HTTPException):
    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class Conflict(HTTPException):
    def __init__(self, detail: str = "Conflict") -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)
