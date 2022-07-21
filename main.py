
import re
import logging
from os import getenv
from dataclasses import dataclass
from datetime import (
    date as Date,
    datetime as Datetime,
)
from contextlib import AsyncExitStack

from aiogram import (
    Bot,
    Dispatcher,
    executor
)
from aiogram.types import (
    ChatType,
    ContentType,
    ChatMemberStatus,
    BotCommand,
)
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.dispatcher.handler import CancelHandler
from aiogram.utils.exceptions import (
    BadRequest,
    MessageToForwardNotFound,
    MessageIdInvalid,
)

import aiobotocore.session


#######
#
#   SECRETS
#
######

# Ask @alexkuk for .env


BOT_TOKEN = getenv('BOT_TOKEN')

AWS_KEY_ID = getenv('AWS_KEY_ID')
AWS_KEY = getenv('AWS_KEY')

DYNAMO_ENDPOINT = getenv('DYNAMO_ENDPOINT')

CHAT_ID = int(getenv('CHAT_ID'))


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
CHATS = 'chats'
EVENT = 'event'
EVENTS_ARCHIVE = 'events_archive'
WHOIS_HOWTO = 'whois_howto'


@dataclass
class Post:
    message_id: int
    type: str
    event_date: Date = None


def find_posts(posts, message_id=None, type=None):
    for post in posts:
        if (
                message_id and post.message_id == message_id
                or type and post.type == type
        ):
            yield post


def find_post(posts, **kwargs):
    for post in find_posts(posts, **kwargs):
        return post


######
#   POST FOOTER
####

# #contacts
# #chats
# #event 2022-07-09


@dataclass
class PostFooter:
    type: str
    event_date: Date = None


EVENT_POST_FOOTER_PATTERN = re.compile(rf'''
\#{EVENT}
\s+
(\d\d\d\d-\d\d-\d\d)
''', re.X)

NAV_POST_FOOTER_PATTERN = re.compile(
    rf'#({CHATS}|{CONTACTS}|{EVENTS_ARCHIVE}|{WHOIS_HOWTO})'
)


def parse_post_footer(text):
    match = EVENT_POST_FOOTER_PATTERN.search(text)
    if match:
        event_date, = match.groups()
        event_date = Date.fromisoformat(event_date)
        return PostFooter(EVENT, event_date)

    match = NAV_POST_FOOTER_PATTERN.search(text)
    if match:
        type, = match.groups()
        return PostFooter(type)


#####
#  USER
#####


@dataclass
class User:
    id: int
    is_chat_member: bool


######
#
#  DYNAMO
#
######


######
#   MANAGER
######


async def dynamo_client():
    session = aiobotocore.session.get_session()
    manager = session.create_client(
        'dynamodb',

        # Always ru-central1 for YC
        # https://cloud.yandex.ru/docs/ydb/docapi/tools/aws-setup
        region_name='ru-central1',

        endpoint_url=DYNAMO_ENDPOINT,
        aws_access_key_id=AWS_KEY_ID,
        aws_secret_access_key=AWS_KEY,
    )

    # https://github.com/aio-libs/aiobotocore/discussions/955
    exit_stack = AsyncExitStack()
    client = await exit_stack.enter_async_context(manager)
    return exit_stack, client


######
#  OPS
#####


S = 'S'
N = 'N'
BOOL = 'BOOL'


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


async def dynamo_get(client, table, key_name, key_type, key_value):
    response = await client.get_item(
        TableName=table,
        Key={
            key_name: {
                key_type: str(key_value)
            }
        }
    )
    return response.get('Item')


async def dynamo_delete(client, table, key_name, key_type, key_value):
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
    message_id = int(item['message_id']['N'])
    type = item['type']['S']

    if 'event_date' in item:
        event_date = Date.fromisoformat(item['event_date']['S'])

    return Post(message_id, type, event_date)


def dynamo_format_post(post):
    item = {
        'type': {
            'S': post.type
        },
        'message_id': {
            'N': str(post.message_id)
        },
    }
    if post.event_date:
        item['event_date'] = {
            'S': post.event_date.isoformat()
        }
    return item


def dynamo_parse_user(item):
    id = int(item['id']['N'])
    is_chat_member = item['is_chat_member']['BOOL']
    return User(id, is_chat_member)


def dynamo_format_user(user):
    return {
        'id': {
            'N': str(user.id)
        },
        'is_chat_member': {
            'BOOL': user.is_chat_member
        }
    }


######
#   READ/WRITE
######


POSTS_TABLE = 'posts'
USERS_TABLE = 'users'

MESSAGE_ID_KEY = 'message_id'
ID_KEY = 'id'


