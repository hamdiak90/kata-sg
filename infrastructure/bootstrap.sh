#!/bin/bash

set -e

GITHUB_OWNER="hamdiak90"
GITHUB_REPO="kata-sg"
RG="rg-kata"
LOCATION="francecentral"

SUBSCRIPTION_ID=$(az account show --query id --output tsv)
TENANT_ID=$(az account show --query tenantId --output tsv)
echo "Abonnement : $(az account show --query name --output tsv)"

echo "1. Activation des services Azure (quelques minutes la première fois)"
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.DBforPostgreSQL --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.Insights --wait
az provider register --namespace Microsoft.ManagedIdentity --wait

echo "2. Groupe de ressources $RG"
az group create --name "$RG" --location "$LOCATION" --output none

echo "3. Identité de GitHub dans Entra ID"
APP_ID=$(az ad app create --display-name gh-kata --query appId --output tsv)
SP_ID=$(az ad sp create --id "$APP_ID" --query id --output tsv)
sleep 20 

echo "4. Droit Contributor sur le groupe $RG, et rien d'autre"
az role assignment create \
  --assignee-object-id "$SP_ID" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG" \
  --output none

echo "5. Confiance : seule la branche main du repo peut se connecter"
OWNER_ID=$(gh api "repos/$GITHUB_OWNER/$GITHUB_REPO" --jq .owner.id)
REPO_ID=$(gh api "repos/$GITHUB_OWNER/$GITHUB_REPO" --jq .id)
SUBJECT="repo:$GITHUB_OWNER@$OWNER_ID/$GITHUB_REPO@$REPO_ID:ref:refs/heads/main"
echo "   Sujet : $SUBJECT"

cat > /tmp/credential.json <<EOF
{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$SUBJECT",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
az ad app federated-credential create --id "$APP_ID" --parameters /tmp/credential.json --output none

echo "6. Identité qui lira les images du registre (servira à l'étape 4)"
PULL_ID=$(az identity create --name id-kata-pull --resource-group "$RG" --query principalId --output tsv)
az role assignment create \
  --assignee-object-id "$PULL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role AcrPull \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG" \
  --output none || echo "   (si l'erreur dit 'already exists', c'est normal : le droit existe déjà)"

echo "7. Variables du repo (ce ne sont pas des secrets)"
gh variable set AZURE_CLIENT_ID --body "$APP_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
gh variable set AZURE_TENANT_ID --body "$TENANT_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"
gh variable set AZURE_SUBSCRIPTION_ID --body "$SUBSCRIPTION_ID" --repo "$GITHUB_OWNER/$GITHUB_REPO"

echo "Terminé. Tu peux pousser le job deploy."