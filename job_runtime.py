"""Durable job metadata and cancellable, isolated local provider requests."""
import json
import multiprocessing
import threading
import time
from provider import Gemini, ProviderError

TERMINAL = {'done', 'error', 'cancelled', 'interrupted'}

class JobCancelled(ValueError):
    pass

class JobStore:
    def __init__(self, connect):
        self.connect = connect

    def initialize(self, recover=False):
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,body TEXT)')
            if recover:
                for row in c.execute('SELECT id,body FROM jobs').fetchall():
                    job = json.loads(row['body'])
                    if job['status'] not in TERMINAL:
                        job.update(status='interrupted', message='App restarted; completed saved outputs are preserved.', finished_at=time.time(), updated_at=time.time())
                        for stage in job.get('stages', []):
                            if stage['status'] == 'running':
                                stage.update(status='interrupted', finished_at=time.time())
                        c.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job), row['id']))

    def write(self, job):
        self.initialize()
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))

    def all(self):
        self.initialize()
        with self.connect() as c:
            return sorted((json.loads(r['body']) for r in c.execute('SELECT body FROM jobs')), key=lambda j:j.get('created_at', 0), reverse=True)

    def get(self, jid):
        return next((j for j in self.all() if j['id'] == jid), None)

class DurableJob(dict):
    def __init__(self, store, **values):
        self.store = store
        self.lock = threading.RLock()
        super().__init__(values)
        self.store.write(self)

    def update(self, *args, **kwargs):
        with self.lock:
            super().update(*args, **kwargs)
            super().__setitem__('updated_at', time.time())
            self.store.write(self)

    def __setitem__(self, key, value):
        self.update({key:value})

class Runtime:
    def __init__(self):
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.RLock()
        self.process = None
        self.interrupted = False

    def check(self):
        if self.cancelled.is_set():
            raise JobCancelled('Stopped by you. Completed saved outputs are preserved; an in-flight provider charge may still apply.')

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            if self.process is not None and self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=1)


def provider_worker(pipe):
    """Credentials arrive through an anonymous pipe, never process arguments/files."""
    try:
        key, model, prompt, options = pipe.recv()
        raw = Gemini(key, model).generate(prompt, **options)
        pipe.send(('ok', raw))
    except ProviderError as error:
        pipe.send(('provider_error', error.diagnostic()))
    except Exception:
        pipe.send(('error', 'Could not complete the Gemini request. Check your connection and retry.'))
    finally:
        pipe.close()


def isolated_generate(key, model, prompt, options, runtime, worker=provider_worker):
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=worker, args=(child,), daemon=True)
    try:
        with runtime.lock:
            runtime.check()
            process.start()
            runtime.process = process
        child.close()
        parent.send((key, model, prompt, options))
        while True:
            runtime.check()
            if parent.poll(.05):
                try:
                    kind, result = parent.recv()
                except EOFError:
                    runtime.check()
                    raise ValueError('Local model worker exited before returning a response.') from None
                runtime.check()
                if kind == 'ok':
                    return result
                if kind == 'provider_error':
                    raise ProviderError(result['http_status'], result['provider_status'], result['message'], result['retry_after'], result['request_id'])
                raise ValueError(result)
            if not process.is_alive():
                runtime.check()
                raise ValueError('Local model worker exited before returning a response.')
    except (OSError, EOFError):
        # Termination can reset the pipe during either send or receive. Preserve
        # explicit cancellation rather than misreporting it as a provider error.
        runtime.check()
        raise ValueError('Local model worker exited before returning a response.') from None
    finally:
        parent.close()
        child.close()
        with runtime.lock:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=1)
            runtime.process = None
