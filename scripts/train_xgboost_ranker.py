import os
import sys
import numpy as np
import xgboost as xgb

# 현재 디렉토리의 부모(backend/BigProject_Back)를 sys.path에 추가하여 app 모듈 임포트 가능하게 함
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.xgboost_rag_service import xgboost_rag_service

# 외부 파일로 분리된 더미 데이터 생성기 임포트
from scripts.dummy_data_generator import generate_dummy_data

from app.db.session import AsyncSessionLocal
from app.db.models.rag_feedback import RagFeedbackLog
from sqlalchemy import select
import asyncio

async def _fetch_data_async():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(RagFeedbackLog))
        logs = result.scalars().all()
        return logs

def fetch_data_from_db():
    """
    실제 DB(PostgreSQL)에 접속하여 사용자들이 남긴 '진짜 피드백 로그'를 가져옵니다.
    반환: (X, y) 또는 데이터가 없으면 None
    """
    try:
        logs = asyncio.run(_fetch_data_async())
    except Exception as e:
        print(f"[DB Error] 피드백 로그 조회 실패: {e}")
        return None
        
    if not logs:
        return None
        
    X_list = []
    y_list = []
    
    for log in logs:
        # DB에 저장된 원래 특징(벡터 스코어 포함)을 다시 추출
        features = xgboost_rag_service._extract_features(
            log.query_text, log.chunk_text, float(log.vector_score)
        )
        X_list.append(features)
        y_list.append(log.label)
        
    return np.array(X_list), np.array(y_list)

def train_and_save_model():
    # 1. 데이터 준비 (하이브리드: 더미 데이터 + 실제 DB 피드백 데이터)
    print("[Dummy Generator] 기본 뼈대를 잡기 위한 가상 학습 데이터셋을 생성합니다...")
    X_dummy, y_dummy = generate_dummy_data(xgboost_rag_service._extract_features)
    
    db_result = fetch_data_from_db()
    
    if db_result is not None:
        X_db, y_db = db_result
        print(f"[DB] 실제 사용자 피드백 데이터({X_db.shape[0]}건)를 성공적으로 불러왔습니다!")
        print("[Hybrid] 더미 데이터와 실제 데이터를 병합(Concatenate)하여 학습을 진행합니다.")
        
        # NumPy 배열 병합 (더미 데이터 뒤에 실제 데이터 붙이기)
        X = np.vstack((X_dummy, X_db))
        y = np.concatenate((y_dummy, y_db))
    else:
        print("[Dummy] DB에 학습할 진짜 데이터가 아직 없습니다. [더미 데이터]만으로 초기(콜드스타트) 학습을 진행합니다.")
        X, y = X_dummy, y_dummy
        
    print(f"데이터 형태: X={X.shape}, y={y.shape}")
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report

    # 학습용과 테스트용(검증용) 데이터 분리 (8:2 비율)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    print(f"분리 완료: 학습용 {X_train.shape[0]}개, 테스트용 {X_test.shape[0]}개")
    
    # 2. XGBoost 모델 초기화 및 학습
    print("XGBoost 모델 학습을 시작합니다...")
    model = xgb.XGBClassifier(
        n_estimators=50,
        max_depth=3,
        learning_rate=0.1,
        random_state=42,
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)
    print("모델 학습 완료!")
    
    print("\n" + "=" * 50)
    print("[모델 정확도 (Classification Report)]")
    print("=" * 50)
    y_pred = model.predict(X_test)
    try:
        report = classification_report(
            y_test, 
            y_pred, 
            labels=[0, 1], 
            target_names=["연관성 낮음(0)", "연관성 높음(1)"],
            zero_division=0
        )
        print(report)
    except Exception as e:
        print(f"(테스트 데이터가 너무 적어 리포트를 생성할 수 없습니다: {e})")
        print(f"실제값: {y_test}, 예측값: {y_pred}")
    print("=" * 50 + "\n")
    
    # 3. 모델 저장
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    models_dir = os.path.join(base_dir, 'app', 'models')
    
    if not os.path.exists(models_dir):
        os.makedirs(models_dir)
        
    model_path = os.path.join(models_dir, 'xgboost_rag_ranker.json')
    model.save_model(model_path)
    print(f"[성공] 모델이 성공적으로 저장되었습니다: {model_path}")
    
    # Feature Importance 출력
    print("\n[Feature Importance (어떤 기준이 모델 학습에 가장 큰 영향을 미쳤을까요?)]")
    importances = model.feature_importances_
    feature_names = [
        "1. 벡터 유사도 점수 (초기 RAG 검색 정확도)", 
        "2. 조례 원문 텍스트 길이 (너무 짧으면 무시)", 
        "3. 핵심 키워드 매칭 횟수 (연관성 지표)", 
        "4. 수치/거리 조건 포함 여부 (구체적인 법적 기준 존재 여부)"
    ]
    for name, imp in zip(feature_names, importances):
        print(f" - {name}: {imp:.4f}")

if __name__ == "__main__":
    train_and_save_model()
