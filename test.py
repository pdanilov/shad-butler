
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

    DB,
    BotContext,

    Date,

    Post,
    find_post,
)


#######
#
#   DB
#
######


@pytest.fixture
async def db():
    db = DB()
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

    posts = await db.cached(db.read_posts)
    assert find_post(posts, message_id=post.message_id)

    db.pop_cache(db.read_posts)
    assert not db.cache

    await db.delete_post(post.message_id)
    posts = await db.read_posts()
    assert not find_post(posts, message_id=post.message_id)


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

    async def request(self, method, data):
        json = format_json(data, ensure_ascii=False)
        self.trace.append([method, json])
        return {}


class FakeDB(DB):
    def __init__(self):
        DB.__init__(self)
        self.posts = []

    async def read_posts(self):
        return self.posts

    async def put_post(self, post):
        self.posts.append(post)

    async def delete_post(self, message_id):
        self.posts = [
            _ for _ in self.posts
            if _.message_id != message_id
        ]


class FakeBotContext(BotContext):
    def __init__(self):
        self.bot = FakeBot('123:faketoken')
        self.dispatcher = Dispatcher(self.bot)
        self.db = FakeDB()


@pytest.fixture(scope='function')
def context():
    context = FakeBotContext()
    context.setup_handlers()

    Bot.set_current(context.bot)
    Dispatcher.set_current(context.dispatcher)

    return context


async def process_update(context, json):
    data = parse_json(json)
    update = Update(**data)
    await context.dispatcher.process_update(update)


async def test_bot_start(context):
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Привет, могу показать ближайшие события из чата ШАД 15+, ссылки на локальные чаты, контакты кураторов.", "reply_markup": "{\\"keyboard\\": [[{\\"text\\": \\"🎉 События\\"}, {\\"text\\": \\"Чаты городов\\"}, {\\"text\\": \\"Контакты\\"}]], \\"resize_keyboard\\": true}"}']]


async def test_bot_empty_events_button(context):
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "🎉 События"}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Странно, инфы нет в базе."}']]


async def test_bot_empty_contacts_button(context):
    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Контакты"}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Странно, инфы нет в базе."}']]


async def test_bot_add_edit_event(context):
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
    assert context.bot.trace == [
        ['forwardMessage', '{"chat_id": 113947584, "from_chat_id": -1001627609834, "message_id": 22}'],
        ['forwardMessage', '{"chat_id": 113947584, "from_chat_id": -1001627609834, "message_id": 22}']]


async def test_bot_add_remove_contacts(context):
    json = '{"update_id": 767558050, "message": {"message_id": 22, "from": {"id": 1087968824, "is_bot": true, "first_name": "Group", "username": "GroupAnonymousBot"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "text": "Контакты #contacts"}}'
    await process_update(context, json)
    assert context.db.posts == [Post(type='contacts', message_id=22, event_tag=None, event_date=None)]

    json = '{"update_id": 767558051, "edited_message": {"message_id": 22, "from": {"id": 1087968824, "is_bot": true, "first_name": "Group", "username": "GroupAnonymousBot"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "edit_date": 1657879298, "text": "Контакты"}}'
    await process_update(context, json)
    assert context.db.posts == []

    json = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "Контакты"}}'
    await process_update(context, json)
    assert context.bot.trace == [['sendMessage', '{"chat_id": 113947584, "text": "Странно, инфы нет в базе."}']]
