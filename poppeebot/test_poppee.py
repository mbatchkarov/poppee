import datetime
from unittest.mock import patch

import pytest
from telebot.types import Message, Chat, User as TUser

from poppeebot.db import get_db, drop_all, Pee, User
from poppeebot.poppee import (
    subscribe,
    handle_unsub_command,
    handle_info_command,
    write_pepee_time,
    get_last_pee_time,
    handle_message,
    handle_your_dog_command,
    PEE_INTERVAL_MINUTES,
    YOUR_DOG_8H_TEXT,
    handle_snooze_command,
    remind_iterator,
)


def get_user(id=0):
    return TUser(123 + id, False, "FirstName", "LastName")


def get_chat(id=0):
    return Chat(456 + id, "chat")


def get_msg(id=0, text="hi") -> Message:
    return Message(
        789 + id, get_user(id), 12345689, get_chat(id), "text", {"text": text}, ""
    )


chat = get_chat()
user = get_user()
pee_msg = get_msg()
your_dog_msg = get_msg(text=YOUR_DOG_8H_TEXT)


@pytest.fixture(autouse=True)
def mock_telegram():
    with patch("poppeebot.poppee.bot.send_message") as fake_telegram:
        yield fake_telegram


@pytest.fixture()
def empty_db():
    db = get_db()
    yield db
    drop_all(db)


@pytest.fixture()
def db_one_user(mock_telegram):
    db = get_db()
    User(chat_id=chat.id, name=user.first_name, next_ping=0).save()
    yield db
    drop_all(db)


@pytest.fixture()
def db_two_users(mock_telegram):
    db = get_db()
    User(chat_id=chat.id, name=user.first_name, next_ping=0).save()
    User(chat_id=chat.id + 1, name=user.last_name, next_ping=0).save()
    yield db
    drop_all(db)


def test_subscribe_empty(empty_db):
    assert User.select().count() == 0
    subscribe(chat.id, user.first_name)
    assert User.select().count() == 1
    db_user = User.get(User.chat_id == chat.id)
    assert db_user.name == user.first_name
    assert db_user.next_ping > 0


def test_subscribe(db_one_user):
    assert User.select().count() == 1
    subscribe(chat.id + 1, user.first_name)
    assert User.select().count() == 2
    assert User.get(User.chat_id == chat.id + 1).name == user.first_name


def test_unsubscribe_empty(empty_db, mock_telegram):
    handle_unsub_command(pee_msg)
    assert User.select().count() == 0  # no crash
    assert mock_telegram.call_count == 1


def test_unsubscribe(db_one_user):
    assert User.select().count() == 1
    handle_unsub_command(pee_msg)
    assert User.select().count() == 0


def test_write_peepee_time(empty_db):
    assert Pee.select().count() == 0
    for time in range(5):
        with patch("time.time", return_value=time):
            write_pepee_time(chat.id)
        assert get_last_pee_time() == time
    assert Pee.select().count() == 5


def test_info(db_one_user):
    with patch("time.time", return_value=0):
        write_pepee_time(chat.id)

    with patch("time.time", return_value=1):
        time_since_pee = handle_info_command(pee_msg)
    assert time_since_pee == 1


def test_record_peepee(db_one_user):
    old_ping_time = User.get(User.chat_id == chat.id).next_ping
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
    new_ping_time = User.get(User.chat_id == chat.id).next_ping
    assert new_ping_time - old_ping_time == PEE_INTERVAL_MINUTES * 60


def test_record_peepee_no_subscribers(empty_db):
    handle_message(pee_msg)  # no crash


def test_snooze(db_one_user):
    old_ping_time = User.get(User.chat_id == chat.id).next_ping_hours
    with patch("time.time", return_value=0):
        handle_snooze_command(pee_msg)
    new_ping_time = User.get(User.chat_id == chat.id).next_ping_hours
    assert 1 > new_ping_time > old_ping_time


def test_snooze_empty_db(empty_db):
    handle_snooze_command(pee_msg)  # no crash


@pytest.mark.parametrize(
    "hour, minutes", [(22, 30), (0, 0), (1, 10), (5, 59), (7, 10), (8, 29)]
)
def test_remind_silent_at_night(hour, minutes):
    fixed_now = datetime.datetime(2017, 8, 21, hour, minutes, 23)
    with patch("poppeebot.poppee.get_time_in_berlin", return_value=fixed_now):
        assert not remind_iterator()


