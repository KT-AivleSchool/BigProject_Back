import logging
import asyncio
import random
from typing import Optional
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

async def geocode_address(address: str, client: Optional[httpx.AsyncClient] = None) -> Optional[dict]:
    """
    지번 또는 도로명 주소를 입력받아 위경도 좌표를 반환하는 비동기 함수.
    
    기본적으로 Kakao Local API를 사용하며, 실패하거나 결과가 없을 경우 Vworld API로 Fallback 합니다.
    성공 시 {"lat": 위도, "lng": 경도} 형태의 딕셔너리를 반환하며, 
    모든 API에서 찾지 못하거나 예외 발생 시 None을 반환합니다.
    
    :param address: 검색할 주소 문자열
    :param client: 외부에서 주입 가능한 httpx.AsyncClient 객체 (커넥션 풀링 재사용 최적화용)
    """
    if not address or not address.strip():
        return None
        
    lat_lng = await _geocode_kakao(address, client)
    if lat_lng:
        return lat_lng
        
    logger.info(f"Kakao geocoding failed or returned no results for address: '{address}'. Attempting Vworld API...")
    lat_lng = await _geocode_vworld(address, client)
    return lat_lng

async def _geocode_kakao(address: str, client: Optional[httpx.AsyncClient] = None) -> Optional[dict]:
    """Kakao Local API를 이용한 지오코딩"""
    api_key = settings.KAKAO_REST_API_KEY
    if not api_key:
        logger.warning("Kakao REST API Key is missing. Skipping Kakao geocoding.")
        return None
        
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {"query": address}
    
    async def do_get(c: httpx.AsyncClient) -> Optional[dict]:
        response = await c.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        documents = data.get("documents", [])
        if documents:
            doc = documents[0]
            # x: 경도(lng), y: 위도(lat)
            lng = float(doc["x"])
            lat = float(doc["y"])
            return {"lat": lat, "lng": lng}
        return None

    try:
        if client:
            return await do_get(client)
        else:
            async with httpx.AsyncClient(timeout=5.0) as local_client:
                return await do_get(local_client)
    except Exception as e:
        logger.error(f"Error during Kakao geocoding for '{address}': {str(e)}")
        
    return None

async def _geocode_vworld(address: str, client: Optional[httpx.AsyncClient] = None) -> Optional[dict]:
    """Vworld Geocoding API를 이용한 지오코딩 (임시 API 장애 복구 재시도 가드 탑재)"""
    api_key = settings.VWORLD_API_KEY
    if not api_key or api_key == "your-vworld-api-key-here":
        logger.warning("Vworld API Key is missing or invalid. Skipping Vworld geocoding.")
        return None
        
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getcoord",
        "version": "2.0",
        "crs": "epsg:4326",
        "address": address,
        "refine": "true",
        "simple": "false",
        "format": "json",
        "type": "road",
        "key": api_key
    }
    
    async def do_get_with_retry(c: httpx.AsyncClient, p: dict) -> Optional[dict]:
        for attempt in range(2):
            try:
                response = await c.get(url, params=p, timeout=5.0)
                response.raise_for_status()
                data = response.json()
                res = data.get("response", {})
                
                status_code = res.get("status")
                if status_code == "OK":
                    point = res.get("result", {}).get("point", {})
                    if point:
                        lng = float(point["x"])
                        lat = float(point["y"])
                        return {"lat": lat, "lng": lng}
                elif status_code == "NOT_FOUND":
                    # 주소가 존재하지 않는 경우 재시도 불필요
                    return None
                else:
                    logger.warning(f"Vworld geocoding status is {status_code} (Attempt {attempt + 1}/2)")
            except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as e:
                if attempt == 1:
                    raise
                wait_time = 0.5 + random.uniform(0.1, 0.3)
                logger.warning(f"Vworld temporary failure: {str(e)}. Retrying in {wait_time:.2f}s...")
                await asyncio.sleep(wait_time)
        return None

    try:
        # 1차 시도 (도로명 주소)
        result = None
        if client:
            result = await do_get_with_retry(client, params)
        else:
            async with httpx.AsyncClient(timeout=5.0) as local_client:
                result = await do_get_with_retry(local_client, params)
                
        if result:
            return result
            
        # 2차 시도 (지번 주소)
        params["type"] = "parcel"
        if client:
            result = await do_get_with_retry(client, params)
        else:
            async with httpx.AsyncClient(timeout=5.0) as local_client:
                result = await do_get_with_retry(local_client, params)
                
        return result
    except Exception as e:
        logger.error(f"Error during Vworld geocoding for '{address}': {str(e)}")
        
    return None
