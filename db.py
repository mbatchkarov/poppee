from peewee import (
    SqliteDatabase,
    Model,
    IntegerField,
    CharField,
    DateTimeField,
    AutoField,
    ForeignKeyField,
    Database,
)


class User(Model):
    chat_id = IntegerField(unique=True, index=True)
    name = CharField()
    next_ping = DateTimeField(default=None)


class Pee(Model):
    id = AutoField(unique=True, index=True, primary_key=True)
    time = DateTimeField(index=True)
    user_id = ForeignKeyField(User, lazy_load=True)


MODELS = [User, Pee]


def get_db(path=":memory:") -> Database:
    db = SqliteDatabase(path)
    db.bind([User, Pee])
    db.connect()
    db.create_tables(MODELS)
    return db


def drop_all(db: Database):
    db.drop_tables(MODELS)
    db.create_tables(MODELS)
