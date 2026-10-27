# kata-sg : application conteneurisée sur Azure, avec observabilité

Application web (frontend nginx + API FastAPI + PostgreSQL) déployée sur Azure Container Apps
par un pipeline GitHub Actions, avec observabilité Application Insights.

## Plan
1. Code applicatif (généré par IA, relu)
2. CI : tests
3. Connexion Azure par OIDC (aucun secret Azure stocké)
4. Registre d'images, environnement Container Apps, Application Insights, PostgreSQL
5. Déploiement du backend et du front
6. Test de charge
7. Scénarios de panne
8. Destruction de l'infra

