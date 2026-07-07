from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.config import settings
from app.db.base import User
from utils.auth_utils import create_access_token
from app.schemas.auth import UserRegister, UserLogin, TokenResponse, UserResponse
import bcrypt

router = APIRouter()

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_user(user: UserRegister, db: AsyncSession = Depends(get_db)):
    """
    [Cj(찬진) 파트] 신규 구정 관리자 및 실무자 회원가입
    """

    # email/ID 중복 검사
    stmt = select(User).where(User.email == user.email)
    result = await db.execute(stmt)
    existing_user = result.scalars().first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='이미 존재하는 이메일입니다.'
        )

    # 비밀번호 암호화
    hashed_pw = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    # 회원정보 생성
    new_user = User(
        email=user.email,
        hashed_password=hashed_pw,
        username=user.username
    )
    
    # DB에 저장
    try:
        db.add(new_user)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DB injection error"
        )

    # 응답값 생성
    resp = {
        "status": "success",
        "message": f"User {user.username} registered successfully.",
        "user_email": user.email
    }

    return resp

@router.post("/login")
async def login_user(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """
    [Cj(찬진) 파트] JWT 발급을 통한 구정 실무자 로그인 인증
    """

    # email ID 기분으로 유저를 쿼리
    stmt = select(User).where(User.email == credentials.email)
    result = await db.execute(stmt)
    user = result.scalars().first()

    # 유저 조회 결과
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 혹은 패스워드가 올바르지 않습니다."
        )

    #비활성 유저 체크
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다."
        )
    
    # 비밀번호 검사 플래그
    is_password_correct = bcrypt.checkpw(
        credentials.password.encode('utf-8'),
        user.hashed_password.encode('utf-8')
    )

    if not is_password_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 혹은 패스워드가 올바르지 않습니다."
        )
    
    # 토큰 식별자
    token_payload = {
        "sub": user.email,        # 토큰 식별자 (주로 이메일 또는 고유 ID)
        "username": user.username,
        "user_id": user.id,   
    }
    token = create_access_token(token_payload)    # 토큰 생성 

    # 응답값 생성
    resp = {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES
    }

    return resp

