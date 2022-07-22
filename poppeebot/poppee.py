import json
import os
import sys
import datetime
import time
from threading import Thread

from peewee import fn, DoesNotExist
from telebot import types, TeleBot

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from poppeebot.db import Pee, User, get_db

with open(".env") as infile:
    conf = json.load(infile)

# https://github.com/eternnoir/pyTelegramBotAPI
bot = TeleBot(conf["telegram_poppee"], parse_mode=None)
PEE_INTERVAL_MINUTES = 180
NAG_INTERVAL_MINUTES = 10

CHAT_IDS_FILE = "data.sqlite"

SNOOZE_TEXT = "Snooze"
YOUR_DOG_2H_TEXT = "Your dog (2h)"
YOUR_DOG_8H_TEXT = "Your dog (8h)"
QUICK_PEE_BUTTON = types.ReplyKeyboardMarkup(row_width=2)
QUICK_PEE_BUTTON.row(
    types.KeyboardButton(YOUR_DOG_2H_TEXT),
    types.KeyboardButton(YOUR_DOG_8H_TEXT),
    types.KeyboardButton(SNOOZE_TEXT),
)
QUICK_PEE_BUTTON.row(types.KeyboardButton("SHE PEED!"))

CLOSE_BUTTONS = types.ReplyKeyboardRemove(selective=False)

NOTIF_START = (8, 30)
NOTIF_END = (22, 30)


def write_pepee_time(user_id):
    Pee(time=time_now(), user_id=user_id).save()


def get_last_pee_time() -> float:
    pee = Pee.select(fn.MAX(Pee.time)).scalar()
    return pee or 0  # if no pee recorded, assume it's ages ago


def subscribe(chat_id: int, username: str):
    ids = User.select()
    num_users = User.select().count()
    if num_users >= 2:
        raise ValueError('too many users')
    next_ping = max(user.next_ping for user in ids) if ids else (time_now() + PEE_INTERVAL_MINUTES * 60)
    User.replace(chat_id=chat_id, next_ping=next_ping, name=username).execute()  # this does an upsert


def get_time_in_berlin():
    timezone = "Europe/Berlin"
    try:
        # py < 3.9
        import pytz

        return datetime.datetime.now(pytz.timezone(timezone))
    except ImportError:
        from zoneinfo import ZoneInfo

        return datetime.datetime.now(ZoneInfo(timezone))


def remind_iterator() -> bool:
    """Return False to indicate we should be quiet"""
    berlin_now = get_time_in_berlin()
    hour_minute = (berlin_now.hour, berlin_now.minute)
    if NOTIF_END <= hour_minute or NOTIF_START >= hour_minute:
        # quiet at night
        return False
    time_since_pee_s = time_now() - get_last_pee_time()
    for user in User.select():
        if user.next_ping < time_now():
            message = (
                f"Yo {user.name}, doggo needs to pee. Last pee was ~{round(time_since_pee_s / 3600, 1)}h "
                f"ago. Click to mark deed as done"
            )
            bot.send_message(user.chat_id, message, reply_markup=QUICK_PEE_BUTTON)
    return True


def remind():
    print("Starting reminder thread...")
    while True:
        time.sleep(60 * NAG_INTERVAL_MINUTES)
        if not remind_iterator():
            continue


@bot.message_handler(commands=["start", "sub"])
def handle_sub_command(message):
    subscribe(message.chat.id, message.chat.first_name)
    time_since_pee_s = time_now() - get_last_pee_time()
    bot.send_message(
        message.chat.id,
        f"Subscribed you. Last pee was ~{round(time_since_pee_s / 3600, 1)}h "
        f"ago. Interval between pees is {PEE_INTERVAL_MINUTES} minutes",
        reply_markup=QUICK_PEE_BUTTON,
    )


@bot.message_handler(commands=["help"])
def handle_help_command(message):
    bot.send_message(
        message.chat.id,
        "Send /info to get pee status; /sub to subscribe for notifications; "
        "/unsub to unsubscribe; any other text to record pee time",
    )


@bot.message_handler(commands=["info", "status"])
def handle_info_command(message):
    time_since_pee_s = time_now() - get_last_pee_time()
    bot.send_message(
        message.chat.id,
        f"Last pee was ~{round(time_since_pee_s / 3600, 1)}h ago. Interval between pees is {PEE_INTERVAL_MINUTES} minutes",
        reply_markup=QUICK_PEE_BUTTON,
    )
    return time_since_pee_s


