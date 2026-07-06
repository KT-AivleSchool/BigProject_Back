from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas.auth import UserRegister, UserLogin, TokenResponse, UserResponse

router = APIRouter()

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(user: UserRegister):
    """
    [Cj(찬진) 파트] 신규 구정 관리자 및 실무자 회원가입
    """
    # 임시 mock 구현 (실제론 DB session 연동 및 패스워드 bcrypt 해싱)
    return {
        "id": 1,
        "email": user.email,
        "username": user.username,
        "is_active": True
    }

@router.post("/login", response_model=TokenResponse)
def login_user(credentials: UserLogin):
    """
    [Cj(찬진) 파트] JWT 발급을 통한 구정 실무자 로그인 인증
    """
    # 임시 mock 토큰 발행
    if credentials.email == "admin@yongsan.go.kr" and credentials.password == "admin123!":
        return {
            "access_token": "mock_jwt_token_for_omnisite_development",
            "token_type": "bearer"
        }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="이메일 혹은 패스워드가 올바르지 않습니다."
    )

