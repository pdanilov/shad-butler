
import re
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
from aiogram.utils.exceptions import (
    MessageToForwardNotFound,
    MessageIdInvalid
)

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


#######
#
#   OBJ
#
####


######
#  POST
######


CONTACTS = 'contacts'
LOCAL_CHATS = 'local_chats'
EVENT = 'event'


@dataclass
class Post:
    type: str
    message_id: int
    event_tag: str = None
    event_date: Date = None


def find_posts(posts, type=None, message_id=None):
    for post in posts:
        if (
                type and post.type == type
                or message_id and post.message_id == message_id
        ):
            yield post


def max_message_id_post(posts):
    if posts:
        return max(posts, key=lambda _: _.message_id)


def find_post(posts, **kwargs):
    posts = list(find_posts(posts, **kwargs))
    return max_message_id_post(posts)


######
#   POST FOOTER
####


@dataclass
class PostFooter:
    type: str
    event_tag: str = None
    event_date: Date = None


EVENT_POST_FOOTER_PATTERN = re.compile(rf'''
\#{EVENT}
\s+
\#([^#\s]+)
\s+
(\d\d\d\d-\d\d-\d\d)
''', re.X)

NAV_POST_FOOTER_PATTERN = re.compile(rf'#({LOCAL_CHATS}|{CONTACTS})')


def parse_post_footer(text):
    # #contacts
    # #local_chats
    # #event #sf_picnic 2022-07-09
    # #event #zoom_alice 2022-07-13

    match = EVENT_POST_FOOTER_PATTERN.search(text)
    if match:
        event_tag, event_date = match.groups()
        event_date = Date.fromisoformat(event_date)
        return PostFooter(EVENT, event_tag, event_date)

    match = NAV_POST_FOOTER_PATTERN.search(text)
    if match:
        type, = match.groups()
        return PostFooter(type)


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


S = 'S'
N = 'N'


async def dynamo_scan(client, table):
    response = await client.scan(
        TableName=table
    )
    return response['Items']


async def dynamo_put(client, table, item):
    await client.put_item(
        TableName=table,
        Item=item
    )


async def dynamo_delete(client, table, key_name, key_value, key_type=N):
    await client.delete_item(
        TableName=table,
        Key={
            key_name: {
                key_type: str(key_value)
            }
        }
    )


######
#   DE/SERIALIZE
####


def dynamo_parse_post(item):
    type = item['type']['S']
    message_id = int(item['message_id']['N'])

    event_tag, event_date = None, None
    if 'event_tag' in item:
        event_tag = item['event_tag']['S']

    if 'event_date' in item:
        event_date = Date.fromisoformat(item['event_date']['S'])

    return Post(type, message_id, event_tag, event_date)


def dynamo_format_post(post):
    item = {
        'type': {
            'S': post.type
        },
        'message_id': {
            'N': str(post.message_id)
        },
    }
    if post.event_tag:
        item['event_tag'] = {
            'S': post.event_tag
        }
    if post.event_date:
        item['event_date'] = {
            'S': post.event_date.isoformat()
        }
    return item


######
#   READ/WRITE
######


POSTS_TABLE = 'posts'
MESSAGE_ID_KEY = 'message_id'


async def read_posts(db):
    items = await dynamo_scan(db.client, POSTS_TABLE)
    return [dynamo_parse_post(_) for _ in items]


async def put_post(db, post):
    item = dynamo_format_post(post)
    await dynamo_put(db.client, POSTS_TABLE, item)


async def delete_post(db, message_id):
    await dynamo_delete(
        db.client, POSTS_TABLE,
        MESSAGE_ID_KEY, message_id
    )


######
#  DB
#######


class DB:
    def __init__(self):
        self.manager = None
        self.client = None

    async def connect(self):
        self.manager = dynamo_manager()
        self.client = await enter_dynamo(self.manager)

    async def close(self):
        await exit_dynamo(self.manager)


DB.read_posts = read_posts
DB.put_post = put_post
DB.delete_post = delete_post


#######
#
#   BOT
#
#####


