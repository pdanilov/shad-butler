
import logging
from os import getenv
from dataclasses import dataclass
from datetime import (
    date as Date,
    datetime as Datetime,
)

from aiogram import (
    Bot,
    Dispatcher,
    executor
)
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ChatType,
)
from aiogram.dispatcher.middlewares import BaseMiddleware

import aiobotocore.session

# Ask @alexkuk for secret.py
import secret


######
#
#   LOGGER
#
#######


log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
log.addHandler(logging.StreamHandler())


######
#
#  DYNAMO
#
######


######
#   MANAGER
######


def dynamo_manager():
    session = aiobotocore.session.get_session()
    return session.create_client(
        'dynamodb',

        # Always ru-central1 for YC
        # https://cloud.yandex.ru/docs/ydb/docapi/tools/aws-setup
        region_name='ru-central1',

        endpoint_url=secret.DYNAMO_ENDPOINT,
        aws_access_key_id=secret.AWS_KEY_ID,
        aws_secret_access_key=secret.AWS_KEY,
    )


# Not sure the best way use aiobotocore in non context
# https://github.com/aio-libs/aiobotocore/discussions/955


async def enter_dynamo(manager):
    return await manager.__aenter__()


async def exit_dynamo(manager):
    await manager.__aexit__(
        exc_type=None,
        exc_val=None,
        exc_tb=None
    )


######
#  OPS
#####


async def dynamo_scan(client, table):
    response = await client.scan(
        TableName=table
    )
    return response['Items']


async def dynamo_scan_first(client, table):
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
#   READ
######


EVENT_MESSAGES_TABLE = 'event_messages'
LOCAL_CHATS_MESSAGE_TABLE = 'local_chats_message'
CONTACTS_MESSAGE_TABLE = 'contacts_message'


async def read_event_messages(client):
    items = await dynamo_scan(client, EVENT_MESSAGES_TABLE)
    return [parse_event_message(_) for _ in items]


async def read_local_chats_message(client):
    item = await dynamo_scan_first(client, LOCAL_CHATS_MESSAGE_TABLE)
    return parse_nav_message(item)


async def read_contacts_message(client):
    item = await dynamo_scan_first(client, CONTACTS_MESSAGE_TABLE)
    return parse_nav_message(item)


######
#  DB
#######


class DB:
    def __init__(self):
        self.manager = dynamo_manager()
        self.client = None

        self.cache = {}

    async def connect(self):
        self.client = await enter_dynamo(self.manager)

    async def close(self):
        await exit_dynamo(self.manager)

    async def cached(self, read, *args):
        key = read, args
        result = self.cache.get(key)
        if not result:
            result = await read(self.client, *args)
        self.cache[key] = result
        return result


#######
#
#   BOT
#
#####


######
#  HANDLERS
######


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


async def handle_start_command(context, message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(EVENTS_BUTTON_TEXT),
            KeyboardButton(LOCAL_CHATS_BUTTON_TEXT),
            KeyboardButton(CONTACTS_BUTTON_TEXT),
        ]],
        resize_keyboard=True
    )
    await context.bot.send_message(
        chat_id=message.chat.id,
        text=START_MESSAGE_TEXT,
        reply_markup=keyboard
    )


async def handle_events_button(context, message):
    records = await context.db.cached(read_event_messages)
    today = Datetime.now().date()
    records = (
        _ for _ in records
        if _.date >= today
    )
    records = sorted(
        records,
        key=lambda _: _.date
    )
    for record in records:
        await context.bot.forward_message(
            chat_id=message.chat.id,
            from_chat_id=secret.SHAD_CHAT_ID,
            message_id=record.message_id
        )

    await context.bot.send_message(
        chat_id=message.chat.id,
        text=MORE_EVENTS_MESSAGE_TEXT
    )


async def handle_nav_button(context, message, record):
    await context.bot.forward_message(
        chat_id=message.chat.id,
        from_chat_id=secret.SHAD_CHAT_ID,
        message_id=record.message_id
    )


async def handle_local_chats_button(context, message):
    record = await context.db.cached(read_local_chats_message)
    await handle_nav_button(context, message, record)


async def handle_contacts_button(context, message):
    record = await context.db.cached(read_contacts_message)
    await handle_nav_button(context, message, record)


def setup_handlers(context):
    context.dispatcher.register_message_handler(
        context.handle_start_command,
        chat_type=ChatType.PRIVATE,
        commands=START_COMMAND,
    )

    context.dispatcher.register_message_handler(
        context.handle_events_button,
        chat_type=ChatType.PRIVATE,
        text=EVENTS_BUTTON_TEXT,
    )
    context.dispatcher.register_message_handler(
        context.handle_local_chats_button,
        chat_type=ChatType.PRIVATE,
        text=LOCAL_CHATS_BUTTON_TEXT,
    )
    context.dispatcher.register_message_handler(
        context.handle_contacts_button,
        chat_type=ChatType.PRIVATE,
        text=CONTACTS_BUTTON_TEXT,
    )


######
#   MIDDLEWARE
#######


class LoggingMiddleware(BaseMiddleware):
    async def on_pre_process_update(self, update, data):
        log.debug(f'Update: {update}')


def setup_middlewares(context):
    context.dispatcher.middleware.setup(LoggingMiddleware())


########
#   WEBHOOK
######


async def on_startup(context, _):
    await context.db.connect()


async def on_shutdown(context, _):
    await context.db.close()


# YC Serverless Containers requires PORT env var
# https://cloud.yandex.ru/docs/serverless-containers/concepts/runtime#peremennye-okruzheniya
PORT = getenv('PORT', 8080)


def run(context):
    executor.start_webhook(
        dispatcher=context.dispatcher,

        # YC Serverless Container is assigned with endpoint
        # https://bba......v7v9.containers.yandexcloud.net/
        webhook_path='/',

        port=PORT,

        on_startup=context.on_startup,
        on_shutdown=context.on_shutdown,

        # Disable aiohttp "Running on ... Press CTRL+C"
        # Polutes YC Logging
        print=None
    )


########
#   CONTEXT
######


class BotContext:
    def __init__(self):
        self.bot = Bot(token=secret.BOT_TOKEN)
        self.dispatcher = Dispatcher(self.bot)
        self.db = DB()


BotContext.handle_start_command = handle_start_command
BotContext.handle_events_button = handle_events_button
BotContext.handle_local_chats_button = handle_local_chats_button
BotContext.handle_contacts_button = handle_contacts_button
BotContext.setup_handlers = setup_handlers

BotContext.setup_middlewares = setup_middlewares

BotContext.on_startup = on_startup
BotContext.on_shutdown = on_shutdown
BotContext.run = run


######
#
#   MAIN
#
#####


if __name__ == '__main__':
    context = BotContext()
    context.setup_handlers()
    context.setup_middlewares()
    context.run()
