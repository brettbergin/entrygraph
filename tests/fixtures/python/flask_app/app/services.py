"""Service layer; run_report reaches subprocess.run via three hops and a cycle."""

import subprocess as sub

from app.db import find_user

REPORT_TIMEOUT = 30


class ReportRunner:
    """Executes report generation shell pipelines."""

    def __init__(self, name):
        self.name = name

    def start(self):
        return self.render_and_execute(0)

    def render_and_execute(self, depth):
        if depth > 3:
            return self.start()  # deliberate cycle for reachability tests
        return sub.run(["generate-report", self.name], timeout=REPORT_TIMEOUT)


def run_report(name):
    runner = ReportRunner(name)
    return runner.start()


def lookup_user(user_id):
    return find_user(user_id)
