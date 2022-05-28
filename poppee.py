import json
import datetime
import time
from threading import Thread

from peewee import fn, DoesNotExist
import pytz
import telebot
from telebot import types

from .db import Pee, User, get_db

with open(".env") as infile:
    conf = json.load(infile)

# https://github.com/eternnoir/pyTelegramBotAPI
bot = telebot.TeleBot(conf["telegram_poppee"], parse_mode=None)
PEE_INTERVAL_MINUTES = 180
NAG_INTERVAL_MINUTES = 5

CHAT_IDS_FILE = "data.sqlite"

QUICK_PEE_BUTTON = types.ReplyKeyboardMarkup(row_width=2)
QUICK_PEE_BUTTON.add(types.KeyboardButton("Snooze"))
QUICK_PEE_BUTTON.add(types.KeyboardButton("SHE PEED!"))

CLOSE_BUTTONS = types.ReplyKeyboardRemove(selective=False)


def write_pepee_time(user_id):
    pee_time = int(time.time())  # the the nearest second
    Pee(time=pee_time, user_id=user_id).save()
    User.update(
        {User.next_ping: int(time.time()) + PEE_INTERVAL_MINUTES * 60}
    ).execute()


def get_pepee_time() -> float:
    pee = Pee.select(fn.MAX(Pee.time)).scalar()
    return pee or 0  # if no pee recorded, assume it's ages ago


def subscribe(chat_id: int, username: str):
    ids = User.select()
    next_ping = (
        max(user.next_ping for user in ids)
        if ids
        else (int(time.time()) + PEE_INTERVAL_MINUTES * 60)
    )
    subscriber = User.replace(
        chat_id=chat_id, next_ping=next_ping, name=username
    ).execute()  # this does an upsert


def get_time_in_berlin():
    tz = pytz.timezone("Europe/Berlin")
    return datetime.datetime.now(tz)


def remind_iterator() -> bool:
    """Return False to indicate we should be quiet"""
    berlin_now = get_time_in_berlin()
    if berlin_now.hour >= 22 or berlin_now.hour <= 7:
        # quiet at night
        return False

    time_since_pee_s = time.time() - get_pepee_time()
    for user in User.select():
        if user.next_ping < time.time():
            bot.send_message(
                user.chat_id,
                f"Yo {user.name}, doggo needs to pee. Last pee was ~{round(time_since_pee_s / 3600, 1)}h ago. Click to mark deed as done",
                reply_markup=QUICK_PEE_BUTTON,
            )
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
    time_since_pee_s = time.time() - get_pepee_time()
    bot.send_message(
        message.chat.id,
        f"Subscribed you. Last pee was ~{round(time_since_pee_s / 3600, 1)}h ago. Interval between pees is {PEE_INTERVAL_MINUTES} minutes",
        reply_markup=QUICK_PEE_BUTTON,
    )


@bot.message_handler(commands=["help"])
def handle_help_command(message):
    bot.send_message(
        message.chat.id,
        f"Send /info to get pee status; /sub to subscribe for notifications; /unsub to unsubscribe; any other text to record pee time",
    )


@bot.message_handler(commands=["info", "status"])
def handle_info_command(message):
    time_since_pee_s = time.time() - get_pepee_time()
    bot.send_message(
        message.chat.id,
        f"Last pee was ~{round(time_since_pee_s / 3600, 1)}h ago. Interval between pees is {PEE_INTERVAL_MINUTES} minutes",
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
    # skip 4 nag intervals
    User.update(
        {User.next_ping: int(time.time()) + (4 * NAG_INTERVAL_MINUTES * 60)}
    ).execute()
    bot.send_message(
        message.chat.id,
        f"Setting remind time for {4 * NAG_INTERVAL_MINUTES} minutes from now",
        reply_markup=QUICK_PEE_BUTTON,
    )


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # print("Got message", message)
    if "snooze" in message.text.lower():
        return handle_snooze_command(message)

    this_chat_id = message.chat.id
    try:
        sub = User.get(User.chat_id == this_chat_id)
        write_pepee_time(sub.chat_id)
        User.update(
            {User.next_ping: int(time.time()) + (PEE_INTERVAL_MINUTES * 60)}
        ).execute()
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
    bot.infinity_polling()
