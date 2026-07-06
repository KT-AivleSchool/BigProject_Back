from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

router = APIRouter()

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    username: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(user: UserRegister):
    """
    [Cj(찬진) 파트] 신규 구정 관리자 및 실무자 회원가입
    """
    # 임시 mock 구현
    return {
        "status": "success",
        "message": f"User {user.username} registered successfully.",
        "user_email": user.email
    }

@router.post("/login")
def login_user(credentials: UserLogin):
    """
    [Cj(찬진) 파트] JWT 발급을 통한 구정 실무자 로그인 인증
    """
    # 임시 mock 토큰 발행
    if credentials.email == "admin@yongsan.go.kr" and credentials.password == "admin123!":
        return {
            "access_token": "mock_jwt_token_for_omnisite_development",
            "token_type": "bearer",
            "expires_in_minutes": 1440
        }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="이메일 혹은 패스워드가 올바르지 않습니다."
    )
