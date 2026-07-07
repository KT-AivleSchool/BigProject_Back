from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.db.base import User
from utils.auth_utils import create_access_token
import bcrypt

router = APIRouter()

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    username: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(user: UserRegister, db:Session = Depends(get_db)):
    """
    [Cj(찬진) 파트] 신규 구정 관리자 및 실무자 회원가입
    """

    # email/ID 중복 검사
    existing_user = db.query(User).filter(
        (User.email == user.email)
    ).first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='이미 존재하는 이메일입니다.'
        )
    
    hashed_pw = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    new_user = User(
        email=user.email,
        hashed_password=hashed_pw,
        username=user.username
    )
    
    try:
        db.add(new_user)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DB injection error"
        )

    resp = {
        "status": "success",
        "message": f"User {user.username} registered successfully.",
        "user_email": user.email
    }

    return resp

@router.post("/login")
def login_user(credentials: UserLogin, db: Session = Depends(get_db)):
    """
    [Cj(찬진) 파트] JWT 발급을 통한 구정 실무자 로그인 인증
    """

    user = db.query(User).filter(User.email == credentials.email).first()

    print("입력 이메일:", credentials.email)
    print("입력 비밀번호:", repr(credentials.password))
    print("DB 해시:", user.hashed_password)

    print(
        bcrypt.checkpw(
            credentials.password.encode("utf-8"),
            user.hashed_password.encode("utf-8")
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 혹은 패스워드가 올바르지 않습니다."
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다."
        )
    
    is_password_correct = bcrypt.checkpw(
        credentials.password.encode('utf-8'),
        user.hashed_password.encode('utf-8')
    )

    if not is_password_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 혹은 패스워드가 올바르지 않습니다."
        )
    
    token_payload = {
        "sub": user.email,        # 토큰 식별자 (주로 이메일 또는 고유 ID)
        "username": user.username,
        "user_id": user.id,   
    }
    token = create_access_token(token_payload)    

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES
    }
