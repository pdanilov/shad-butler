
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
    ChatMemberStatus,
)
from aiogram.dispatcher.filters import BoundFilter
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.utils.exceptions import (
    BadRequest,
    MessageToForwardNotFound,
    MessageIdInvalid,
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


#####
#  USER
#####


START_STATE = 'start'
GRAD_STATE = 'grad'
NO_GRAD_STATE = 'no_grad'
NAV_STATE = 'nav'


@dataclass
class User:
    id: int
    state: str


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


# TODO Not sure the best way use aiobotocore in non context
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


def dynamo_parse_user(item):
    id = int(item['id']['N'])
    state = item['state']['S']
    return User(id, state)


def dynamo_format_user(user):
    return {
        'id': {
            'N': str(user.id)
        },
        'state': {
            'S': user.state
        }
    }


######
#   READ/WRITE
######


POSTS_TABLE = 'posts'
GRADS_TABLE = 'grads'
USERS_TABLE = 'users'

MESSAGE_ID_KEY = 'message_id'
USERNAME_KEY = 'username'
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


async def grad_exists(db, username):
    item = await dynamo_get(
        db.client, GRADS_TABLE,
        USERNAME_KEY, S, username
    )
    return item is not None


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
        self.manager = None
        self.client = None

    async def connect(self):
        self.manager = dynamo_manager()
        self.client = await enter_dynamo(self.manager)

    async def close(self):
        await exit_dynamo(self.manager)

    async def set_user_state(self, id, state):
        user = User(id, state)
        await self.put_user(user)


DB.read_posts = read_posts
DB.put_post = put_post
DB.delete_post = delete_post

DB.grad_exists = grad_exists

DB.get_user = get_user
DB.put_user = put_user
DB.delete_user = delete_user


# Assume only one instance of CachedDB exists in the world
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


def one_row_keyboard(labels):
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(_)
            for _ in labels
        ]],
        resize_keyboard=True
    )


START_CHAT_MEMBER_TEXT = 'Привет, вижу, ты уже в чате выпускников.'
START_GRAD_TEXT = (
    'Привет, нашел юзернейм @{username} в базе ШАДа. '
    'Вот одноразовая ссылка на закрытый чатик выпускников {url}. Заходи!'
)
START_NO_GRAD_TEXT = (
    'Привет, не нашёл юзернейм @{username} в базе ШАДа. Напиши куратору.'
)

GRAD_KEYBOARD = one_row_keyboard(['Что дальше?'])
GRAD_SUCCESS_TEXT = 'Ура!'
GRAD_FAIL_TEXT = 'Не нашел тебя в чате, заходи по ссылке.'

NO_GRAD_KEYBOARD = one_row_keyboard(['Попробовать еще раз'])

EVENTS_BUTTON_TEXT = '🎉 События'
LOCAL_CHATS_BUTTON_TEXT = 'Чаты городов'
CONTACTS_BUTTON_TEXT = 'Контакты кураторов'

NAV_DOC_TEXT = (
    'Я простой бот, умею отвечать только на 3 сообщения:\n'
    f'«{EVENTS_BUTTON_TEXT}» — 3 ближайших мероприятия;\n'
    f'«{LOCAL_CHATS_BUTTON_TEXT}» — ссылки на местные чатики 🇦🇲🇬🇪🇬🇧🇮🇱;\n'
    f'«{CONTACTS_BUTTON_TEXT}» — к кому обращаться если есть вопросы, идеи.'
)
NAV_KEYBOARD = one_row_keyboard([
    EVENTS_BUTTON_TEXT,
    LOCAL_CHATS_BUTTON_TEXT,
    CONTACTS_BUTTON_TEXT,
])

MISSING_NAV_TEXT = 'Странно, инфы нет в базе.'
NO_EVENTS_TEXT = (
    'В ближайшее время нет событий. '
    'Чтобы посмотреть прошедшие, поищи по тегу #event в чате ШАД 15+.'
)
MISSING_FORWARD_TEXT = (
    'Хотел переслать пост {url}, но он исчез, '
    'странно, удалю и у себя.'
)
NAV_OTHER_TEXT = 'Извини, не понял «{text}».'


def join_texts(texts):
    return '\n\n'.join(texts)


#####
#   START
######


async def chat_member_exists(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id
        )
    except BadRequest:
        # TODO Pull request to aiogram.utils.exceptions UserNotFound with
        # match='User not found'
        return False

    if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return False
    else:
        return True


async def start_chat_member(context, message):
    await message.answer(
        text=join_texts([
            START_CHAT_MEMBER_TEXT,
            NAV_DOC_TEXT
        ]),
        reply_markup=NAV_KEYBOARD
    )
    await context.db.set_user_state(
        message.from_user.id,
        NAV_STATE
    )


async def start_grad(context, message):
    # New invite link name = user username. May exists multiple links
    # with same name. After user join, link does not work for other
    # users. User may leave and rejoin having that link.
    result = await context.bot.create_chat_invite_link(
        chat_id=secret.SHAD_CHAT_ID,
        name=message.from_user.username,
        member_limit=1
    )
    url = result.invite_link

    text = START_GRAD_TEXT.format(
        username=message.from_user.username,
        url=url
    )
    await message.answer(
        text=text,
        reply_markup=GRAD_KEYBOARD
    )
    await context.db.set_user_state(
        message.from_user.id,
        GRAD_STATE
    )


async def start_no_grad(context, message):
    text = START_NO_GRAD_TEXT.format(
        username=message.from_user.username,
    )
    await message.answer(
        text=text,
        reply_markup=NO_GRAD_KEYBOARD
    )
    await context.db.set_user_state(
        message.from_user.id,
        NO_GRAD_STATE
    )


