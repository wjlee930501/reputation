"""Alembic 0053/0054이 추가한 컬럼이 ORM 모델에도 선언돼 있는지 검증.

배경: 0053(content_items.image_policy_verified_at)과 0054(hospitals.hero_specialties,
hospitals.content_focus_topics, content_items.content_focus_topic)이 DB 컬럼을
추가했지만 대응하는 SQLAlchemy 모델 컬럼이 없었다 — `alembic revision --autogenerate`가
이 컬럼들을 "모델에 없다"고 보고 drop_column을 제안하게 된다.

`alembic upgrade head`를 인메모리 SQLite로 돌려 실제 마이그레이션 체인과 비교하는
방법은 이 프로젝트에 맞지 않는다 — 마이그레이션이 PostgreSQL 전용 ENUM/JSON 타입과
raw SQL(0054의 UPDATE 문 등)을 쓰기 때문에 SQLite에서 깨진다. 대신 이 테스트는
Base.metadata에 선언된 컬럼이 있는지, 타입/nullable이 마이그레이션과 일치하는지만
가볍게 확인한다.
"""
import sqlalchemy as sa

from app.core.database import Base
from app.models import content, hospital  # noqa: F401 — 모델 등록 트리거


def test_content_items_has_image_policy_verified_at_column():
    """migration 0053: content_items.image_policy_verified_at (DateTime(timezone=True), nullable)."""
    table = Base.metadata.tables["content_items"]
    assert "image_policy_verified_at" in table.c
    column = table.c["image_policy_verified_at"]
    assert isinstance(column.type, sa.DateTime)
    assert column.type.timezone is True
    assert column.nullable is True


def test_hospitals_has_hero_specialties_column():
    """migration 0054: hospitals.hero_specialties (JSON, NOT NULL, server_default '[]')."""
    table = Base.metadata.tables["hospitals"]
    assert "hero_specialties" in table.c
    column = table.c["hero_specialties"]
    assert isinstance(column.type, sa.JSON)
    assert column.nullable is False
    assert column.server_default is not None


def test_hospitals_has_content_focus_topics_column():
    """migration 0054: hospitals.content_focus_topics (JSON, NOT NULL, server_default '[]')."""
    table = Base.metadata.tables["hospitals"]
    assert "content_focus_topics" in table.c
    column = table.c["content_focus_topics"]
    assert isinstance(column.type, sa.JSON)
    assert column.nullable is False
    assert column.server_default is not None


def test_content_items_has_content_focus_topic_column():
    """migration 0054: content_items.content_focus_topic (String(40), nullable)."""
    table = Base.metadata.tables["content_items"]
    assert "content_focus_topic" in table.c
    column = table.c["content_focus_topic"]
    assert isinstance(column.type, sa.String)
    assert column.type.length == 40
    assert column.nullable is True
