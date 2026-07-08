from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import asyncio

from app.api.deps import get_db
from app.config import settings
from app.db.base import User
from app.utils.auth_utils import create_access_token
from app.schemas.auth import UserRegister, UserLogin, TokenResponse, UserResponse
import bcrypt

router = APIRouter()

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
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

    # 비밀번호 암호화 (CPU-bound bcrypt 작업을 스레드 풀로 격리하여 이벤트 루프 블로킹 방지)
    salt = await asyncio.to_thread(bcrypt.gensalt)
    hashed_pw_bytes = await asyncio.to_thread(
        bcrypt.hashpw,
        user.password.encode('utf-8'),
        salt
    )
    hashed_pw = hashed_pw_bytes.decode('utf-8')
    
    # 회원정보 생성
    new_user = User(
        email=user.email,
        hashed_password=hashed_pw,
        username=user.username
    )
    
    # DB에 저장
    # Note: get_db의 의존성 주입 생명주기(try-except-finally)에서 예외 발생 시 자동으로 
    # session.rollback() 및 session.close()가 호출되므로 커넥션 누수가 원천적으로 방지됩니다.
    try:
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="회원가입 처리 중 데이터베이스 오류가 발생했습니다."
        )

    return new_user

@router.post("/login", response_model=TokenResponse)
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
    
    # 비밀번호 검사 (CPU-bound bcrypt 작업을 스레드 풀로 격리하여 이벤트 루프 블로킹 방지)
    is_password_correct = await asyncio.to_thread(
        bcrypt.checkpw,
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


