"""Regression: the RACI-proposal task must survive to completion.

Bug: DecisionService scheduled propose_raci_to_submitter via
asyncio.get_event_loop().create_task() without keeping a reference. The event
loop only holds a weak reference to such tasks, so it could be garbage-collected
before running — the submitter then got no RACI step. The fix routes scheduling
through DecisionService._schedule_bg, which uses the PTB Application task manager
when present, or holds a strong reference otherwise.
"""

import asyncio
import gc

import pytest

from app.services.decision_service import DecisionService, _bg_tasks


def _make_svc(application):
    # Bypass __init__ (which builds a ClaudeService needing env/config).
    svc = DecisionService.__new__(DecisionService)
    svc.session = None
    svc.application = application
    return svc


@pytest.mark.asyncio
async def test_schedule_bg_uses_application_when_present():
    scheduled = []

    class FakeApp:
        def create_task(self, coro):
            task = asyncio.ensure_future(coro)
            scheduled.append(task)
            return task

    svc = _make_svc(FakeApp())

    ran = asyncio.Event()

    async def work():
        ran.set()

    svc._schedule_bg(work())
    assert len(scheduled) == 1  # went through the tracked Application path
    await asyncio.wait_for(ran.wait(), timeout=1)


@pytest.mark.asyncio
async def test_schedule_bg_survives_gc_without_application():
    """Without an Application, the task must not be GC'd before it runs."""
    svc = _make_svc(None)

    ran = asyncio.Event()

    async def work():
        # yield control so the scheduler could drop a weakly-held task
        await asyncio.sleep(0.01)
        ran.set()

    svc._schedule_bg(work())
    # A strong reference must be retained by the module.
    assert len(_bg_tasks) >= 1
    gc.collect()  # would reap a weakly-referenced task; strong ref keeps it alive
    await asyncio.wait_for(ran.wait(), timeout=1)
    # Done-callback clears the strong reference.
    await asyncio.sleep(0)
    assert all(not t.done() or t not in _bg_tasks for t in list(_bg_tasks))
