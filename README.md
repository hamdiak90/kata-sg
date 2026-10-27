# kata-app

Petite application web (un front nginx et une API FastAPI branchée sur PostgreSQL) déployée sur Azure Container Apps.

L'app en elle-même n'est pas le sujet. Ce repo sert surtout à montrer la partie qui m'intéresse : un déploiement reproductible de bout en bout, une observabilité exploitable, et ce qu'on apprend quand on charge l'app et qu'on casse des choses volontairement.

## Architecture

```
Navigateur ──> web (nginx, Container Apps, 1-3 replicas)
                 │
                 └──> api (FastAPI, Container Apps, 2-6 replicas, autoscaling HTTP)
                        ├──> PostgreSQL Flexible Server (B1ms, TLS obligatoire)
                        └──> Application Insights ──> Log Analytics (plafond 1 Go/jour)

GitHub Actions ──(OIDC, aucun secret Azure stocké)──> Azure
     └──> ACR (images api et web, taguées avec le SHA du commit)
```

## Qui a fait quoi

| Partie | Auteur |
|---|---|
| Code applicatif (backend, frontend, tests, docker-compose) | backend et frontend généré par IA, relu par moi |
| Pipeline CI/CD, infra, bootstrap, destroy | Moi |
| Test de charge, scénarios de panne, diagnostic | Moi |

Je suis DevOps, pas développeur. J'ai préféré passer le temps imparti sur le déploiement, la mesure et le diagnostic plutôt que sur l'écriture d'un CRUD.

## Choix techniques

| Choix | Pourquoi | Ce que j'ai écarté |
|---|---|---|
| Azure Container Apps | Pas de cluster à gérer, autoscaling HTTP et révisions fournis. Pour deux services, c'est suffisant. | AKS : node pools, ingress controller, montées de version… beaucoup d'exploitation pour deux conteneurs, et plus cher. |
| Azure CLI dans le pipeline | Rien à initialiser avant le premier déploiement, et chaque commande est rendue idempotente. | Terraform : il faut un backend de state (storage account, verrouillage) avant de commencer. En vrai projet c'est ce que je prendrais. |
| OIDC GitHub → Entra ID | Jetons de courte durée, limités à ce repo. Aucun secret Azure dans GitHub. | Secret de service principal : secret longue durée à faire tourner, et à surveiller s'il fuit. |
| Rôle Contributor limité à `rg-kata` | Moindre privilège : le pipeline ne peut toucher qu'à ce resource group. | Contributor sur l'abonnement : bien plus simple, mais beaucoup trop large. |
| Application Insights (OpenTelemetry) | Natif Azure, la distro OpenTelemetry instrumente FastAPI et psycopg2 sans effort, rien à héberger. | Stack Grafana/Loki/Mimir : c'est ce que je connais le mieux, mais il faut l'héberger et l'exploiter, hors de propos ici. |
| Échantillonnage des traces à 10 % | Ne pas dépasser le plafond de 1 Go/jour pendant les tests de charge. | |

## Déployer

```bash
./infra/bootstrap.sh          # une seule fois : identité OIDC, rôle, variables du repo
gh secret set DB_PASSWORD     # une seule fois
git push                      # le pipeline crée ou met à jour tout
```

Compter environ 18 minutes la première fois (PostgreSQL est long à créer), 3 minutes ensuite d'apres mes tests.

## Détruire

```bash
./infra/destroy.sh
az group exists --name rg-kata   # doit renvoyer false
```

## Pipeline

Dans l'ordre :

1. tests de l'API ;
2. build des images `api` et `web`, push dans ACR avec le SHA du commit comme tag ;
3. création des ressources si elles n'existent pas : resource group, ACR, Log Analytics, Application Insights, PostgreSQL, environnement Container Apps ;
4. déploiement de l'API puis du front sur la nouvelle image ;
5. vérification que `/health` et `/ready` répondent.

Chaque création commence par vérifier si la ressource existe déjà. Le but est de pouvoir relancer le job sans réfléchir après une erreur passagère. C'est arrivé : un 503 d'Azure Resource Manager en plein déploiement, et une simple relance a suffi.

## Observabilité

