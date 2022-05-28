import datetime

import pytest
from unittest.mock import patch
from telebot.types import Message, Chat, User as TUser

from .db import get_db, drop_all, Pee, User
from .poppee import (
    subscribe,
    handle_unsub_command,
    handle_info_command,
    write_pepee_time,
    get_pepee_time,
    handle_message,
    PEE_INTERVAL_MINUTES,
    handle_snooze_command,
    remind_iterator,
)

user = TUser(123, False, "FirstName", "LastName")
chat = Chat(456, "chat")
msg = Message(789, user, 12345689, chat, "text", {"text": "hi"}, "")


@pytest.fixture(autouse=True)
def mock_telegram():
    with patch("poppee.poppee.bot.send_message") as fake_telegram:
        yield fake_telegram


@pytest.fixture()
def empty_db():
    db = get_db()
    yield
    drop_all(db)


@pytest.fixture()
def populated_db(mock_telegram):
    db = get_db()
    User(chat_id=chat.id, name=user.first_name, next_ping=0).save()
    yield
    drop_all(db)


def test_subscribe_empty(empty_db):
    assert User.select().count() == 0
    subscribe(chat.id, user.first_name)
    assert User.select().count() == 1
    db_user = User.get(User.chat_id == chat.id)
    assert db_user.name == user.first_name
    assert db_user.next_ping > 0


def test_subscribe(populated_db):
    assert User.select().count() == 1
    subscribe(chat.id + 1, user.first_name)
    assert User.select().count() == 2
    assert User.get(User.chat_id == chat.id + 1).name == user.first_name


def test_unsubscribe_empty(empty_db, mock_telegram):
    handle_unsub_command(msg)
    assert User.select().count() == 0  # no crash
    assert mock_telegram.call_count == 1


def test_unsubscribe(populated_db):
    assert User.select().count() == 1
    handle_unsub_command(msg)
    assert User.select().count() == 0


def test_write_peepee_time(empty_db):
    assert Pee.select().count() == 0
    for time in range(5):
        with patch("time.time", return_value=time):
            write_pepee_time(chat.id)
        assert get_pepee_time() == time
    assert Pee.select().count() == 5


def test_info(populated_db):
    with patch("time.time", return_value=0):
        write_pepee_time(chat.id)

    with patch("time.time", return_value=1):
        time_since_pee = handle_info_command(msg)
    assert time_since_pee == 1


def test_record_peepee(populated_db):
    old_ping_time = User.get(User.chat_id == chat.id).next_ping
    with patch("time.time", return_value=0):
        handle_message(msg)
    new_ping_time = User.get(User.chat_id == chat.id).next_ping
    assert new_ping_time - old_ping_time == PEE_INTERVAL_MINUTES * 60


def test_record_peepee_no_subscribers(empty_db):
    handle_message(msg)  # no crash


def test_snooze(populated_db):
    old_ping_time = User.get(User.chat_id == chat.id).next_ping
    with patch("time.time", return_value=0):
        handle_snooze_command(msg)
    new_ping_time = User.get(User.chat_id == chat.id).next_ping
    assert new_ping_time > old_ping_time


def test_snooze_empty_db(empty_db):
    handle_snooze_command(msg)  # no crash


def test_remind_silent_at_night():
    fixed_now = datetime.datetime(2017, 8, 21, 23, 23, 23)
    with patch("poppee.poppee.get_time_in_berlin", return_value=fixed_now):
        assert not remind_iterator()


def test_remind_sends_messages(populated_db, mock_telegram):
    fixed_now = datetime.datetime(2017, 8, 21, 11, 11, 11)
    with patch("poppee.poppee.get_time_in_berlin", return_value=fixed_now):
        assert remind_iterator()
        assert mock_telegram.call_count == User.select().count()
        chat_id, msg_text = mock_telegram.call_args[0]
        assert chat_id == chat.id
        assert "doggo needs to pee" in msg_text
