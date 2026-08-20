FROM nginx:1.27-alpine
COPY deploy/nginx/nginx-http.conf /opt/vesper/nginx-http.conf
COPY deploy/nginx/nginx-https.conf /opt/vesper/nginx-https.conf
COPY deploy/nginx/entrypoint.sh /opt/vesper/entrypoint.sh
RUN chmod +x /opt/vesper/entrypoint.sh
ENTRYPOINT ["/opt/vesper/entrypoint.sh"]
