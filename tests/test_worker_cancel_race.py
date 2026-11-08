"""A cancelled worker can close its pipe between process start and send."""
from types import SimpleNamespace
import pytest
import job_runtime


def test_pipe_reset_after_cancellation_is_reported_as_cancelled(monkeypatch):
    runtime=job_runtime.Runtime()

    class Pipe:
        def send(self, value):
            runtime.cancelled.set()
            raise ConnectionResetError('Worker stopped while the parent was sending')
        def close(self):pass

    class Process:
        pid=1
        def start(self):pass
        def is_alive(self):return False
        def join(self, timeout=None):pass

    context=SimpleNamespace(Pipe=lambda:(Pipe(),Pipe()),Process=lambda **kwargs:Process())
    monkeypatch.setattr(job_runtime.multiprocessing,'get_context',lambda mode:context)
    with pytest.raises(job_runtime.JobCancelled):
        job_runtime.isolated_generate('synthetic-key','synthetic-model','synthetic-prompt',{},runtime)
