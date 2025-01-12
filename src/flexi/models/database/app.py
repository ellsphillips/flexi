from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flexi.locations import database_file
from flexi.models.database.db import Base

db_engine = create_engine(f"sqlite:///{database_file().resolve()}")
Session = sessionmaker(bind=db_engine)


def init_db() -> None:
    Base.metadata.create_all(db_engine)
    session = Session()
    session.close()
