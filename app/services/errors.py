"""Domain errors. Routes turn these into flash messages (web) or JSON errors (API)."""


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class OutOfStock(DomainError):
    status_code = 409
    code = "out_of_stock"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"
