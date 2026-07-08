import logging
from typing import Optional
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

async def geocode_address(address: str) -> Optional[dict]:
    """
    지번 또는 도로명 주소를 입력받아 위경도 좌표를 반환하는 비동기 함수.
    
    기본적으로 Kakao Local API를 사용하며, 실패하거나 결과가 없을 경우 Vworld API로 Fallback 합니다.
    성공 시 {"lat": 위도, "lng": 경도} 형태의 딕셔너리를 반환하며, 
    모든 API에서 찾지 못하거나 예외 발생 시 None을 반환합니다.
    """
    if not address or not address.strip():
        return None
        
    lat_lng = await _geocode_kakao(address)
    if lat_lng:
        return lat_lng
        
    logger.info(f"Kakao geocoding failed or returned no results for address: '{address}'. Attempting Vworld API...")
    lat_lng = await _geocode_vworld(address)
    return lat_lng

async def _geocode_kakao(address: str) -> Optional[dict]:
    """Kakao Local API를 이용한 지오코딩"""
    api_key = settings.KAKAO_REST_API_KEY
    if not api_key:
        logger.warning("Kakao REST API Key is missing. Skipping Kakao geocoding.")
        return None
        
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {"query": address}
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            documents = data.get("documents", [])
            if documents:
                doc = documents[0]
                # x: 경도(lng), y: 위도(lat)
                lng = float(doc["x"])
                lat = float(doc["y"])
                return {"lat": lat, "lng": lng}
    except Exception as e:
        logger.error(f"Error during Kakao geocoding for '{address}': {str(e)}")
        
    return None

async def _geocode_vworld(address: str) -> Optional[dict]:
    """Vworld Geocoding API를 이용한 지오코딩"""
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
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            res = data.get("response", {})
            if res.get("status") == "OK":
                point = res.get("result", {}).get("point", {})
                if point:
                    # x: 경도(lng), y: 위도(lat)
                    lng = float(point["x"])
                    lat = float(point["y"])
                    return {"lat": lat, "lng": lng}
            else:
                # 도로명 주소(road)로 실패할 경우 지번 주소(parcel)로 재시도
                params["type"] = "parcel"
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                res = data.get("response", {})
                if res.get("status") == "OK":
                    point = res.get("result", {}).get("point", {})
                    if point:
                        lng = float(point["x"])
                        lat = float(point["y"])
                        return {"lat": lat, "lng": lng}
                    
                logger.warning(f"Vworld geocoding returned status: {res.get('status')} for address '{address}'")
    except Exception as e:
        logger.error(f"Error during Vworld geocoding for '{address}': {str(e)}")
        
    return None