@bot.message_handler(commands=["stop", "unsub", "unsubscribe"])
def handle_unsub_command(message):
    User.delete().where(User.chat_id == message.chat.id).execute()
    bot.send_message(
        message.chat.id,
        f"Unsubscribed you. {User.select().count()} subscribers left",
        reply_markup=CLOSE_BUTTONS,
    )


@bot.message_handler(commands=["snooze"])
def handle_snooze_command(message):
    """Skip 4 nag intervals for EVERYONE, or more if user is currently not watching the dog"""
    quiet_time = 4 * NAG_INTERVAL_MINUTES
    quiet_time_for_this_user = quiet_time
    for u in User.select():
        u.next_ping = max(u.next_ping, time_now() + (quiet_time * 60))
        u.save()
        if u.chat_id == message.chat.id:
            quiet_time_for_this_user = (u.next_ping - time_now()) // 60

    bot.send_message(
        message.chat.id,
        f"Snoozing for {quiet_time_for_this_user} minutes",
        reply_markup=QUICK_PEE_BUTTON,
    )


@bot.message_handler(commands=["yourdog"])
def handle_your_dog_command(message):
    """Don't nag sender of message for N hours, depending on message text"""
    # when she should really pee by regardless of who's on duty
    next_pee = get_last_pee_time() + (PEE_INTERVAL_MINUTES * 60)
    # is anyone going to be messaged shortly after the time she is due
    num_watchers = User.select().where(User.next_ping <= next_pee).count()

    if num_watchers > 1:
        # this user can leave the dog to some other user
        snooze_h = 2 if message.text == YOUR_DOG_2H_TEXT else 8
        num_updated = (
            User.update({User.next_ping: time_now() + (snooze_h * 60 * 60)}).where(User.chat_id == message.chat.id).execute()
        )
        assert num_updated == 1
        bot.send_message(
            message.chat.id,
            f"Dog handed over for {snooze_h} hours",
            reply_markup=QUICK_PEE_BUTTON,
        )

        # tell all other users it's their job now
        for other_user in User.select().where(User.chat_id != message.chat.id):
            bot.send_message(
                other_user.chat_id,
                f"Dog is yours for {snooze_h} hours",
                reply_markup=QUICK_PEE_BUTTON,
            )
    else:
        # doggo can't be unsupervised, all users' timer is reset to be safe
        earliest_ping = User.select(fn.MIN(User.next_ping)).scalar() or 0
        User.update({User.next_ping: earliest_ping}).execute()
        time_to_next_ping = round((earliest_ping - time_now()) / 3600, 2)
        bot.send_message(
            message.chat.id,
            f"Someone has to watch her! Setting next ping in ~{time_to_next_ping}h",
            reply_markup=QUICK_PEE_BUTTON,
        )


def time_now() -> int:
    """Returns time now in seconds since unix epoch. Extracted to a function for easy mocking"""
    return int(time.time())


def modify_time_now(**kwargs) -> int:
    """Like time_now() but with some fields (hours, minute, year, etc) modified"""
    return int(datetime.datetime.fromtimestamp(time_now(), tz=datetime.timezone.utc).replace(**kwargs).timestamp())


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    if SNOOZE_TEXT in message.text:
        return handle_snooze_command(message)
    elif message.text in (YOUR_DOG_2H_TEXT, YOUR_DOG_8H_TEXT):
        return handle_your_dog_command(message)

    this_chat_id = message.chat.id
    try:
        sub = User.get(User.chat_id == this_chat_id)
        write_pepee_time(sub.chat_id)

        # when she should really pee by regardless of who's on duty
        next_pee: int = min(
            [
                # the usual pee logic
                time_now() + (PEE_INTERVAL_MINUTES * 60),
                # ensure she pees before bed. notify early enough that we get at least 2 nags before quiet time
                modify_time_now(hour=NOTIF_END[0], minute=NOTIF_END[1] - NAG_INTERVAL_MINUTES - 5),
            ]
        )
        for u in User.select():
            u.next_ping = max(next_pee, u.next_ping)
            u.save()
        bot.send_message(
            message.chat.id,
            "Gotcha, recording pee time now",
            reply_markup=QUICK_PEE_BUTTON,
        )
    except DoesNotExist:
        bot.send_message(
            message.chat.id,
            "You cannot update pee time, subscribe for notifications first",
        )


if __name__ == "__main__":
    get_db(CHAT_IDS_FILE)
    Thread(target=remind, daemon=True).start()
    print("Starting server...")
    bot.infinity_polling(timeout=20, long_polling_timeout=20)