async def read_posts(db):
    items = await dynamo_scan(db.client, POSTS_TABLE)
    return [dynamo_parse_post(_) for _ in items]


async def put_post(db, post):
    item = dynamo_format_post(post)
    await dynamo_put(db.client, POSTS_TABLE, item)


async def delete_post(db, message_id):
    await dynamo_delete(
        db.client, POSTS_TABLE,
        MESSAGE_ID_KEY, N, message_id
    )


async def get_user(db, id):
    item = await dynamo_get(
        db.client, USERS_TABLE,
        ID_KEY, N, id
    )
    if item:
        return dynamo_parse_user(item)


async def put_user(db, user):
    item = dynamo_format_user(user)
    await dynamo_put(db.client, USERS_TABLE, item)


async def delete_user(db, id):
    await dynamo_delete(
        db.client, USERS_TABLE,
        ID_KEY, N, id
    )


######
#  DB
#######


class DB:
    def __init__(self):
        self.exit_stack = None
        self.client = None

    async def connect(self):
        self.exit_stack, self.client = await dynamo_client()

    async def close(self):
        await self.exit_stack.aclose()


DB.read_posts = read_posts
DB.put_post = put_post
DB.delete_post = delete_post

DB.get_user = get_user
DB.put_user = put_user
DB.delete_user = delete_user


# YC Serverless Container allows up to 16 concurrent connections,
# before launching another instance. Telegram sends up to 16
# concurrent requests to webhook. So there should always be only one
# instace if CachedDB.


class CachedDB(DB):
    def __init__(self):
        DB.__init__(self)

        self.posts = None
        self.id_users = {}

    async def read_posts(self):
        if self.posts is None:
            self.posts = await DB.read_posts(self)
        return self.posts

    async def put_post(self, post):
        await DB.put_post(self, post)
        self.posts = None

    async def delete_post(self, message_id):
        await DB.delete_post(self, message_id)
        self.posts = None

    async def get_user(self, id):
        if id not in self.id_users:
            self.id_users[id] = await DB.get_user(self, id)
        return self.id_users[id]

    async def put_user(self, user):
        await DB.put_user(self, user)
        self.id_users.pop(user.id, None)

    async def delete_user(self, id):
        await DB.delete_user(self, id)
        self.id_users.pop(id, None)


#######
#
#   HANDLERS
#
####


START_COMMAND = 'start'
FUTURE_EVENTS_COMMAND = 'future_events'
EVENTS_ARCHIVE_COMMAND = EVENTS_ARCHIVE
CHATS_COMMAND = CHATS
CONTACTS_COMMAND = CONTACTS
WHOIS_HOWTO_COMMAND = WHOIS_HOWTO

BOT_COMMANDS = [
    BotCommand(FUTURE_EVENTS_COMMAND, 'ближайшие эвентах'),
    BotCommand(EVENTS_ARCHIVE_COMMAND, 'записи прошедших эвентов'),
    BotCommand(CHATS, 'тематические чаты'),
    BotCommand(CONTACTS_COMMAND, 'контакты кураторов'),
    BotCommand(WHOIS_HOWTO_COMMAND, 'зачем и как писать #whois'),
]

START_TEXT = f'''Что может делать этот бот?
Добавлять выпускников ШАД в закрытое комьюнити в телеграм. \
Для этого нужно только выпуститься из ШАДа :)
Рассказывать о предстоящих эвентах для выпускников ШАД.
Помогать ориентироваться, если хочешь помочь ШАДу.

Команды
/{FUTURE_EVENTS_COMMAND} - ближайшие эвенты;
/{WHOIS_HOWTO_COMMAND} - зачем и как писать #whois;
/{EVENTS_ARCHIVE_COMMAND} - записи прошедших эвентов;
/{CHATS_COMMAND} - тематические чаты;
/{CONTACTS_COMMAND} - контакты кураторов.'''

NO_FUTURE_EVENTS_TEXT = (
    'В ближайшее время нет эвентов. '
    f'Список прошедших - /{EVENTS_ARCHIVE_COMMAND}'
)

MISSING_POSTS_TEXT = 'Не нашел постов с тегом #{type}.'
MISSING_FORWARD_TEXT = (
    'Хотел переслать пост {url}, но он исчез. '
    'Удалил из своей базы.'
)

NOTIFY_NEW_POST = 'Добавил пост {url} базу, тег #{type}'
NOTIFY_DELETE_POST = 'Удалил пост {url} из базы, тег #{type}'


######
#  START
######


async def handle_start_command(context, message):
    await message.answer(text=START_TEXT)
    await context.bot.set_my_commands(
        commands=BOT_COMMANDS
    )


######
#   OTHER
#####


async def handle_other(context, message):
    await message.answer(text=START_TEXT)


