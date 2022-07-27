
# Бот-навигатор для чата выпускников ШАДа

## Инструкции

### Добавить бота в чат

Добавить @shad_alumni_bot в админы, забрать все права кроме "Пригласительные ссылки".

Бот с правами админа читает все сообщения из чата. Бот их нигде не сохраняет, даже в логах. Бот ищет в сообщении футер типа "#contacts", "#event 2022-02-02", сохраняет номер такого сообщения в базу.

## Разработка, лог команд

Создать директорию в YC.

```bash
yc resource-manager folder create --name shad-alumni-bot
```

Создать сервисный аккаунт в YC. Записать `id` в `.env`.

```bash
yc iam service-accounts create shad-alumni-bot --folder-name shad-alumni-bot

id: {SERVICE_ACCOUNT_ID}
```

Сгенерить ключи для DynamoDB, добавить их в `.env`.

```bash
yc iam access-key create \
  --service-account-name shad-alumni-bot \
  --folder-name shad-alumni-bot

key_id: {AWS_KEY_ID}
secret: {AWS_KEY}
```

Назначить роли, сервисный акк может только писать и читать YDB.

Нюанс, в CLI есть опция поставить роль на YDB `yc ydb database add-access-binding`, но она <a href="https://cloud.yandex.ru/docs/ydb/security/">пока не работает</a>: "на данный момент роль может быть назначена только на родительский ресурс (каталог или облако), роли которого наследуются вложенными ресурсами". Поэтому роль на весь каталог `yc resource-manager folder add-access-binding`.

```bash
for role in ydb.viewer ydb.editor
do
  yc resource-manager folder add-access-binding shad-alumni-bot \
    --role $role \
    --service-account-name shad-alumni-bot \
    --folder-name shad-alumni-bot \
    --async
done
```

Создать базу YDB. Записать эндпоинт для DynamoDB в `.env`.

```bash
yc ydb database create default --serverless --folder-name shad-alumni-bot

document_api_endpoint: {DYNAMO_ENDPOINT}
```

Установить, настроить `aws`.

```bash
pip install awscli
aws configure --profile shad-alumni-bot

{AWS_KEY_ID}
{AWS_KEY}
ru-central1
```

Создать табличку.

```bash
aws dynamodb create-table \
  --table-name posts \
  --attribute-definitions \
    AttributeName=message_id,AttributeType=N \
    AttributeName=type,AttributeType=S \
    AttributeName=event_date,AttributeType=S \
  --key-schema \
    AttributeName=message_id,KeyType=HASH \
  --endpoint $DYNAMO_ENDPOINT \
  --profile shad-alumni-bot
```

Удалить таблички.

```bash
aws dynamodb delete-table --table-name posts \
  --endpoint $DYNAMO_ENDPOINT \
  --profile shad-alumni-bot
```

Список таблиц.

```bash
aws dynamodb list-tables \
  --endpoint $DYNAMO_ENDPOINT \
  --profile shad-alumni-bot
```

Заполнить табличку постами.

```bash
items=('{"message_id": {"N": "5"}, "event_date": {"S": "2022-07-09"}}' \
'{"message_id": {"N": "6"}, "event_date": {"S": "2022-07-13"}}' \
'{"message_id": {"N": "7"}, "event_date": {"S": "2022-07-25"}}')

for item in $items
do
  aws dynamodb put-item \
    --table-name posts \
    --item $item \
    --endpoint $DYNAMO_ENDPOINT \
    --profile shad-alumni-bot
done
```

Прочитать табличку.

```bash
aws dynamodb scan \
  --table-name posts \
  --endpoint $DYNAMO_ENDPOINT \
  --profile shad-alumni-bot
```

Создать реестр для контейнера в YC. Записать `id` в `.env`.

```bash
yc container registry create default --folder-name shad-alumni-bot

id: {REGISTRY_ID}
```

Дать права сервисному аккаунту читать из реестра. Интеграция с YC Serverless Container.

```bash
yc container registry add-access-binding default \
  --role container-registry.images.puller \
  --service-account-name shad-alumni-bot \
  --folder-name shad-alumni-bot
```

Создать Serverless Container. Записать `id` в `.env`.

```bash
yc serverless container create --name default --folder-name shad-alumni-bot

id: {CONTAINER_ID}
```

Разрешить без токена. Телеграм дергает вебхук.

```bash
yc serverless container allow-unauthenticated-invoke default \
  --folder-name shad-alumni-bot
```

Логи.

```bash
yc log read default --follow --folder-name shad-alumni-bot
```

Узнать телеграмный токен у @BotFather. Записать в `.env`.

Прицепить вебхук.

```bash
WEBHOOK_URL=https://${CONTAINER_ID}.containers.yandexcloud.net/
curl --url https://api.telegram.org/bot${BOT_TOKEN}/setWebhook\?url=${WEBHOOK_URL}
```

Узнать `chat_id` чатик выпускников. Скопировать ссылку на любое сообщение `https://t.me/c/123123123/5329`. Добавить в начало -100 `chat_id=-100123123123`. Записать `CHAT_ID` в `.env`.

Трюк чтобы загрузить окружение из `.env`.

```bash
export $(cat .env | xargs)
```

Установить зависимости для тестов.

```bash
pip install \
  pytest-aiohttp \
  pytest-asyncio \
  pytest-cov \
  pytest-flakes \
  pytest-pycodestyle
```

Прогнать линтер. Потестить базу, бота.

```bash
make test-lint
make test-lint test-key KEY=db
make test-lint test-key KEY=bot
```

Собрать образ, загрузить его в реестр, задер

```bash
make image push deploy
```
