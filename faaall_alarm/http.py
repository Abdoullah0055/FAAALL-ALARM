"""Transport JSON borné, sans redirection ni répétition implicite d'un POST."""

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ServiceError(Exception):
    def __init__(self, status: int | None = None, *, code: int | None = None,
                 reason: str = "réponse inattendue du service"):
        super().__init__("Service distant indisponible.")
        self.status = status
        self.code = code
        self.reason = reason


def numeric_error_code(value: object) -> int | None:
    if type(value) is int and 10000 <= value <= 99999:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{4}", value):
        return int(value)
    return None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, headers: dict[str, str], body: bytes | None = None) -> dict:
    request = Request(url, data=body, headers=headers)
    try:
        with build_opener(NoRedirect).open(request, timeout=8) as response:
            result = json.load(response)
    except HTTPError as error:
        code = None
        try:
            with error:
                payload = json.loads(error.read(8192))
            if isinstance(payload, dict):
                code = numeric_error_code(payload.get("code"))
        except (HTTPException, OSError, ValueError):
            pass
        raise ServiceError(error.code, code=code) from None
    except (HTTPException, OSError):
        raise ServiceError(reason="connexion impossible ou délai dépassé") from None
    except ValueError:
        raise ServiceError() from None
    if not isinstance(result, dict):
        raise ServiceError()
    return result