L'API est instrumentée avec OpenTelemetry : chaque requête HTTP et chaque appel PostgreSQL remontent dans Application Insights, avec le lien entre les deux. Ça permet de savoir tout de suite si une requête lente passe son temps en base ou ailleurs (voir le diagnostic plus bas).

Le front nginx n'est pas instrumenté. Il ne fait que servir des fichiers statiques et relayer vers l'API, qui voit déjà toutes les requêtes utiles. Ajouter de la télémétrie côté navigateur aurait pris du temps pour peu d'information supplémentaire dans ce contexte.

Requêtes que j'ai utilisées (Application Insights → Journaux) :

```kusto
requests | where timestamp > ago(30m)
| summarize p95 = percentile(duration, 95), total = sum(itemCount) by name

dependencies | where timestamp > ago(30m)
| summarize p95 = percentile(duration, 95), total = sum(itemCount) by type, name
```

J'utilise `sum(itemCount)` et pas `count()` : avec l'échantillonnage à 10 %, chaque ligne conservée représente à peu près 10 requêtes. Avec `count()` on sous-estime le trafic d'un facteur 10.

## Test de charge (k6)

`loadtest/k6.js` : 80 % de lectures, 20 % d'écritures, montée progressive jusqu'à 50 utilisateurs. Seuils : p95 < 500 ms, erreurs < 1 %.

```bash
docker run --rm -i -e API_URL=https://<api> grafana/k6 run - < stresstest/k6.js
```

| Mesure | Avant | Après |
|---|---|---|
| p95 | 2 240 ms | 72 ms |
| médiane | 382 ms | 34 ms |
| erreurs | 0,15 % | 0 % |
| débit | 16,3 req/s | 25,1 req/s |

# Plan de tests — kata-sg

Chaque test indique la commande à lancer et le résultat attendu.

Variables utilisées partout :

```bash
API=https://api.gentlesea-cd887941.francecentral.azurecontainerapps.io
RG=rg-kata
```

Requêtes KQL : portail Azure → ressource Application Insights (`appin-kata`, ou `ai-kata` si non renommée) → **Journaux** → coller → **Exécuter**. Les données arrivent avec 2 à 5 min de délai.

---

## 1. Tests unitaires (CI)

Lancés automatiquement à chaque push par le job `test` (pytest + PostgreSQL en conteneur de service).

```bash
gh run list --limit 1
```

| Attendu |
|---|
| Job `test` vert avant tout déploiement |

---

## 2. Smoke tests après déploiement

```bash
curl -s $API/health      # le processus répond (ne teste pas la base)
curl -s $API/ready       # la base répond
curl -s $API/api/info    # nom de la réplica + version déployée
curl -s $API/api/items   # lecture en base
curl -s -X POST $API/api/items -H 'Content-Type: application/json' -d '{"name":"smoke"}'
```

| Test | Attendu |
|---|---|
| `/health` | `{"status":"ok"}` |
| `/ready` | `{"status":"ready"}` |
| `/api/info` | `version` = SHA du dernier commit |
| `GET /api/items` | liste JSON |
| `POST /api/items` | 201 + `{"id":…,"name":"smoke"}` |
| Front (URL `web` dans le navigateur) | page affichée, création d'un item OK |

---

## 3. Observabilité

Générer du trafic, attendre 3 min :

```bash
for i in $(seq 1 30); do curl -s -o /dev/null $API/api/items; done
```

```kusto
requests
| where timestamp > ago(30m)
| summarize total = sum(itemCount), p95 = percentile(duration, 95) by name, resultCode
```

```kusto
dependencies
| where timestamp > ago(30m)
| summarize total = sum(itemCount), p95 = percentile(duration, 95) by type, name
```

| Test | Attendu |
|---|---|
| Requêtes HTTP visibles | lignes `GET /api/items` en 200 |
| Appels PostgreSQL visibles | lignes `postgresql` SELECT / INSERT |
| Échantillonnage | `sum(itemCount)` ≈ 10 × `count()` |

---

## 4. Test de charge (k6)

`loadtest/k6.js` : 80 % lectures / 20 % écritures, montée à 50 utilisateurs en 2 min.
Seuils : p95 < 500 ms, erreurs < 1 %.

Terminal 2 (réplicas) :

