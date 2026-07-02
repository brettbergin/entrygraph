import subprocess


class Base:
    def run(self):
        pass


class HandlerA(Base):
    def run(self):
        pass


class HandlerB(Base):
    def run(self):
        pass


def source_fn(obj):
    subprocess.run(["ls"])  # direct command_exec sink: source_fn -> subprocess.run
    obj.run()  # unknown receiver -> CHA fan-out (excluded by default)
