from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url):
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 60})

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine(settings.database_url)
Session = sessionmaker(engine, expire_on_commit=False)
