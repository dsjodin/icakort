"""En enda bakgrundsjobbplats.

BankID-pollningen blockerar upp till tre minuter och en full synk tar
minuter -- båda måste ligga utanför request-cykeln. En plats räcker och är
dessutom rätt modell: det finns en användare, och två samtidiga synkar mot
samma databas vill vi inte ha.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

MAX_LOG_LINES = 200


class JobBusy(RuntimeError):
    """Ett jobb kör redan."""


@dataclass
class Job:
    kind: str                              # "login" | "sync"
    state: str = "running"                 # "running" | "done" | "error"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    log: list[str] = field(default_factory=list)
    qr_payload: str | None = None
    result: dict | None = None
    error: str | None = None

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": list(self.log),
            "has_qr": self.qr_payload is not None,
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._thread: threading.Thread | None = None

    def current(self) -> Job | None:
        with self._lock:
            return self._job

    def start(self, kind: str, work: Callable[[Job], dict | None]) -> Job:
        """Starta ett jobb. Avvisar om ett redan kör."""
        with self._lock:
            if self._job is not None and self._job.is_running:
                raise JobBusy(f"Ett jobb kör redan: {self._job.kind}")
            job = Job(kind=kind)
            self._job = job

        def run() -> None:
            try:
                job.result = work(job)
                job.state = "done"
            except Exception as exc:  # noqa: BLE001 - felet ska nå gränssnittet
                job.error = str(exc) or exc.__class__.__name__
                job.state = "error"
                job.log.append(f"Fel: {job.error}")
                traceback.print_exc()
            finally:
                job.finished_at = time.time()
                # QR:en är meningslös när flödet är slut och ska inte ligga kvar.
                job.qr_payload = None

        thread = threading.Thread(target=run, name=f"icakort-{kind}", daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()
        return job

    def join(self, timeout: float | None = None) -> None:
        """Vänta in jobbet. Finns för testerna."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


def log(job: Job, message: str) -> None:
    """Lägg till en loggrad, med tak så en lång synk inte äter minne."""
    job.log.append(message)
    if len(job.log) > MAX_LOG_LINES:
        del job.log[: len(job.log) - MAX_LOG_LINES]


# Processens jobbplats. Webblagret har en användare och en app-instans.
runner = JobRunner()
