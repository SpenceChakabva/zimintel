# gunicorn.conf.py — ZimIntel SSE-optimised config
# ──────────────────────────────────────────────────
# SSE (Server-Sent Events) requires special gunicorn settings:
#
#   1. worker_class = 'gthread'   — threaded workers; each SSE connection gets
#                                   its own thread so it doesn't block others.
#                                   gevent workers can work too but require
#                                   all sleeps to use gevent.sleep().
#
#   2. timeout = 0                — MOST CRITICAL. Default timeout (30s) kills
#                                   long-lived SSE connections. 0 = no timeout.
#
#   3. keepalive = 65             — HTTP keep-alive; keeps the TCP connection
#                                   open between SSE events.
#
#   4. worker_connections = ...   — For gthread, controls max simultaneous
#                                   threads per worker.

import multiprocessing

# ── Workers ────────────────────────────────────────────────────────────────
# gthread: best for SSE — true OS threads, no monkey-patching required
worker_class = 'gthread'

# 2 workers * (2*CPU+1) threads = good concurrency for mixed SSE + REST traffic
workers = 2
threads = (multiprocessing.cpu_count() * 2) + 1

# ── Timeouts — CRITICAL FOR SSE ────────────────────────────────────────────
timeout = 0          # Never time out workers — SSE streams live forever
graceful_timeout = 30
keepalive = 65       # Slightly above typical 60s proxy keepalive timeouts

# ── Binding ────────────────────────────────────────────────────────────────
import os
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ── Buffering — disable for SSE ────────────────────────────────────────────
# Ensures each 'yield' in the generator is flushed immediately to the client
# rather than being buffered by gunicorn until the response ends.
sendfile = False

# ── Logging ────────────────────────────────────────────────────────────────
accesslog = '-'
errorlog = '-'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)ss'

# ── Process naming ─────────────────────────────────────────────────────────
proc_name = 'zimintel-api'
