from sqlmodel import SQLModel, Session, create_engine
from remembrance.core.settings import settings

engine = create_engine(settings.DATABASE_URL, echo=False)

def init_db():
    from remembrance.models import tables  # noqa
    SQLModel.metadata.create_all(engine)

def get_session() -> Session:
    return Session(engine)
