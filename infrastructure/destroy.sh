#!/bin/bash
# Supprime ce que bootstrap.sh et le pipeline ont créé dans Azure.
# Lancer : ./infrastructure/destroy.sh

set -e   # le script s'arrête à la première erreur

RG="rg-kata"

echo "Vont être supprimés : le groupe $RG (registre, base, apps, logs...) et l'identité GitHub gh-kata."
read -r -p "Tape oui pour confirmer : " REPONSE
if [ "$REPONSE" != "oui" ]; then
  echo "Annulé, rien n'a été supprimé."
  exit 0
fi

echo "1. Suppression de l'identité GitHub dans Entra ID"
# --display-name trouve les noms qui COMMENCENT par gh-kata : le --query garde le nom exact
for APP_ID in $(az ad app list --display-name gh-kata --query "[?displayName=='gh-kata'].appId" --output tsv); do
  az ad app delete --id "$APP_ID"
  echo "   Identité $APP_ID supprimée"
done

echo "2. Suppression du groupe $RG et de tout son contenu"
az group delete --name "$RG" --yes --no-wait

echo "Terminé. Azure continue la suppression en arrière-plan pendant quelques minutes."
echo "Pour vérifier : az group exists --name $RG   (attendu à la fin : false)"