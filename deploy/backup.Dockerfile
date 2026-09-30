FROM postgres:16-alpine

RUN apk add --no-cache aws-cli

COPY deploy/backup-worker.sh /usr/local/bin/vesper-backup-worker
RUN chmod 0755 /usr/local/bin/vesper-backup-worker

ENTRYPOINT ["/usr/local/bin/vesper-backup-worker"]
