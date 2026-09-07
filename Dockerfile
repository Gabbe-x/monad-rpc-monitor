FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY monad_rpc_monitor ./monad_rpc_monitor
RUN pip install --no-cache-dir . && mkdir -p /data

ENV MONAD_RPC_MONITOR_DB=/data/monad-rpc-monitor.db
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

ENTRYPOINT ["monad-rpc-monitor"]
CMD ["serve", "--listen", "0.0.0.0:8080", "--db", "/data/monad-rpc-monitor.db"]
