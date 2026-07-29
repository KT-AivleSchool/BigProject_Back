import numpy as np

# xgboost_rag_service 모듈 임포트는 이 함수를 호출하는 쪽에서 이미 경로가 잡혀있다고 가정하거나
# 내부에서 지연 임포트(lazy import)를 사용하는 것이 안전합니다.


def generate_dummy_data(extract_features_func):
    """
    학습을 위한 가상 데이터셋 생성
    반환: (X, y)
    X: Feature 행렬 (n_samples, n_features)
    y: 정답 레이블 (n_samples,) - 1: 연관성 높음, 0: 연관성 낮음
    """
    print("[Dummy Generator] 가상 학습 데이터셋을 생성합니다...")

    # 쿼리, 청크 텍스트, 벡터 점수, 정답(1 or 0) 세트
    raw_data = [
        # 좋은 문서 (정답=1): 쿼리 단어가 많이 포함되고, 수치 조건이 명확함
        (
            "흡연부스 설치 기준",
            "흡연부스 설치 시 주변 건물과 50m 이상 거리를 두어야 한다.",
            0.85,
            1,
        ),
        (
            "전기차 충전소 규제",
            "전기차 충전소는 주차장 면적의 5% 이상을 할당해야 하며 100제곱미터 이상의 공간이 필요하다.",
            0.90,
            1,
        ),
        (
            "청년주택 혜택 용적률",
            "청년주택 건설 시 용적률 500% 혜택을 제공하며 역세권 250m 이내여야 합니다.",
            0.88,
            1,
        ),
        # 나쁜 문서 (정답=0): 길이는 길지만 쿼리 키워드 부족, 수치 조건 없음
        (
            "흡연부스 설치 기준",
            "최근 도심 내 공원 환경 조성을 위해 다양한 나무를 심고 시민들에게 휴식 공간을 제공하자는 논의가 활발히 진행 중입니다.",
            0.35,
            0,
        ),
        (
            "전기차 충전소 규제",
            "친환경 자동차 보급이 늘어나면서 시민들의 대기 오염에 대한 관심이 높아지고 있습니다. 이에 따라 지자체는 캠페인을 벌입니다.",
            0.40,
            0,
        ),
        # 애매한 문서 (정답=0): 키워드는 좀 있지만 수치 조건이나 구체성 결여, 혹은 너무 짧음
        ("청년주택 혜택 용적률", "청년주택 관련 조례입니다.", 0.50, 0),
        (
            "전기차 충전소 규제",
            "전기차와 관련된 일반적인 안전 수칙 안내문입니다.",
            0.45,
            0,
        ),
        # 🚨 함정 데이터 1 (정답=0): 벡터 점수는 매우 높지만, 막상 핵심 키워드나 수치 조건이 없는 껍데기 문서
        (
            "흡연부스 설치 기준",
            "흡연과 무관한 일반적인 시설물 설치 기준입니다. 다양한 조례에서 공통적으로 적용되는 위치 제약 조건만을 설명하고 있습니다.",
            0.88,
            0,
        ),
        (
            "건축 허가 면적",
            "건축에 관한 허가 기준을 다루는 안내문서로, 구체적인 기준이나 제약 사항은 추후 고시됩니다.",
            0.92,
            0,
        ),
        # 🚨 함정 데이터 2 (정답=1): 벡터 점수는 낮지만, 정답이 되는 핵심 키워드와 수치 조건(숫자)이 완벽하게 들어간 알짜 문서
        ("전기차 충전소 규제", "면적 5% 할당 및 100제곱미터 이상 확보 필수.", 0.55, 1),
        ("소음 규제 기준", "공사장 소음 65dB 야간 50dB", 0.45, 1),
    ]

    # 12개의 베이스 시나리오를 바탕으로, 완전히 고유한(Unique) 100개의 데이터를 랜덤하게 합성하여 생성합니다.
    augmented_raw_data = []

    import random

    for _ in range(100):
        # 12개 중 하나를 베이스로 선택
        base_query, base_chunk, base_v_score, base_label = random.choice(raw_data)

        # 1. 쿼리 약간 변형 (길이 변화를 위해)
        query = (
            base_query
            + " "
            + random.choice(["", "방법", "관련 법안", "사례", "가이드라인"])
        )

        # 2. 텍스트 랜덤 추가 (텍스트 길이 특징 부여)
        noise_text = " " + "테스트 " * random.randint(0, 10)
        chunk = base_chunk + noise_text

        # 3. 벡터 점수에 강한 노이즈(난수) 추가 (0.0 ~ 1.0 사이 유지)
        v_score = min(1.0, max(0.0, base_v_score + np.random.normal(0, 0.15)))

        # 4. 정답(Label)에도 의도적인 혼란(Noise) 추가 (약 10% 확률로 정답이 뒤바뀜 - 현실 세계의 휴먼 에러 모사)
        label = base_label
        if random.random() < 0.10:
            label = 1 if base_label == 0 else 0

        augmented_raw_data.append((query, chunk, v_score, label))

    X_list = []
    y_list = []

    for query, chunk, v_score, label in augmented_raw_data:
        # 주입받은 특징 추출 함수(xgboost_rag_service._extract_features)를 사용
        features = extract_features_func(query, chunk, v_score)
        X_list.append(features)
        y_list.append(label)

    return np.array(X_list), np.array(y_list)
