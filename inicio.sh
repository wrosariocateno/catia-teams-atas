#!/bin/bash

clear

PROJECT="catia-cateno"
REGION="southamerica-east1"
SERVICE="catia-teams-atas2"

echo "PROJETO:"$PROJECT
echo "REGIAO:"$REGION
echo "SERVICO:"$SERVICE

gcloud run services describe catia-teams-atas2 \
  --region=southamerica-east1 \
  --format="value(spec.template.spec.containers[0].env)"

pause
#gcloud run deploy "$SERVICE" \
#  --region "$REGION" \
#  --source . \
#  --allow-unauthenticated
gcloud config set builds/use_kaniko True
gcloud builds submit --tag "gcr.io/$PROJECT/$SERVICE" --project "$PROJECT" --no-cache

APP_URL=https://catia-teams-atas2-697553333263.southamerica-east1.run.app

echo "APP_URL:"$APP_URL

curl -s   -H "Authorization: Bearer $(gcloud auth print-identity-token)"   "$APP_URL/shared-search-teste?limit=60&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4" | jq .