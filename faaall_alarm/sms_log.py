"""Journal local des diagnostics SMS construits par l’application."""

from datetime import datetime
from pathlib import Path
from threading import Lock

SMS_LOG_PATH = Path(__file__).resolve().parent.parent / "sms-logs.txt"
_LOCK = Lock()


def write_sms_log(path: Path, source: str, event: str) -> None:
    """Écrire uniquement des libellés contrôlés, jamais une réponse brute."""
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    try:
        with _LOCK, path.open("a", encoding="utf-8") as log:
            log.write(f"{timestamp} | {source} | {event}\n")
    except OSError:
        # La panne du journal ne doit jamais empêcher l’envoi de l’alerte.
        pass
