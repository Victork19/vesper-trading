FROM certbot/certbot:latest
COPY deploy/certbot/entrypoint.sh /opt/vesper/entrypoint.sh
RUN chmod 755 /opt/vesper/entrypoint.sh
ENTRYPOINT ["/opt/vesper/entrypoint.sh"]
