import pytest

from pipeline import procs


@pytest.fixture(autouse=True)
def _allow_subprocesses():
    """A server shutting down in one test must not block ffmpeg in the next."""
    procs.reset()
    yield
    procs.reset()
