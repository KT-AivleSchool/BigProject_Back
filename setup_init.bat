@echo off
chcp 65001 > nul
echo ======================================================================
echo 🚀 OmniSite Windows 1-Click 초기 세팅 및 환경 설정
echo ======================================================================

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    uv run python setup_bootstrap.py %*
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        python setup_bootstrap.py %*
    ) else (
        py setup_bootstrap.py %*
    )
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ 세팅 중 오류가 발생했습니다. 파이썬 환경을 확인해주세요.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ✅ 세팅이 성공적으로 완료되었습니다!
pause