######
#   FORWARD
####


def message_url(chat_id, message_id):
    # https://github.com/aiogram/aiogram/blob/master/aiogram/types/chat.py#L79
    chat_id = -1_000_000_000_000 - chat_id

    # -1001627609834, 21 -> https://t.me/c/1627609834/21
    return f'https://t.me/c/{chat_id}/{message_id}'


async def forward_post(context, message, post):
    # Telegram Bot API missing delete event
    # https://github.com/tdlib/telegram-bot-api/issues/286#issuecomment-1154020149
    # Remove after forward fails

    try:
        await context.bot.forward_message(
            chat_id=message.chat.id,
            from_chat_id=CHAT_ID,
            message_id=post.message_id
        )

    # No sure why 2 types of exceptions
    # Clear history, empty chat -> MessageIdInvalid
    # Remove single message -> MessageToForwardNotFound
    except (MessageToForwardNotFound, MessageIdInvalid):
        await context.db.delete_post(post.message_id)

        url = message_url(
            chat_id=CHAT_ID,
            message_id=post.message_id
        )
        text = MISSING_FORWARD_TEXT.format(url=url)
        await message.answer(text=text)


######
#   FUTURE EVENTS
#######


def select_future(posts, cap=3):
    today = Datetime.now().date()
    posts = [
        _ for _ in posts
        if _.event_date >= today
    ]
    posts.sort(key=lambda _: _.event_date)
    return posts[:cap]


async def handle_future_events_command(context, message):
    posts = await context.db.read_posts()
    posts = list(find_posts(posts, type=EVENT))
    if not posts:
        text = MISSING_POSTS_TEXT.format(type=EVENT)
        await message.answer(text=text)
        return

    posts = select_future(posts)
    if not posts:
        await message.answer(text=NO_FUTURE_EVENTS_TEXT)
        return

    for post in posts:
        await forward_post(context, message, post)


#######
#  NAV
####


async def handle_nav_command(context, message, type):
    posts = await context.db.read_posts()
    post = find_post(posts, type=type)
    if post:
        await forward_post(context, message, post)
    else:
        text = MISSING_POSTS_TEXT.format(type=type)
        await message.answer(text=text)


async def handle_chats_command(context, message):
    await handle_nav_command(context, message, CHATS)


async def handle_contacts_command(context, message):
    await handle_nav_command(context, message, CONTACTS)


async def handle_whois_howto_command(context, message):
    await handle_nav_command(context, message, WHOIS_HOWTO)


async def handle_events_arhive_command(context, message):
    await handle_nav_command(context, message, EVENTS_ARCHIVE)


####
#  CHAT
#####


async def handle_new_post(context, message, footer):
    post = Post(
        message.message_id, footer.type,
        footer.event_date
    )
    await context.db.put_post(post)
    await notify_post(
        context, message,
        footer.type, NOTIFY_NEW_POST
    )


async def handle_delete_post(context, message, post):
    await context.db.delete_post(post.message_id)
    await notify_post(
        context, message,
        post.type, NOTIFY_DELETE_POST
    )


async def notify_post(context, message, type, pattern):
    url = message_url(
        chat_id=message.chat.id,
        message_id=message.message_id
    )
    text = pattern.format(
        url=url,
        type=type
    )
    try:
        await context.bot.send_message(
            chat_id=message.from_user.id,
            text=text
        )
    except BadRequest:
        # Post author does not use bot for example, or blocked it, or whatever
        # https://github.com/aiogram/aiogram/blob/master/examples/broadcast_example.py#L25
        pass


async def handle_chat_new_message(context, message):
    footer = parse_post_footer(message.text)
    if footer:
        await handle_new_post(context, message, footer)


async def handle_chat_edited_message(context, message):
    footer = parse_post_footer(message.text)
    if footer:
        # Added footer to existing message
        await handle_new_post(context, message, footer)
        return

    posts = await context.db.read_posts()
    post = find_post(posts, message_id=message.message_id)
    if post:
        # Removed footer from post
        await handle_delete_post(context, message, post)


async def handle_chat_new_member(context, message):
    for member in message.new_chat_members:
        user = User(member.id, is_chat_member=True)
        context.db.put_user(user)


async def handle_chat_left_member(context, message):
    member = message.left_chat_member
    user = User(member.id, is_chat_member=False)
    context.db.put_user(user)


#####
#  SETUP
#####


