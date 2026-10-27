FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

# Utilisateur non-root
USER 10001
EXPOSE 8000
# Un seul processus par conteneur : on scale en ajoutant des réplicas.
# Pas de log d'accès : chaque requête est déjà tracée dans Application Insights.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