```bash
watch -n 5 "az containerapp replica list -n api -g $RG --query '[].name' -o tsv | wc -l"
```

Terminal 1 :

```bash
docker run --rm -i -e API_URL=$API grafana/k6 run - < loadtest/k6.js
```

Contrôle côté serveur, 5 min après la fin :

```kusto
requests
| where timestamp > ago(15m)
| summarize total = sum(itemCount), p95 = percentile(duration, 95) by name
```

| Mesure | Attendu |
|---|---|
| Seuils k6 | ✓ tous les deux |
| p95 (k6) | < 500 ms |
| Erreurs (k6) | 0 % |
| Réplicas max | > 2 (autoscaling) |
| Total App Insights | ≈ total k6 (±10 %, échantillonnage) |
| p95 App Insights | ≈ p95 k6 |

---

## 5. Panne : crash d'une réplica

Activer la route (désactivée par défaut) :

```bash
az containerapp update -n api -g $RG --set-env-vars CHAOS_ENABLED=true --output none
az containerapp replica list -n api -g $RG \
  --query "[].{nom:name, redemarrages:properties.containers[0].restartCount}" -o table
```

Terminal 2 (sonde) :

```bash
while true; do echo "$(date +%T) $(curl -s -m 5 -w ' code=%{http_code}' $API/api/info)"; sleep 1; done
```

Terminal 1 :

```bash
curl -s -m 5 -X POST -w "\ncode=%{http_code}\n" $API/api/crash
# attendre 30 s
az containerapp replica list -n api -g $RG \
  --query "[].{nom:name, redemarrages:properties.containers[0].restartCount}" -o table
```

Désactiver la route :

```bash
az containerapp update -n api -g $RG --remove-env-vars CHAOS_ENABLED --output none
curl -s -X POST -w "\ncode=%{http_code}\n" $API/api/crash
```

| Test | Attendu |
|---|---|
| Réponse du crash | `202` + nom de la réplica |
| Sonde pendant le crash | `code=200` en continu, servie par l'autre réplica |
| Réplicas tombées | 1 seule (`redemarrages = 1`), l'autre à 0 |
| Retour de la réplica | automatique, ~20 s |
| Route après désactivation | `404` |

---

## 6. Panne : la base tombe (optionnel, ~15 min)

Terminal 2 (sonde) :

```bash
while true; do echo "$(date +%T) health=$(curl -s -m 5 -o /dev/null -w '%{http_code}' $API/health) ready=$(curl -s -m 5 -o /dev/null -w '%{http_code}' $API/ready)"; sleep 2; done
```

Terminal 1 :

```bash
az postgres flexible-server stop -g $RG -n pg-kata-hamdi
curl -s -m 10 -w "\n%{http_code} en %{time_total}s\n" $API/api/items
az postgres flexible-server start -g $RG -n pg-kata-hamdi
```

Preuve dans App Insights :

```kusto
requests
| where timestamp > ago(30m)
| summarize total = sum(itemCount) by bin(timestamp, 1m), resultCode
| render timechart
```

| Test | Attendu |
|---|---|
| `health` pendant la panne | 200 (pas de redémarrage en boucle) |
| `ready` pendant la panne | 503 en quelques secondes, ni 504 ni blocage |
| `/api/items` pendant la panne | `{"detail":"database unavailable"}`, 503 en ~3 s |
| Retour | `ready=200` sans intervention |

---

## 7. Destruction

```bash
./infrastructure/destroy.sh
az group wait --name $RG --deleted && echo "Groupe supprimé"
az group exists --name $RG
az ad app list --display-name gh-kata --query "[].appId" -o tsv
```

| Test | Attendu |
|---|---|
| Groupe de ressources | `false` |
| Identité GitHub | rien (supprimée) |

## Limites et ce que je ferais ensuite

- L'autoscaling met un moment à réagir : sur un pic court, les replicas arrivent après le pic.
- Pas de télémétrie côté front.
- La base est en accès public restreint aux services Azure. En production je la mettrais en réseau privé (VNet + private endpoint).
- Pas d'alertes. Les premières à ajouter : p95 et taux d'erreurs de l'API.
- Infra en Azure CLI : à passer en Terraform dès que le projet dure plus de quelques jours.