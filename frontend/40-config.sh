#!/bin/sh
# Écrit l'URL de l'API au démarrage du conteneur :
# la même image fonctionne en local et sur Azure.
echo "window.API_URL='${API_URL:-http://localhost:8000}';" > /usr/share/nginx/html/config.js
