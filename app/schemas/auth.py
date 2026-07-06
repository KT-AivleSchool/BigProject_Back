from pydantic import BaseModel, EmailStr, Field

class UserRegister(BaseModel):
    email: EmailStr = Field(..., description="사용자 이메일 주소 (ID 역할)")
    password: str = Field(..., min_length=6, description="비밀번호 (최소 6자 이상)")
    username: str = Field(..., description="실무자 이름")

class UserLogin(BaseModel):
    email: EmailStr = Field(..., description="사용자 이메일 주소")
    password: str = Field(..., description="비밀번호")

class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT Access Token")
    token_type: str = Field("bearer", description="토큰 타입")

class UserResponse(BaseModel):
    id: int = Field(..., description="DB 고유 식별 ID")
    email: EmailStr = Field(..., description="이메일")
    username: str = Field(..., description="이름")
    is_active: bool = Field(True, description="계정 활성화 상태")

    class Config:
        from_attributes = True
