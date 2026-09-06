"""L3 事实总线骨架测试。"""

from __future__ import annotations

from hiveweave.services import fact_bus
from hiveweave.services.fact_bus import Fact, publish, recent_facts, reset_for_tests


def setup_function():
    reset_for_tests()


def test_publish_and_recent():
    publish("merge_landed", "hw/A447", {"branch": "hw/A447"})
    facts = recent_facts()
    assert len(facts) == 1
    assert facts[0].kind == "merge_landed"
    assert facts[0].subject == "hw/A447"


def test_subscribe_callback():
    received = []
    fn = lambda f: received.append(f)
    fact_bus.subscribe(fn)
    publish("task_closed", "task-1")
    assert len(received) == 1
    assert received[0].kind == "task_closed"


def test_subscriber_error_does_not_block():
    def bad(f):
        raise RuntimeError("boom")
    fact_bus.subscribe(bad)
    received = []
    fact_bus.subscribe(lambda f: received.append(f))
    publish("test_kind", "subj")
    assert len(received) == 1  # bad 的异常被吞，好的仍收到


def test_kind_filter():
    publish("merge_landed", "hw/A")
    publish("task_closed", "task-B")
    merge = recent_facts(kind="merge_landed")
    assert len(merge) == 1
    assert merge[0].subject == "hw/A"


def test_recent_cap():
    for i in range(250):
        publish("fill", f"s-{i}")
    assert len(recent_facts(limit=999)) == 200  # _MAX_RECENT