def setup_handlers(context):
    context.dispatcher.register_message_handler(
        context.handle_start_command,
        chat_type=ChatType.PRIVATE,
        commands=START_COMMAND,
    )

    context.dispatcher.register_message_handler(
        context.handle_future_events_command,
        chat_type=ChatType.PRIVATE,
        commands=FUTURE_EVENTS_COMMAND,
    )
    context.dispatcher.register_message_handler(
        context.handle_chats_command,
        chat_type=ChatType.PRIVATE,
        commands=CHATS_COMMAND,
    )
    context.dispatcher.register_message_handler(
        context.handle_contacts_command,
        chat_type=ChatType.PRIVATE,
        commands=CONTACTS_COMMAND,
    )
    context.dispatcher.register_message_handler(
        context.handle_whois_howto_command,
        chat_type=ChatType.PRIVATE,
        commands=WHOIS_HOWTO_COMMAND,
    )
    context.dispatcher.register_message_handler(
        context.handle_events_arhive_command,
        chat_type=ChatType.PRIVATE,
        commands=EVENTS_ARCHIVE_COMMAND,
    )

    context.dispatcher.register_message_handler(
        context.handle_other,
        chat_type=ChatType.PRIVATE,
    )

    context.dispatcher.register_message_handler(
        context.handle_chat_new_message,
        chat_id=CHAT_ID,
    )
    context.dispatcher.register_edited_message_handler(
        context.handle_chat_edited_message,
        chat_id=CHAT_ID,
    )
    context.dispatcher.register_message_handler(
        context.handle_chat_new_member,
        chat_id=CHAT_ID,
        content_types=ContentType.NEW_CHAT_MEMBERS
    )
    context.dispatcher.register_message_handler(
        context.handle_chat_left_member,
        chat_id=CHAT_ID,
        content_types=ContentType.LEFT_CHAT_MEMBER
    )


######
#
#   MIDDLEWARE
#
######


#######
#  LOGGING
######


class LoggingMiddleware(BaseMiddleware):
    async def on_pre_process_update(self, update, data):
        log.debug(f'Update: {update}')


#######
#  CHAT MEMBER
######


NOT_CHAT_MEMBER_TEXT = (
    'Не нашел тебя в чате выпускников ШАДа. '
    'Напиши, пожалуйста, кураторам. '
    'Бот отвечает только тем кто в чатике.'
)


class UserNotFound(BadRequest):
    match = 'user not found'


async def check_chat_member(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id
        )
    except UserNotFound:
        return False

    if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return False

    return True


class ChatMemberMiddleware(BaseMiddleware):
    def __init__(self, context):
        self.context = context
        BaseMiddleware.__init__(self)

    # Only register_message_handler for private chats in
    # setup_handlers

    async def on_pre_process_message(self, message, data):
        if message.chat.id == CHAT_ID:
            return

        if message.chat.type == ChatType.PRIVATE:
            id = message.from_user.id
            user = await self.context.db.get_user(id)
            if not user:
                is_chat_member = await check_chat_member(
                    self.context.bot,
                    chat_id=CHAT_ID,
                    user_id=id
                )
                user = User(id, is_chat_member)
                await self.context.db.put_user(user)

            if user.is_chat_member:
                return

        await message.answer(text=NOT_CHAT_MEMBER_TEXT)
        raise CancelHandler


#######
#   WIP
######


WIP_TEXT = 'Бот пока отвечает только разработчикам.'
WIP_USERNAMES = [
    'alexkuk',
    'shuternay',
    'tinicheva',
    'farshov',
]


class WIPMiddleware(BaseMiddleware):
    def __init__(self, context):
        self.context = context
        BaseMiddleware.__init__(self)

    async def on_pre_process_message(self, message, data):
        if (
                message.chat.type == ChatType.PRIVATE
                and message.from_user.username not in WIP_USERNAMES
        ):
            await message.answer(text=WIP_TEXT)
            raise CancelHandler


#######
#   SETUP
#########


def setup_middlewares(context):
    middlewares = [
        LoggingMiddleware(),
        WIPMiddleware(context),
        ChatMemberMiddleware(context),
    ]
    for middleware in middlewares:
        context.dispatcher.middleware.setup(middleware)


#######
#
#   BOT
#
#####


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
        self.bot = Bot(token=BOT_TOKEN)
        self.dispatcher = Dispatcher(self.bot)
        self.db = CachedDB()


BotContext.handle_start_command = handle_start_command
BotContext.handle_future_events_command = handle_future_events_command
BotContext.handle_chats_command = handle_chats_command
BotContext.handle_contacts_command = handle_contacts_command
BotContext.handle_whois_howto_command = handle_whois_howto_command
BotContext.handle_events_arhive_command = handle_events_arhive_command

BotContext.handle_other = handle_other

BotContext.handle_chat_new_message = handle_chat_new_message
BotContext.handle_chat_edited_message = handle_chat_edited_message
BotContext.handle_chat_new_member = handle_chat_new_member
BotContext.handle_chat_left_member = handle_chat_left_member

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
