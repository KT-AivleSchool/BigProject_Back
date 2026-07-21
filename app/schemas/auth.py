from pydantic import BaseModel, EmailStr, Field, field_validator
import re


class UserRegister(BaseModel):
    email: EmailStr = Field(..., description="사용자 이메일 주소 (ID 역할)")
    password: str = Field(..., description="비밀번호 (영문, 숫자, 특수문자 조합)")
    username: str = Field(..., description="실무자 이름")

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        has_eng = bool(re.search(r"[a-zA-Z]", v))
        has_num = bool(re.search(r"\d", v))
        has_spec = bool(re.search(r"[^a-zA-Z0-9]", v))

        types_count = sum([has_eng, has_num, has_spec])

        # 3종류 조합인 경우 최소 8자리 이상
        if types_count >= 3:
            if len(v) < 8:
                raise ValueError("영문, 숫자, 특수문자를 모두 포함하는 경우 최소 8자리 이상이어야 합니다.")
        # 2종류 조합인 경우 최소 10자리 이상
        elif types_count == 2:
            if len(v) < 10:
                raise ValueError("영문, 숫자, 특수문자 중 2종류를 조합하는 경우 최소 10자리 이상이어야 합니다.")
        else:
            raise ValueError("비밀번호는 영문, 숫자, 특수문자 중 최소 2종류 이상을 조합해야 합니다.")

        return v


class UserLogin(BaseModel):
    email: EmailStr = Field(..., description="사용자 이메일 주소")
    password: str = Field(..., description="비밀번호")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT Access Token")
    token_type: str = Field("bearer", description="토큰 타입")
    expires_in_minutes: int = Field(..., description="토큰 만료 시간")


class UserResponse(BaseModel):
    id: int = Field(..., description="DB 고유 식별 ID")
    email: EmailStr = Field(..., description="이메일")
    username: str = Field(..., description="이름")
    is_active: bool = Field(True, description="계정 활성화 상태")

    class Config:
        from_attributes = True
