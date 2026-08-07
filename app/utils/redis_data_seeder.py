"""
OmniSite Redis Raw Data Seeder & Loader

로컬의 data_임시/<도메인>/data/ 원본 파일들을
Feather/Parquet/Pickle 바이너리 포맷으로 직렬화하여 Redis에 적재하고,
파이프라인 및 정제 프로세스가 Redis에서 수 밀리초 내로 직접 읽어올 수 있도록 지원하는 모듈입니다.
"""

import io
import os
import pickle
import logging
import redis
import pandas as pd
import geopandas as gpd
from app.config import settings

logger = logging.getLogger("uvicorn.error")

_REDIS_KEY_PREFIX = "omnisite:raw_data"
_ENCODINGS = ["utf-8-sig", "cp949", "euc-kr", "utf-8"]


def get_redis_client() -> redis.Redis | None:
    """동기식 Redis 클라이언트 인스턴스 획득"""
    try:
        url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=False)
        client.ping()
        return client
    except Exception as e:
        logger.warning(f"[RedisSeeder] Redis 연결 실패: {e}")
        return None


def _make_redis_key(domain: str, filename: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{domain}:{filename}"


def _read_csv_fallback(fpath: str) -> pd.DataFrame:
    """한글 CP949 / EUC-KR / UTF-8 인코딩을 다각도로 폴백하여 CSV를 파싱합니다."""
    last_err = None
    for enc in _ENCODINGS:
        try:
            return pd.read_csv(fpath, encoding=enc, low_memory=False)
        except UnicodeDecodeError as e:
            last_err = e
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return pd.read_csv(fpath, encoding="utf-8-sig", low_memory=False)


def seed_domain_data_to_redis(domain: str = "흡연", data_dir: str | None = None) -> dict[str, bool]:
    """
    지정한 도메인의 data_임시/<domain>/data/ 디렉터리 내 데이터를
    바이너리로 직렬화하여 Redis에 적재합니다.
    """
    redis_cli = get_redis_client()
    if not redis_cli:
        logger.warning("[RedisSeeder] Redis 클라이언트를 생성할 수 없습니다. 적재를 건너뜁니다.")
        return {}

    if not data_dir:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        data_dir = os.path.join(base_dir, "data_임시", domain, "data")

    if not os.path.exists(data_dir):
        logger.warning(f"[RedisSeeder] 데이터 디렉터리가 존재하지 않습니다: {data_dir}")
        return {}

    results = {}
    files = os.listdir(data_dir)
    logger.info(f"[RedisSeeder] {domain} 도메인 시딩 시작 (총 {len(files)}개 파일 탐색)")

    for fname in files:
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            continue

        ext = os.path.splitext(fname)[1].lower()
        key = _make_redis_key(domain, fname)

        try:
            raw_bytes = None
            if ext in (".csv", ".txt"):
                df = _read_csv_fallback(fpath)
                buf = io.BytesIO()
                try:
                    df.to_feather(buf)
                    raw_bytes = buf.getvalue()
                except Exception:
                    # PyArrow 큰 정수 오버플로 등 발생 시 Pickle 바이너리로 유연하게 백업
                    raw_bytes = pickle.dumps(df)
            elif ext in (".shp", ".gpkg", ".geojson"):
                gdf = gpd.read_file(fpath)
                buf = io.BytesIO()
                try:
                    gdf.to_feather(buf)
                    raw_bytes = buf.getvalue()
                except Exception:
                    raw_bytes = pickle.dumps(gdf)
            elif ext in (".feather", ".parquet"):
                with open(fpath, "rb") as f:
                    raw_bytes = f.read()
            else:
                logger.debug(f"[RedisSeeder] 건너뜀 (지원 확장자 아님): {fname}")
                continue

            if raw_bytes:
                redis_cli.set(key, raw_bytes)
                results[fname] = True
                logger.info(f"  ✅ [Redis 적재 성공] {key} ({len(raw_bytes):,} bytes)")
        except Exception as e:
            results[fname] = False
            logger.error(f"  ❌ [Redis 적재 실패] {fname}: {e}")

    return results


def get_dataset_from_redis(domain: str, filename: str) -> pd.DataFrame | gpd.GeoDataFrame | None:
    """
    Redis에서 적재된 바이너리 데이터를 읽어 DataFrame 또는 GeoDataFrame으로 즉시 복원합니다.
    """
    redis_cli = get_redis_client()
    if not redis_cli:
        return None

    key = _make_redis_key(domain, filename)
    try:
        data_bytes = redis_cli.get(key)
        if not data_bytes:
            return None

        # 1차 Feather 시도, 2차 Pickle 시도
        buf = io.BytesIO(data_bytes)
        buf.seek(0)

        try:
            return gpd.read_feather(buf)
        except Exception:
            pass

        buf.seek(0)
        try:
            return pd.read_feather(buf)
        except Exception:
            pass

        return pickle.loads(data_bytes)
    except Exception as e:
        logger.warning(f"[RedisLoader] Redis 키 조회 중 예외 발생 ({key}): {e}")
        return None
