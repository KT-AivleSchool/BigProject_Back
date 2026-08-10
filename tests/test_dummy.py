# FastAPI E2E & Unit Test Pipeline Base
# 향후 구현될 백엔드 API 테스트 자동화를 위한 진입점 더미 테스트 세션입니다.


def test_always_passes():
    """
    CI/CD pytest exit code 5 (no tests collected) 에러 방지 및
    통합 파이프라인 정합성을 수립하기 위한 기본 합격 테스트 케이스
    """
    assert True