async def handle_start_state(context, message):
    is_chat_member = await chat_member_exists(
        context.bot,
        chat_id=secret.SHAD_CHAT_ID,
        user_id=message.from_user.id
    )
    if is_chat_member:
        await start_chat_member(context, message)

    else:
        is_grad = await context.db.grad_exists(message.from_user.username)
        if is_grad:
            await start_grad(context, message)
        else:
            await start_no_grad(context, message)


######
#  GRAD
######


async def handle_grad_state(context, message):
    is_chat_member = await chat_member_exists(
        context.bot,
        chat_id=secret.SHAD_CHAT_ID,
        user_id=message.from_user.id
    )
    if is_chat_member:
        text = join_texts([
            GRAD_SUCCESS_TEXT,
            NAV_DOC_TEXT
        ])
        await message.answer(
            text=text,
            reply_markup=NAV_KEYBOARD
        )
        await context.db.set_user_state(
            message.from_user.id,
            NAV_STATE
        )

    else:
        await message.answer(
            text=GRAD_FAIL_TEXT,
            reply_markup=GRAD_KEYBOARD
        )


#######
#  NO GRAD
######


async def handle_no_grad_state(context, message):
    await handle_start_state(context, message)


######
#   NAV
#######


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


async def handle_nav_events(context, message, cap=3):
    posts = await context.db.read_posts()
    posts = [
        _ for _ in posts
        if _.type == EVENT
    ]
    if not posts:
        await message.answer(text=MISSING_NAV_TEXT)
        return

    posts.sort(key=lambda _: _.message_id)
    posts = {
        _.event_tag: _
        for _ in posts
    }.values()

    today = Datetime.now().date()
    posts = [
        _ for _ in posts
        if _.event_date >= today
    ]
    posts.sort(key=lambda _: _.event_date)
    posts = posts[:cap]
    if not posts:
        await message.answer(text=NO_EVENTS_TEXT)

    for post in posts:
        await forward_post(context, message, post)


async def handle_nav(context, message, type):
    posts = await context.db.read_posts()
    post = find_post(posts, type=type)
    if post:
        await forward_post(context, message, post)
    else:
        await message.answer(text=MISSING_NAV_TEXT)


async def handle_nav_local_chats(context, message):
    await handle_nav(context, message, LOCAL_CHATS)


async def handle_nav_contacts(context, message):
    await handle_nav(context, message, CONTACTS)


async def handle_nav_other(contacts, message):
    text = join_texts([
        NAV_OTHER_TEXT.format(text=message.text),
        NAV_DOC_TEXT
    ])
    await message.answer(
        text=text,
        reply_markup=NAV_KEYBOARD
    )


####
#  CHAT
#####


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


#####
#  SETUP
#####


def setup_handlers(context):
    context.dispatcher.register_message_handler(
        context.handle_start_state,
        chat_type=ChatType.PRIVATE,
        user_state=START_STATE,
    )
    context.dispatcher.register_message_handler(
        context.handle_grad_state,
        chat_type=ChatType.PRIVATE,
        user_state=GRAD_STATE,
    )
    context.dispatcher.register_message_handler(
        context.handle_no_grad_state,
        chat_type=ChatType.PRIVATE,
        user_state=NO_GRAD_STATE,
    )

    context.dispatcher.register_message_handler(
        context.handle_nav_events,
        chat_type=ChatType.PRIVATE,
        text=EVENTS_BUTTON_TEXT,
        user_state=NAV_STATE,
    )
    context.dispatcher.register_message_handler(
        context.handle_nav_local_chats,
        chat_type=ChatType.PRIVATE,
        text=LOCAL_CHATS_BUTTON_TEXT,
        user_state=NAV_STATE,
    )
    context.dispatcher.register_message_handler(
        context.handle_nav_contacts,
        chat_type=ChatType.PRIVATE,
        text=CONTACTS_BUTTON_TEXT,
        user_state=NAV_STATE,
    )
    context.dispatcher.register_message_handler(
        context.handle_nav_other,
        chat_type=ChatType.PRIVATE,
        user_state=NAV_STATE,
    )

    context.dispatcher.register_message_handler(
        context.handle_chat_new_message,
        chat_id=secret.SHAD_CHAT_ID,
    )
    context.dispatcher.register_edited_message_handler(
        context.handle_chat_edited_message,
        chat_id=secret.SHAD_CHAT_ID,
    )


#####
#  FILTER
####


class UserStateFilter(BoundFilter):
    context = None
    key = 'user_state'

    def __init__(self, user_state):
        self.user_state = user_state

    async def check(self, obj):
        user = await self.context.db.get_user(obj.from_user.id)
        state = (
            user.state if user
            else START_STATE
        )
        return state == self.user_state


def setup_filters(context):
    UserStateFilter.context = context
    context.dispatcher.filters_factory.bind(UserStateFilter)


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
        self.db = CachedDB()


BotContext.handle_start_state = handle_start_state
BotContext.handle_grad_state = handle_grad_state
BotContext.handle_no_grad_state = handle_no_grad_state

BotContext.handle_nav_events = handle_nav_events
BotContext.handle_nav_local_chats = handle_nav_local_chats
BotContext.handle_nav_contacts = handle_nav_contacts
BotContext.handle_nav_other = handle_nav_other

BotContext.handle_chat_new_message = handle_chat_new_message
BotContext.handle_chat_edited_message = handle_chat_edited_message

BotContext.setup_handlers = setup_handlers
BotContext.setup_filters = setup_filters
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
    context.setup_filters()
    context.setup_handlers()
    context.setup_middlewares()
    context.run()
