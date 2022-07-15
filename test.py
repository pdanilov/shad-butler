
import pytest

from main import (
    DB,
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

