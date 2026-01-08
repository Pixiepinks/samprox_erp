web: WEB_CONCURRENCY=1 GUNICORN_CMD_ARGS="--timeout 180 --graceful-timeout 30 --workers 1 --threads 4 --worker-class gthread --max-requests 500 --max-requests-jitter 50" gunicorn app:app
