import json
import datetime
import time
from threading import Lock, Thread

import pytz
import telebot
from pyexpat.errors import messages
from telebot import types

with open(".env") as infile:
    conf = json.load(infile)

lock = Lock()

# https://github.com/eternnoir/pyTelegramBotAPI
bot = telebot.TeleBot(
    conf["telegram_poppee"], parse_mode=None
)  # You can set parse_mode by default. HTML or MARKDOWN
PEE_INTERVAL_MINUTES = 180
NAG_INTERVAL_MINUTES = 5

CHAT_IDS_FILE = "data.sqlite"

QUICK_PEE_BUTTON = types.ReplyKeyboardMarkup(row_width=2)
QUICK_PEE_BUTTON.add(types.KeyboardButton("Snooze"))
QUICK_PEE_BUTTON.add(types.KeyboardButton("SHE PEED!"))

CLOSE_BUTTONS = types.ReplyKeyboardRemove(selective=False)

from peewee import (
    SqliteDatabase,
    Model,
    IntegerField,
    CharField,
    DateTimeField,
    AutoField,
    ForeignKeyField,
    fn,
)
import datetime


STATE = SqliteDatabase(CHAT_IDS_FILE)


class BaseModel(Model):
    class Meta:
        database = STATE


class User(BaseModel):
    chat_id = IntegerField(unique=True, index=True)
    name = CharField()
    next_ping = DateTimeField(default=None)


class Pee(BaseModel):
    id = AutoField(unique=True, index=True, primary_key=True)
    time = DateTimeField(index=True)
    user_id = ForeignKeyField(User, lazy_load=True)


STATE.connect()
STATE.create_tables([User, Pee])


def write_pepee_time(user_id):
    pee_time = int(time.time())  # the the nearest second
    Pee(time=pee_time, user_id=user_id).save()
    for user in User.select():
        user.next_ping = int(time.time()) + PEE_INTERVAL_MINUTES * 60
        user.save()


def get_pepee_time() -> float:
    pee = Pee.select(fn.MAX(Pee.time)).scalar()
    return pee or 0  # if no pee recorded, assume it's ages ago


def subscribe(chat_id, username) -> dict:
    ids = User.select()
    next_ping = (
        max(user.next_ping for user in ids)
        if ids
        else (int(time.time()) + PEE_INTERVAL_MINUTES * 60)
    )
    subscriber = User.replace(
        chat_id=chat_id, next_ping=next_ping, name=username
    ).execute()
    # subscriber.save()


def remind():
    print("Starting reminder thread...")
    while True:
        time.sleep(60 * NAG_INTERVAL_MINUTES)

        tz = pytz.timezone("Europe/Berlin")
        berlin_now = datetime.datetime.now(tz)
        if berlin_now.hour >= 22 or berlin_now.hour <= 7:
            # quiet at night
            continue

        time_since_pee_s = time.time() - get_pepee_time()
        for user in User.select():
            if time.time() > user.next_ping:
                bot.send_message(
                    user.chat_id,
                    f"Yo {user.name}, doggo needs to pee. Last pee was ~{round(time_since_pee_s / 3600, 1)}h ago. Click to mark deed as done",
                    reply_markup=QUICK_PEE_BUTTON,
                )


@bot.message_handler(commands=["start", "sub"])
def handle_sub_command(message):
    with lock:
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
    for user in User.select():
        user.next_ping = int(time.time()) + (
            4 * NAG_INTERVAL_MINUTES * 60
        )  # skip 4 nag intervals
        user.save()
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
    sub = User.get(User.chat_id == this_chat_id)
    if sub:  # we have a subscriber
        write_pepee_time(sub.chat_id)
        for user in User.select():
            user.next_ping = int(time.time()) + (PEE_INTERVAL_MINUTES * 60)
            user.save()
        bot.send_message(
            message.chat.id,
            "Gotcha, recording pee time now",
            reply_markup=QUICK_PEE_BUTTON,
        )
    else:
        bot.send_message(
            message.chat.id,
            "You cannot update pee time, subscribe for notifications first",
        )

    # started working on snoozing but can't be fucked now...
    # numbers = re.findall(r'\d+\.?\d?', message.text)
    # if numbers:
    #     if numbers[0] > 3: # assume they mean minutes, otherwise hours
    #         new_duration = int(numbers[0])
    # else:


if __name__ == "__main__":
    Thread(target=remind, daemon=True).start()
    print("Starting server...")
    bot.infinity_polling()