def test_remind_sends_messages(db_one_user, mock_telegram):
    fixed_now = datetime.datetime(2017, 8, 21, 11, 11, 11)
    with patch("poppeebot.poppee.get_time_in_berlin", return_value=fixed_now):
        assert remind_iterator()
        assert mock_telegram.call_count == User.select().count()
        chat_id, msg_text = mock_telegram.call_args[0]
        assert chat_id == chat.id
        assert "doggo needs to pee" in msg_text


def test_your_dog_basic(db_two_users, mock_telegram):
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
        handle_your_dog_command(your_dog_msg)
    assert mock_telegram.call_count == 3

    # first user hands over the dog
    chat_id, txt = mock_telegram.call_args_list[1][0]
    assert chat_id == chat.id
    assert "Dog handed over" in txt

    # second is told she's now their problem
    chat_id, txt = mock_telegram.call_args_list[2][0]
    assert chat_id == chat.id + 1
    assert "Dog is yours" in txt

    # notification time incremented for user who sent message
    assert User.get_by_id(1).next_ping > User.get_by_id(2).next_ping


def test_your_dog_sets_reminder(db_two_users, mock_telegram):
    """
    Check this works:
    1) doggy pees at 00:00
        a) doggy now has to pee at 03:00
    2) at 02:00, user0 says YOUR DOG FOR 8 HOURS
        b) user1's notification time remains 03:00
        c) user0's notification time is at least 10:00
    3) at 04:00 doggy pees
        d) user1's notification time becomes 07:00
        e) user0's notification time remains at least 10:00
    4) doggy pees at 09:00
        f) both users' notification time is now 12:00

    """
    # action 1)
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
    # outcome a)
    for u in User.select():
        assert u.next_ping == PEE_INTERVAL_MINUTES * 60

    # action 2)
    with patch("time.time", return_value=2 * 60 * 60):
        handle_your_dog_command(your_dog_msg)
    # outcomes b) and c)
    assert User.get_by_id(2).next_ping_hours == 3
    assert User.get_by_id(1).next_ping_hours >= 10

    # action 3)
    with patch("time.time", return_value=4 * 60 * 60):
        handle_message(pee_msg)
    # outcomes d) and e)
    assert User.get_by_id(2).next_ping_hours == 7
    assert User.get_by_id(1).next_ping_hours >= 10

    # action 4)
    with patch("time.time", return_value=9 * 60 * 60):
        handle_message(pee_msg)
    # outcome f)
    assert User.get_by_id(1).next_ping_hours >= 12
    assert User.get_by_id(2).next_ping_hours >= 12


def test_snooze_with_your_dog(db_two_users):
    """User1's dog, it's time to pee, they snooze. The other user's notif time is NOT changed"""
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
        handle_message(your_dog_msg)

    with patch("time.time", return_value=4 * 60 * 60):
        handle_snooze_command(get_msg(id=1))

    assert 4 < User.get_by_id(2).next_ping_hours < 5
    assert User.get_by_id(1).next_ping_hours >= 8


def test_both_say_your_dog(db_two_users, mock_telegram):
    """If all users say it's NOT their dog, it's everyone's dog. Someone has to watch her..."""
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
        handle_message(get_msg(text=YOUR_DOG_8H_TEXT, id=0))

    with patch("time.time", return_value=1 * 60 * 60):
        handle_message(get_msg(text=YOUR_DOG_8H_TEXT, id=1))
    for u in User.select():
        assert u.next_ping_hours == 3

    exp = [
        "recording pee",
        "Dog handed over",
        "Dog is yours",
        "Someone has to watch her",
    ]
    for sent_msg, expected in zip(mock_telegram.call_args_list, exp):
        chat_id, msg_text = sent_msg[0]
        assert expected in msg_text


def test_snooze_when_one_user(db_one_user):
    """The one and only caretaker can't delegate to anyone else"""
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
        handle_message(get_msg(text=YOUR_DOG_8H_TEXT, id=0))
    assert User.get_by_id(1).next_ping_hours == 3


def test_snooze_when_not_my_dog(db_two_users, mock_telegram):
    """Snoozing does nothing when someone else is looking after the dog"""
    with patch("time.time", return_value=0):
        handle_message(pee_msg)
        handle_message(get_msg(text=YOUR_DOG_8H_TEXT, id=0))
        assert User.get_by_id(1).next_ping_hours == 8
        assert User.get_by_id(2).next_ping_hours == 3
        handle_snooze_command(pee_msg)
    assert User.get_by_id(1).next_ping_hours == 8
    assert User.get_by_id(2).next_ping_hours == 3

    chat_id, msg_text = mock_telegram.call_args_list[-1][0]
    assert msg_text == "Snoozing for 480 minutes"  # 8 hours
