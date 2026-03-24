"""Gunicorn configuration"""
import multiprocessing

# Binding
bind = "0.0.0.0:8080"

# Worker configuration
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 5

# Logging
accesslog = "-"  # stdout
errorlog = "-"   # stderr
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "openclaw-provisioning"

# Graceful restart
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
