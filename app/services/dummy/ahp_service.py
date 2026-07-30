import math


def calculate_ahp_consistency(
    matrix_size: int, pairwise_matrix: list[list[float]]
) -> dict:
    """
    AHP 쌍대비교 정규화 및 일관성 비율(C.R.) 계산 (순수 파이썬 구현 버전)
    Numpy 의존성 없이 수학적 고유벡터 및 C.R. 검증을 초고속으로 수행합니다.
    """
    n = matrix_size
    A = pairwise_matrix

    # 1. 각 행의 기하평균(Geometric Mean) 계산
    geom_means = []
    for row in A:
        # math.prod를 사용해 각 행의 요소를 모두 곱함
        row_product = math.prod(row)
        # n제곱근 적용
        geom_mean = row_product ** (1.0 / n)
        geom_means.append(geom_mean)

    # 2. 가중치 정규화 (합이 1.0이 되도록 조정)
    sum_geom_means = sum(geom_means)
    weights = [gm / sum_geom_means for gm in geom_means]

    # 3. 최대 고유치 (lambda_max) 산출을 위한 Aw 벡터 연산
    # Aw = A * w (행렬곱)
    aw_vector = []
    for i in range(n):
        row_sum = 0.0
        for j in range(n):
            row_sum += A[i][j] * weights[j]
        aw_vector.append(row_sum)

    # 각 행별 (Aw_i / w_i)의 평균을 취해 lambda_max 근사치 계산
    lambda_max_terms = [aw_vector[i] / weights[i] for i in range(n)]
    lambda_max = sum(lambda_max_terms) / n

    # 4. 일관성 지수 (C.I.) 계산
    ci = (lambda_max - n) / (n - 1) if n > 1 else 0.0

    # 5. 무작위 일관성 지수 (R.I.) 상수 설정 (Saaty 표준)
    # n=1~10에 따른 고정 테이블
    ri_table = {
        1: 0.0,
        2: 0.0,
        3: 0.58,
        4: 0.90,
        5: 1.12,
        6: 1.24,
        7: 1.32,
        8: 1.41,
        9: 1.45,
        10: 1.49,
    }
    ri = ri_table.get(n, 1.12)  # 기본 5인자(1.12)

    # 6. 일관성 비율 (C.R.) 산정
    cr = ci / ri if ri > 0.0 else 0.0

    # 7. 합리성 판정 임계치 (C.R. < 0.1)
    is_valid = cr < 0.1

    return {
        "is_valid": is_valid,
        "consistency_ratio": round(cr, 4),
        "lambda_max": round(lambda_max, 4),
        "weights": [round(w, 4) for w in weights],
    }
