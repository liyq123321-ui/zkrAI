"""Task-local progress reporting for cross-cutting Agent observability."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import logging


ProgressReporter = Callable[[str, str], None]
_reporter: ContextVar[ProgressReporter | None] = ContextVar(
    "agent_progress_reporter", default=None
)
logger = logging.getLogger(__name__)


@contextmanager
def bind_agent_progress(reporter: ProgressReporter) -> Iterator[None]:
    """Bind one reporter to the current asynchronous command task."""

    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def report_agent_progress(stage: str, message: str) -> None:
    """Report sanitized progress without allowing telemetry to break Agent work."""

    reporter = _reporter.get()
    if reporter is None:
        return
    try:
        reporter(stage, message)
    except Exception:
        logger.exception("Agent progress reporter failed")
