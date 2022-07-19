
import asyncio
import datetime
from json import (
    loads as parse_json,
    dumps as format_json
)

import pytest

from aiogram.types import Update

from main import (
    Bot,
    Dispatcher,
    BadRequest,

    DB,
    CachedDB,
    BotContext,

    Date,

    Post,
    find_post,

    START_STATE,
    GRAD_STATE,
    NO_GRAD_STATE,
    NAV_STATE,
    User,
)


#######
#
#   DB
#
######


# To use db fixture with scope=session, has to define event_loop
# fixture, no idea why
# https://github.com/tortoise/tortoise-orm/issues/638


@pytest.fixture(scope='session')
def event_loop():
    return asyncio.get_event_loop()


@pytest.fixture(scope='session')
async def db():
    db = CachedDB()
    await db.connect()
    yield db
    await db.close()


async def test_db_posts(db):
    post = Post(
        type='test',
        message_id=-1,
        event_tag='zoom_vasya',
        event_date=Date.fromisoformat('2020-01-01')
    )

    # Yep, insert in prod DB. Type "test" should not interfere with
    # working bot
    await db.put_post(post)

    posts = await db.read_posts()
    assert find_post(posts, message_id=post.message_id) == post

    await db.delete_post(post.message_id)
    posts = await db.read_posts()
    assert not find_post(posts, message_id=post.message_id)


async def test_db_grads(db):
    assert await db.grad_exists('alexkuk')
    assert not await db.grad_exists('ne_grad')


async def test_db_users(db):
    user = User(
        id=1,
        state=START_STATE
    )
    await db.put_user(user)
    assert user == await db.get_user(user.id)
    await db.delete_user(user.id)


#####
#
#   BOT
#
#####


# Mock bot, do not send requests to Telegram server, just store in
# trace array. Okey to do so, almost never use Telegram response
# anyway. Exception is error in forwardMessage, that we use to delete
# post.

# Mock db, just put/delete items in array. Test actual Dynamo in
# separate db tests.


class FakeBot(Bot):
    def __init__(self, token):
        Bot.__init__(self, token)
        self.trace = []
        self.chat_members = []

    async def request(self, method, data):
        json = format_json(data, ensure_ascii=False)
        self.trace.append([method, json])
        return {}

    def trace_methods(self):
        return [method for method, _ in self.trace]

    async def get_chat_member(self, chat_id, user_id):
        if user_id not in self.chat_members:
            raise BadRequest('user not found')


class FakeDB(DB):
    def __init__(self):
        DB.__init__(self)
        self.posts = []
        self.grads = []
        self.users = []

    async def read_posts(self):
        return self.posts

    async def put_post(self, post):
        self.posts.append(post)

    async def delete_post(self, message_id):
        self.posts = [
            _ for _ in self.posts
            if _.message_id != message_id
        ]

    async def grad_exists(self, username):
        return username in self.grads

    async def get_user(self, id):
        for user in self.users:
            if user.id == id:
                return user

    async def put_user(self, user):
        self.users.append(user)


class FakeBotContext(BotContext):
    def __init__(self):
        self.bot = FakeBot('123:faketoken')
        self.dispatcher = Dispatcher(self.bot)
        self.db = FakeDB()


@pytest.fixture(scope='function')
def context():
    context = FakeBotContext()
    context.setup_filters()
    context.setup_handlers()

    Bot.set_current(context.bot)
    Dispatcher.set_current(context.dispatcher)

    return context


async def process_update(context, json):
    data = parse_json(json)
    update = Update(**data)
    await context.dispatcher.process_update(update)


#######
#   START
######


async def test_bot_start_chat_member(context):
    context.bot.chat_members = [113947584]
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}'
    await process_update(context, json)
    assert context.db.users == [User(id=113947584, state='nav')]


async def test_bot_start_grad(context):
    context.db.users = [
        User(113947584, START_STATE)
    ]
    context.db.grads = ['alexkuk']
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}'
    await process_update(context, json)
    assert context.db.users == [User(id=113947584, state='start'), User(id=113947584, state='grad')]


