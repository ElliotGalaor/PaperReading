import os
import subprocess
import sys
import threading
import time
from itertools import pairwise
from pathlib import Path

import httpx
import pytest

from papersim.config import settings
from papersim.ingest import arxiv

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/2101.00001v1</id><title>T</title><summary>A</summary></entry>
</feed>"""

DELAY = 0.3


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Cache et verrou dans un dossier temporaire, délai court."""
    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    monkeypatch.setattr(arxiv, "_gate", arxiv.ArxivGate(tmp_path / "arxiv.lock", delay=DELAY))
    return tmp_path


def _mock_client(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _assert_spaced(intervals):
    """Pas de chevauchement, et au moins DELAY entre la fin d'une requête et le début de la suivante."""
    intervals = sorted(intervals)
    for (_, e1), (s2, _) in pairwise(intervals):
        assert s2 >= e1 + DELAY - 0.02, (e1, s2)


def test_gate_serializes_threads():
    intervals, lock = [], threading.Lock()

    def worker():
        with arxiv.get_gate().slot():
            start = time.time()
            time.sleep(0.05)  # durée de la « requête »
            end = time.time()
        with lock:
            intervals.append((start, end))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(intervals) == 4
    _assert_spaced(intervals)


CHILD = """
import sys, time
from pathlib import Path
from papersim.ingest.arxiv import ArxivGate
gate = ArxivGate(Path(sys.argv[1]), delay=float(sys.argv[2]))
for _ in range(2):
    with gate.slot():
        start = time.time(); time.sleep(0.05); end = time.time()
    print(start, end, flush=True)
"""


def test_gate_serializes_processes(isolated):
    lock = isolated / "shared.lock"
    env = {**os.environ, "PYTHONPATH": str(Path(arxiv.__file__).parents[2])}
    procs = [
        subprocess.Popen([sys.executable, "-c", CHILD, str(lock), str(DELAY)],
                         stdout=subprocess.PIPE, text=True, env=env)
        for _ in range(2)
    ]
    intervals = []
    for p in procs:
        out, _ = p.communicate(timeout=60)
        assert p.returncode == 0
        intervals += [tuple(map(float, line.split())) for line in out.splitlines()]
    assert len(intervals) == 4
    _assert_spaced(intervals)


def test_retry_respects_retry_after(monkeypatch):
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "7"})
        return httpx.Response(200, text=FEED)

    real_sleep = time.sleep
    monkeypatch.setattr(arxiv.time, "sleep", lambda s: sleeps.append(s) if s >= 1 else real_sleep(s))
    monkeypatch.setattr(arxiv, "_client", _mock_client(handler))
    papers = arxiv.fetch_arxiv_ids(["2101.00001"])
    assert len(papers) == 1 and len(calls) == 2
    assert 7 in sleeps


def test_retries_are_bounded(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    monkeypatch.setattr(arxiv.time, "sleep", lambda s: None)
    monkeypatch.setattr(arxiv, "_client", _mock_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        arxiv.fetch_arxiv_ids(["2101.00001"])
    assert len(calls) == arxiv.MAX_RETRIES + 1


def test_same_query_is_cached_for_the_day(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=FEED)

    monkeypatch.setattr(arxiv, "_client", _mock_client(handler))
    arxiv.fetch_arxiv_ids(["2101.00001"])
    arxiv.fetch_arxiv_ids(["2101.00001v2"])  # même identifiant normalisé
    assert len(calls) == 1


def test_cache_expires_next_day(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=FEED)

    monkeypatch.setattr(arxiv, "_client", _mock_client(handler))
    arxiv.fetch_arxiv_ids(["2101.00001"])
    for f in (settings.cache_dir / "arxiv").iterdir():
        yesterday = time.time() - 86400
        os.utime(f, (yesterday, yesterday))
    arxiv.fetch_arxiv_ids(["2101.00001"])
    assert len(calls) == 2


def test_errors_are_not_cached(monkeypatch):
    error_feed = FEED.replace("http://arxiv.org/abs/2101.00001v1", "http://arxiv.org/api/errors#x")
    monkeypatch.setattr(arxiv, "_client", _mock_client(lambda r: httpx.Response(200, text=error_feed)))
    with pytest.raises(ValueError):
        arxiv.fetch_arxiv_ids(["foo"])
    assert not (settings.cache_dir / "arxiv").exists()


def test_max_results_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "arxiv_max_results", 250)
    assert arxiv.clamp_max_results(10_000) == 250
    monkeypatch.setattr(settings, "arxiv_max_results", 99_999)
    assert arxiv.clamp_max_results(99_999) == arxiv.HARD_MAX_RESULTS
    assert arxiv.clamp_max_results(50) == 50
