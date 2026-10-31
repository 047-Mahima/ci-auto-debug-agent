from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Repository


def test_repository_sets_created_at_on_insert():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        repo = Repository(github_repo_id=123, full_name="octo/demo")
        session.add(repo)
        session.commit()
        session.refresh(repo)

        assert repo.created_at is not None


def test_repository_allows_missing_github_repo_id():
    repo = Repository(full_name="octo/demo")

    assert repo.full_name == "octo/demo"
    assert repo.github_repo_id is None
