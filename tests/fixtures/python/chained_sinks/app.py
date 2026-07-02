def entry():
    sink_a()


def sink_a():
    sink_b()  # a sink node that itself reaches another sink


def sink_b():
    pass
