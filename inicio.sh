#!/bin/bash

clear

# Verifica se existe alguma conta ativa configurada
ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)")

if [ -z "$ACTIVE_ACCOUNT" ]; then
    echo "⚠️ Nenhuma conta ativa encontrada. Iniciando login..."
    gcloud auth login --no-launch-browser
else
    echo "✅ Usuário já autenticado: $ACTIVE_ACCOUNT"
fi

PROJECT="catia-cateno"
REGION="southamerica-east1"
SERVICE="catia-teams-atas2"
SERVICE2="catia-teams-atas-auth"

echo "--------------------------------"
echo "PROJETO: $PROJECT"
echo "REGIAO:  $REGION"
echo "SERVICO: $SERVICE"
echo "SERVICO: $SERVICE2"
echo "--------------------------------"

# Configura o projeto padrão no gcloud para evitar erros de 'resource not specified'
gcloud config set project $PROJECT --quiet 2>/dev/null

echo "Verificando variáveis de $SERVICE"
gcloud run services describe $SERVICE \
  --region="$REGION" \
  --project="$PROJECT" \
  --format="json" | jq -r '.spec.template.spec.containers[0].env[] | "\(.name): \(.value)"'

echo "--------------------------------"
echo "Verificando variáveis de $SERVICE2"

gcloud run services describe $SERVICE2 \
  --region="$REGION" \
  --project="$PROJECT" \
  --format="json" | jq -r '.spec.template.spec.containers[0].env[] | "\(.name): \(.value)"'

echo "Iniciando Deploy..."
gcloud run deploy "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT" \
  --source . \
  --allow-unauthenticated

APP_URL="https://catia-teams-atas2-697553333263.southamerica-east1.run.app"

echo "--------------------------------"
echo "APP_URL: $APP_URL"
echo "Testando endpoint de busca..."
echo "--------------------------------"

# O curl agora vai rodar após o deploy
#curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
#  "$APP_URL/shared-search-teste?limit=60&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4" | jq .

# O curl agora vai rodar após o deploy
#curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
#  "$APP_URL/shared-search-teste?limit=60&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4" | jq .

#echo "curl -v -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \"$APP_URL/shared-search-teste?limit=60&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4&user=$ACTIVE_ACCOUNT\" | jq ."

#curl -i -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
#  "$APP_URL/shared-search-teste?limit=60&q=filetype:mp4" | jq .

#curl -v -H "Authorization: Bearer $(gcloud auth print-identity-token)" 'https://catia-teams-atas2-697553333263.southamerica-east1.run.app/shared-search-teste?limit=60&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4&user=william.rosario@prestadores.cateno.com.br'

curl -i -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  "$APP_URL/shared-search-teste?limit=60&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4&user=$ACTIVE_ACCOUNT" | jq .