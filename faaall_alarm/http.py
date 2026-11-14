"""Transport JSON borné, sans redirection ni répétition implicite d'un POST."""

import json
import re
import socket
import ssl
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ServiceError(Exception):
    def __init__(self, status: int | None = None, *, code: int | None = None,
                 reason: str = "réponse inattendue du service",
                 response_format: str | None = None, clue: str | None = None):
        super().__init__("Service distant indisponible.")
        self.status = status
        self.code = code
        self.reason = reason
        self.response_format = response_format
        self.clue = clue


def numeric_error_code(value: object) -> int | None:
    if type(value) is int and 10000 <= value <= 99999:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{4}", value):
        return int(value)
    return None


def _twilio_error_clue(message: object) -> str | None:
    """Classer le texte côté client sans en conserver un seul fragment."""
    if not isinstance(message, str):
        return None
    text = message.casefold()
    if "template" in text and any(word in text for word in ("trial", "predefined", "pre-defined")):
        return "Le refus mentionne un modèle SMS imposé par l’essai Twilio."
    if re.search(r"unverified|not (?:a )?verified|not been verified", text):
        return "Le refus mentionne un numéro non vérifié."
    if "geo permission" in text or "permissions disabled for" in text:
        return "Le refus mentionne les permissions du pays destinataire."
    if "sms-capable" in text or "message-capable" in text:
        return "Le refus mentionne la capacité SMS du numéro expéditeur."
    if "trial account" in text:
        return "Le refus mentionne une restriction du compte d’essai."
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
        clue = None
        response_format = "illisible"
        try:
            with error:
                raw = error.read(8193)
            response_format = "vide" if not raw else "non JSON"
            payload = json.loads(raw) if len(raw) <= 8192 and raw else None
            if len(raw) > 8192:
                response_format = "trop volumineuse"
            if isinstance(payload, dict):
                code = numeric_error_code(payload.get("code"))
                if urlsplit(url).hostname == "api.twilio.com":
                    info = payload.get("more_info")
                    if code is None and isinstance(info, str):
                        match = re.fullmatch(r"https://www\.twilio\.com/(?:docs/)?(?:api/)?errors/([1-9][0-9]{4})", info)
                        if match:
                            code = int(match[1])
                    clue = _twilio_error_clue(payload.get("message"))
                response_format = ("JSON avec code" if code is not None
                                   else "JSON sans code exploitable")
            elif raw and len(raw) <= 8192:
                response_format = "JSON au format inattendu"
        except (HTTPException, OSError, ValueError):
            pass
        raise ServiceError(error.code, code=code, response_format=response_format, clue=clue) from None
    except (HTTPException, OSError) as error:
        cause = error.reason if isinstance(error, URLError) else error
        if isinstance(cause, socket.gaierror):
            reason = "résolution DNS impossible"
        elif isinstance(cause, ssl.SSLError):
            reason = "connexion TLS impossible"
        elif isinstance(cause, TimeoutError):
            reason = "délai réseau dépassé"
        else:
            reason = "connexion interrompue ou impossible"
        raise ServiceError(reason=reason) from None
    except ValueError:
        raise ServiceError() from None
    if not isinstance(result, dict):
        raise ServiceError()
    return result