async def test_bot_start_no_grad(context):
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}'
    await process_update(context, json)
    assert context.db.users == [User(id=113947584, state='no_grad')]


#####
#   GRAD
######


async def test_bot_grad_success(context):
    context.db.users = [
        User(113947584, GRAD_STATE)
    ]
    context.bot.chat_members = [113947584]
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Что дальше"}}'
    await process_update(context, json)
    assert context.db.users == [User(id=113947584, state='grad'), User(id=113947584, state='nav')]


async def test_bot_grad_fail(context):
    context.db.users = [
        User(113947584, GRAD_STATE)
    ]
    context.bot.chat_members = []
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Что дальше"}}'
    await process_update(context, json)


#####
#   NO GRAD
######


async def test_bot_no_grad(context):
    context.db.users = [
        User(113947584, NO_GRAD_STATE)
    ]
    context.db.grads = ['alexkuk']
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Попробовать еще раз"}}'
    await process_update(context, json)
    assert context.db.users == [User(id=113947584, state='no_grad'), User(id=113947584, state='grad')]


#######
#   NAV
####


async def test_bot_nav_empty_events(context):
    context.db.users = [
        User(113947584, NAV_STATE)
    ]
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "🎉 События"}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Странно, инфы нет в базе."}']]


async def test_bot_nav_empty_contacts(context):
    context.db.users = [
        User(113947584, NAV_STATE)
    ]
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Контакты кураторов"}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Странно, инфы нет в базе."}']]


async def test_bot_nav_add_edit_event(context):
    context.db.users = [
        User(113947584, NAV_STATE)
    ]
    json = '{"update_id": 767558050, "message": {"message_id": 22, "from": {"id": 1087968824, "is_bot": true, "first_name": "Group", "username": "GroupAnonymousBot"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "text": "Событие #event #zoom 2030-08-01"}}'
    await process_update(context, json)
    assert context.db.posts == [
        Post(type='event', message_id=22, event_tag='zoom', event_date=datetime.date(2030, 8, 1)),
    ]

    json = '{"update_id": 767558051, "edited_message": {"message_id": 22, "from": {"id": 1087968824, "is_bot": true, "first_name": "Group", "username": "GroupAnonymousBot"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "edit_date": 1657879298, "text": "Событие #event #zoom 2030-09-01"}}'
    await process_update(context, json)
    assert context.db.posts == [
        Post(type='event', message_id=22, event_tag='zoom', event_date=datetime.date(2030, 8, 1)),
        Post(type='event', message_id=22, event_tag='zoom', event_date=datetime.date(2030, 9, 1))
    ]

    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "🎉 События"}}'
    await process_update(context, json)
    assert context.bot.trace == [['forwardMessage', '{"chat_id": 113947584, "from_chat_id": -1001627609834, "message_id": 22}']]


async def test_bot_nav_add_remove_contacts(context):
    context.db.users = [
        User(113947584, NAV_STATE)
    ]
    json = '{"update_id": 767558050, "message": {"message_id": 22, "from": {"id": 1087968824, "is_bot": true, "first_name": "Group", "username": "GroupAnonymousBot"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "text": "Контакты #contacts"}}'
    await process_update(context, json)
    assert context.db.posts == [Post(type='contacts', message_id=22, event_tag=None, event_date=None)]

    json = '{"update_id": 767558051, "edited_message": {"message_id": 22, "from": {"id": 1087968824, "is_bot": true, "first_name": "Group", "username": "GroupAnonymousBot"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "edit_date": 1657879298, "text": "Контакты кураторов"}}'
    await process_update(context, json)
    assert context.db.posts == []

    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Контакты кураторов"}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Странно, инфы нет в базе."}']]


async def test_bot_nav_other(context):
    context.db.users = [
        User(113947584, NAV_STATE)
    ]
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Что-то непонятное"}}'
    await process_update(context, json)
    assert context.bot.trace_methods() == ['sendMessage']
    
