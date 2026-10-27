FROM nginxinc/nginx-unprivileged:1.27-alpine

USER root
COPY index.html /usr/share/nginx/html/
COPY 40-config.sh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/40-config.sh \
 && chown -R 101:0 /usr/share/nginx/html

# Utilisateur non-root, port 8080
USER 101
EXPOSE 8080