def message_url(chat_id, message_id):
    # -1001627609834, 21 -> https://t.me/c/1627609834/21

    chat_id = str(chat_id)
    if chat_id.startswith('-100'):
        # https://habr.com/ru/post/543676/
        # "перед id супергрупп и каналов пишется -100"
        chat_id = chat_id[4:]

    return f'https://t.me/c/{chat_id}/{message_id}'


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

MISSING_NAV_MESSAGE_TEXT = 'Странно, инфы нет в базе.'
MISSING_EVENTS_MESSAGE_TEXT = MISSING_NAV_MESSAGE_TEXT

NO_EVENTS_MESSAGE_TEXT = (
    'В ближайшее время нет событий. '
    'Чтобы посмотреть прошедшие, поищи по тегу #event в чате ШАД 15+.'
)

MISSING_FORWARD_TEXT = (
    'Хотел переслать пост {url}, но он исчез, странно, удалю и у себя.'
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
    await message.answer(
        text=START_MESSAGE_TEXT,
        reply_markup=keyboard
    )


async def forward_post(context, message, post):
    # Telegram Bot API missing delete update event
    # https://github.com/tdlib/telegram-bot-api/issues/286#issuecomment-1154020149
    # Remove after forward fails

    try:
        await context.bot.forward_message(
            chat_id=message.chat.id,
            from_chat_id=secret.SHAD_CHAT_ID,
            message_id=post.message_id
        )

    # No sure why 2 types of exceptions
    # Clear history, empty chat -> MessageIdInvalid
    # Remove single message -> MessageToForwardNotFound
    except (MessageToForwardNotFound, MessageIdInvalid):

        url = message_url(
            chat_id=secret.SHAD_CHAT_ID,
            message_id=post.message_id
        )
        text = MISSING_FORWARD_TEXT.format(url=url)
        await message.answer(text=text)
        await context.db.delete_post(post.message_id)


async def handle_events_button(context, message, cap=3):
    posts = await context.db.read_posts()
    if not posts:
        await message.answer(text=MISSING_EVENTS_MESSAGE_TEXT)
        return

    today = Datetime.now().date()
    posts = [
        _ for _ in posts
        if _.type == EVENT
        if _.event_date >= today
    ]
    posts = sorted(
        posts,
        key=lambda _: _.event_date
    )
    posts = posts[:cap]
    if not posts:
        await message.answer(text=NO_EVENTS_MESSAGE_TEXT)

    for post in posts:
        await forward_post(context, message, post)


async def handle_nav_button(context, message, type):
    posts = await context.db.read_posts()
    post = find_post(posts, type=type)
    if post:
        await forward_post(context, message, post)
    else:
        await message.answer(text=MISSING_NAV_MESSAGE_TEXT)


async def handle_local_chats_button(context, message):
    await handle_nav_button(context, message, LOCAL_CHATS)


async def handle_contacts_button(context, message):
    await handle_nav_button(context, message, CONTACTS)


async def new_post(context, message, footer):
    post = Post(
        footer.type, message.message_id,
        footer.event_tag, footer.event_date
    )
    await context.db.put_post(post)


async def handle_chat_new_message(context, message):
    footer = parse_post_footer(message.text)
    if footer:
        await new_post(context, message, footer)


async def handle_chat_edited_message(context, message):
    footer = parse_post_footer(message.text)
    if footer:
        # Added footer to existing message
        await new_post(context, message, footer)
        return

    posts = await context.db.read_posts()
    post = find_post(posts, message_id=message.message_id)
    if post:
        # Removed footer from post
        await context.db.delete_post(post.message_id)


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

    context.dispatcher.register_message_handler(
        context.handle_chat_new_message,
        chat_id=secret.SHAD_CHAT_ID,
    )
    context.dispatcher.register_edited_message_handler(
        context.handle_chat_edited_message,
        chat_id=secret.SHAD_CHAT_ID,
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
BotContext.handle_chat_new_message = handle_chat_new_message
BotContext.handle_chat_edited_message = handle_chat_edited_message
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
