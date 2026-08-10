from lantai.memory.forgetting import apply_forgetting
from lantai.core import scheduler as scheduler_mod


def run_forgetting_once():
    apply_forgetting()
    scheduler_mod.record_run("forgetting")
