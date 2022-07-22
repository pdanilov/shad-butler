
import asyncio
import datetime
from json import (
    loads as parse_json,
    dumps as format_json
)

import pytest

from aiogram.types import (
    Update,
    ChatMember
)

from main import (
    Bot,
    Dispatcher,
    BadRequest,
    ChatMemberStatus,

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


# have to define event_loop fixture, to use db fixture with
# scope=session. No idea why
# https://github.com/tortoise/tortoise-orm/issues/638


@pytest.fixture(scope='session')
def event_loop():
    return asyncio.get_event_loop()


@pytest.fixture(scope='session')
async def db():
    db = DB()
    await db.connect()
    yield db
    await db.close()


async def test_db_posts(db):
    post = Post(
        type='test',
        message_id=-1,
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


#####
#
#   BOT
#
#####


# Mock bot, do not send requests to Telegram server, just store in
# trace array. Almost never use Telegram response anyway. Except for
# forwardMessage, getChatMember


class FakeBot(Bot):
    def __init__(self, token):
        Bot.__init__(self, token)
        self.trace = []
        self.chat_members = []
        self.chat_messages = []
        
    async def request(self, method, data):
        json = format_json(data, ensure_ascii=False)
        self.trace.append([method, json])
        return {}

    async def forward_message(self, chat_id, from_chat_id, message_id):
        data = dict(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)
        await self.request('forwardMessage', data)

        if message_id not in self.chat_messages:
            raise BadRequest.detect('Message to forward not found')

    async def get_chat_member(self, chat_id, user_id):
        data = dict(chat_id=chat_id, user_id=user_id)
        await self.request('getChatMember', data)

        if user_id not in self.chat_members:
            raise BadRequest.detect('User not found')
        
        return ChatMember(
            status=ChatMemberStatus.MEMBER
        )


# Mock db, just put/delete items in array. Test actual Dynamo in
# test_db_* tests.


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
    context.setup_middlewares()

    Bot.set_current(context.bot)
    Dispatcher.set_current(context.dispatcher)

    return context


async def process_update(context, json):
    data = parse_json(json)
    update = Update(**data)
    await context.dispatcher.process_update(update)


def match_trace(trace, etalon):
    if len(trace) != len(etalon):
        return False

    for (method, json), (etalon_method, etalon_match) in zip(trace, etalon):
        if method != etalon_method:
            return False

        if etalon_match not in json:
            return False

    return True


#######
#   START
######


START_JSON = '{"update_id": 767558049, "message": {"message_id": 303, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "chat": {"id": 113947584, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "type": "private"}, "date": 1657879247, "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}'


async def test_bot_start_wip(context):
    await process_update(context, START_JSON.replace('alexkuk', 'abc'))
    assert match_trace(context.bot.trace, [
        ['sendMessage', '{"chat_id": 113947584, "text": "Бот пока отвечает только разработ']
    ])


async def test_bot_start_not_chat_member(context):
    await process_update(context, START_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['sendMessage', '{"chat_id": 113947584, "text": "Не нашел тебя в чате выпускников']
    ])


async def test_bot_start_check_chat_member(context):
    context.bot.chat_members = [113947584]
    await process_update(context, START_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['sendMessage', '{"chat_id": 113947584, "text": "Что может делать этот бот'],
        ['setMyCommands', '{"commands": "[{\\"command\\": \\"future']
    ])


async def test_bot_start_is_chat_member(context):
    context.bot.chat_members = [113947584]
    await process_update(context, START_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['sendMessage', '{"chat_id": 113947584, "text": "Что может делать этот бот'],
        ['setMyCommands', '{"commands": ']
    ])


######
#  OTHER
#######


async def test_bot_other(context):
    context.bot.chat_members = [113947584]
    await process_update(context, START_JSON.replace('/start', 'hiii'))
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['sendMessage', '{"chat_id": 113947584, "text": "Что может делать этот бот'],
    ])


#######
#   EVENTS
####


EVENTS_JSON = START_JSON.replace('/start', '/future_events')


async def test_bot_events_missing(context):
    context.bot.chat_members = [113947584]
    await process_update(context, EVENTS_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['sendMessage', '"text": "Не нашел постов с тегом #event."}']
    ])


async def test_bot_events_no(context):
    context.bot.chat_members = [113947584]
    context.db.posts = [
        Post(type='event', message_id=22, event_date=datetime.date(2020, 8, 1))
    ]
    context.bot.chat_messages = [22]
    await process_update(context, EVENTS_JSON)
    assert match_trace(context.bot.trace,[
        ['getChatMember', '{"chat_id"'],
        ['sendMessage', '{"chat_id": 113947584, "text": "В ближайшее время нет эвентов']
    ])


async def test_bot_events_select(context):
    context.bot.chat_members = [113947584]
    context.bot.chat_messages = [22, 23, 24]
    context.db.posts = [
        Post(type='event', message_id=22, event_date=datetime.date(2020, 8, 1)),
        Post(type='event', message_id=23, event_date=datetime.date(2030, 8, 1)),
        Post(type='event', message_id=24, event_date=datetime.date(2030, 8, 1)),
    ]
    await process_update(context, EVENTS_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['forwardMessage', '"message_id": 23}'],
        ['forwardMessage', '"message_id": 24}'],
    ])


#######
#   NAV
#####


NAV_JSON = START_JSON.replace('/start', '/chats')


async def test_bot_nav_missing(context):
    context.bot.chat_members = [113947584]
    await process_update(context, NAV_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['sendMessage', '"text": "Не нашел постов с тегом #chats."}']
    ])


async def test_bot_nav_ok(context):
    context.bot.chat_members = [113947584]
    context.bot.chat_messages = [22]
    context.db.posts = [
        Post(type='chats', message_id=22),
    ]
    await process_update(context, NAV_JSON)
    assert match_trace(context.bot.trace, [
        ['getChatMember', '{"chat_id":'],
        ['forwardMessage', '"message_id": 22}'],
    ])



########
#   CHAT
#####


async def test_bot_chat_add_remove_footer(context):
    json = '{"update_id": 767558050, "message": {"message_id": 22, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "text": "Событие #event 2030-08-01"}}'
    await process_update(context, json)
    assert context.db.posts == [
        Post(message_id=22, type='event', event_date=datetime.date(2030, 8, 1))
    ]

    json = '{"update_id": 767558051, "edited_message": {"message_id": 22, "from": {"id": 113947584, "is_bot": false, "first_name": "Alexander", "last_name": "Kukushkin", "username": "alexkuk", "language_code": "ru"}, "sender_chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "chat": {"id": -1001627609834, "title": "shad15_bot_test_chat", "type": "supergroup"}, "date": 1657879275, "edit_date": 1657879298, "text": "Событие"}}'
    await process_update(context, json)
    assert context.db.posts == []
