from __future__ import annotations

from pytest import ExitCode, Session


def pytest_sessionfinish(session: Session, exitstatus: int | ExitCode) -> None:
    if session.config.option.collectonly and session.testscollected == 0:
        session.exitstatus = ExitCode.OK
