"""Plain-text progress that works in terminals, Docker output and saved logs."""
import threading
import time


def say(message):
    print(message, flush=True)


class Progress:
    def __init__(self, label, fraction=None, interval=5):
        self.label = label
        self.fraction = fraction
        self.interval = interval
        self.stop = threading.Event()
        self.outcome = 'done'

    def __enter__(self):
        self.start = time.monotonic()
        say(f'▶ {self.label}')
        self.thread = threading.Thread(target=self.watch, daemon=True)
        self.thread.start()
        return self

    def watch(self):
        while not self.stop.wait(self.interval):
            elapsed = time.monotonic() - self.start
            extra = 'working; remaining time unknown'
            try:
                fraction = self.fraction() if self.fraction else None
                if fraction is not None and 0 < fraction < 1:
                    eta = elapsed * (1 - fraction) / fraction
                    extra = f'{fraction:.0%} · about {eta:.0f}s remaining'
                elif fraction is not None and fraction >= 1:
                    extra = 'finalizing'
            except (OSError, ValueError):
                pass
            say(f'  {self.label} · {elapsed:.0f}s elapsed · {extra}')

    def __exit__(self, kind, value, traceback):
        self.stop.set()
        self.thread.join()
        status = 'failed' if kind else self.outcome
        say(f'  {self.label}: {status} ({time.monotonic() - self.start:.1f}s)')
