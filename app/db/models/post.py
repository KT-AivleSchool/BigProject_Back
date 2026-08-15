from sqlalchemy import Column, Integer, String, Text, BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
# 🔴 `app.db.base` 가 아니라 `app.db.session` 에서 받는다(다른 모델 6개와 같다).
#    `base.py` 는 이 파일을 import 하는 **집합기**라, 여기서 거꾸로 `base` 를 가리키면
#    순환이 된다: `import app.db.models.post` 를 먼저 하면
#    `ImportError: cannot import name 'Post' from partially initialized module` 다.
#    지금까지 안 터진 건 `deps.py` 가 늘 `app.db.base` 를 먼저 import 해서일 뿐이다 —
#    import 순서에 기대는 코드는 부르는 자리가 하나 늘면 터진다.
from app.db.session import Base


class Post(Base):
    __tablename__ = "posts"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    file_path = Column(String(500), nullable=True)
    original_filename = Column(String(255), nullable=True)
    file_size = Column(BigInteger, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    author = relationship("User", backref="posts")
