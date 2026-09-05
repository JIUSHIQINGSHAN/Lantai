from lantai.core import scheduler as scheduler_mod
from lantai.memory.forgetting import apply_forgetting


def run_forgetting_once():
    apply_forgetting()
    scheduler_mod.record_run("forgetting")
