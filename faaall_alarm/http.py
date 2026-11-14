"""Transport JSON borné, sans redirection ni répétition implicite d'un POST."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ServiceError(Exception):
    def __init__(self, status: int | None = None):
        super().__init__("Service distant indisponible.")
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, headers: dict[str, str], body: bytes | None = None) -> dict:
    request = Request(url, data=body, headers=headers)
    try:
        with build_opener(NoRedirect).open(request, timeout=8) as response:
            result = json.load(response)
    except HTTPError as error:
        error.close()
        raise ServiceError(error.code) from None
    except (URLError, OSError, ValueError) as error:
        raise ServiceError() from None
    if not isinstance(result, dict):
        raise ServiceError()
    return result
