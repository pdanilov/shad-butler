
from os import getenv
from dataclasses import dataclass
from datetime import (
    date as Date,
    datetime as Datetime,
)

from aiogram import (
    Bot,
    Dispatcher,
    executor,
    types
)
from aiogram.contrib.middlewares.logging import LoggingMiddleware

import aiobotocore.session

# Ask @alexkuk for secret.py
import secret


######
#
#  DYNAMO
#
######


@dataclass
class DynamoContext:
    manager: ...
    client: ... = None


def dynamo_manager():
    session = aiobotocore.session.get_session()
    return session.create_client(
        'dynamodb',
        region_name='ru-central1',
        endpoint_url=secret.DYNAMO_ENDPOINT,
        aws_access_key_id=secret.AWS_KEY_ID,
        aws_secret_access_key=secret.AWS_KEY,
    )


async def enter_dynamo(context):
    context.client = await context.manager.__aenter__()


async def exit_dynamo(context):
    await context.manager.__aexit__(
        exc_type=None,
        exc_val=None,
        exc_tb=None
    )


######
#  OPS
#####


async def db_scan(client, table):
    response = await client.scan(
        TableName=table
    )
    return response['Items']


async def db_scan_first(client, table):
    response = await client.scan(
        TableName=table,
        Limit=1
    )
    items = response['Items']
    if items:
        return items[0]


######
#   PARSE
####


@dataclass
class EventMessage:
    message_id: int
    date: Date


@dataclass
class NavMessage:
    message_id: int


def parse_event_message(item):
    # [{'date': {'S': '2023-01-01'}, 'message_id': {'N': '7'}},
    #  {'date': {'S': '2022-08-01'}, 'message_id': {'N': '6'}},
    #  {'date': {'S': '2022-01-01'}, 'message_id': {'N': '5'}}]

    return EventMessage(
        date=Date.fromisoformat(item['date']['S']),
        message_id=int(item['message_id']['N'])
    )


def parse_nav_message(item):
    # {'message_id': {'N': '4'}}

    return NavMessage(
        message_id=int(item['message_id']['N'])
    )


######
#  CACHE
######


EVENT_MESSAGES_TABLE = 'event_messages'
LOCAL_CHATS_MESSAGE_TABLE = 'local_chats_message'
CONTACTS_MESSAGE_TABLE = 'contacts_message'


@dataclass
class DBCache:
    event_messages: [EventMessage] = ()
    local_chats_message: NavMessage = None
    contacts_message: NavMessage = None


async def fill_db_cache(db, cache):
    items = await db_scan(db.client, EVENT_MESSAGES_TABLE)
    cache.event_messages = [parse_event_message(_) for _ in items]

    item = await db_scan_first(db.client, LOCAL_CHATS_MESSAGE_TABLE)
    cache.local_chats_message = parse_nav_message(item)

    item = await db_scan_first(db.client, CONTACTS_MESSAGE_TABLE)
    cache.contacts_message = parse_nav_message(item)


######
#  GLOBAL
#######


manager = dynamo_manager()
DB = DynamoContext(manager)

DB_CACHE = DBCache()


#######
#
#   BOT
#
#####


START_COMMAND = 'start'

EVENTS_BUTTON_TEXT = '🎉 События'
LOCAL_CHATS_BUTTON_TEXT = 'Чаты городов'
CONTACTS_BUTTON_TEXT = 'Контакты'
START_MESSAGE_TEXT = (
    'Привет, могу показать ближайшие события из чата ШАД 15+, '
    'ссылки на локальные чаты, контакты кураторов.'
)
MORE_EVENTS_MESSAGE_TEXT = (
    'Чтобы посмотреть все события, '
    'поищи по тегу #event в чате ШАД 15+'
)

BOT = Bot(token=secret.BOT_TOKEN)
DP = Dispatcher(BOT)
DP.middleware.setup(LoggingMiddleware())


@DP.message_handler(text=EVENTS_BUTTON_TEXT)
async def handle_events_button(message):
    today = Datetime.now().date()
    records = (
        _ for _ in DB_CACHE.event_messages
        if _.date >= today
    )
    records = sorted(
        records,
        key=lambda _: _.date
    )
    for record in records:
        await BOT.forward_message(
            chat_id=message.chat.id,
            from_chat_id=secret.SHAD_CHAT_ID,
            message_id=record.message_id
        )

    await BOT.send_message(
        chat_id=message.chat.id,
        text=MORE_EVENTS_MESSAGE_TEXT
    )


async def handle_nav_button(message, record):
    await BOT.forward_message(
        chat_id=message.chat.id,
        from_chat_id=secret.SHAD_CHAT_ID,
        message_id=record.message_id
    )


@DP.message_handler(text=LOCAL_CHATS_BUTTON_TEXT)
async def handle_local_chats_button(message):
    record = DB_CACHE.local_chats_message
    await handle_nav_button(message, record)


@DP.message_handler(text=CONTACTS_BUTTON_TEXT)
async def handle_contacts_button(message):
    record = DB_CACHE.contacts_message
    await handle_nav_button(message, record)


@DP.message_handler(commands=START_COMMAND)
async def handle_start(message):
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[[
            types.KeyboardButton(EVENTS_BUTTON_TEXT),
            types.KeyboardButton(LOCAL_CHATS_BUTTON_TEXT),
            types.KeyboardButton(CONTACTS_BUTTON_TEXT),
        ]],
        resize_keyboard=True
    )
    await BOT.send_message(
        chat_id=message.chat.id,
        text=START_MESSAGE_TEXT,
        reply_markup=keyboard
    )


async def on_startup(_):
    await enter_dynamo(DB)
    await fill_db_cache(DB, DB_CACHE)


async def on_shutdown(_):
    await exit_dynamo(DB)


# YC Serverless Containers requires PORT env var
# https://cloud.yandex.ru/docs/serverless-containers/concepts/runtime#peremennye-okruzheniya
PORT = getenv('PORT', 8080)


if __name__ == '__main__':
    executor.start_webhook(
        dispatcher=DP,
        webhook_path='/',
        port=PORT,

        on_startup=on_startup,
        on_shutdown=on_shutdown,

        # Disable aiohttp "Running on ... Press CTRL+C". Polutes YC
        # Logging
        print=None
    )
