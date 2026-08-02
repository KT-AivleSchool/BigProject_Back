import os
import sys
import argparse
import pandas as pd
import requests
import time

# Add backend dir to sys.path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings

VWORLD_ENDPOINT = "https://api.vworld.kr/req/address"

def geocode_vworld(address: str):
    if not address or pd.isna(address):
        return None, None
    
    api_key = settings.VWORLD_API_KEY
    if not api_key or api_key == "your-vworld-api-key-here":
        print("⚠ VWORLD_API_KEY is not set in config!")
        return None, None
    
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
        resp = requests.get(VWORLD_ENDPOINT, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if "response" in data and data["response"]["status"] == "OK":
                result = data["response"]["result"]["point"]
                return float(result["y"]), float(result["x"]) # lat, lon
            
            # fallback to parcel(지번)
            params["type"] = "parcel"
            resp2 = requests.get(VWORLD_ENDPOINT, params=params, timeout=5)
            if resp2.status_code == 200:
                data2 = resp2.json()
                if "response" in data2 and data2["response"]["status"] == "OK":
                    result = data2["response"]["result"]["point"]
                    return float(result["y"]), float(result["x"])
    except Exception as e:
        print(f"Error geocoding {address}: {e}")
    return None, None

def main():
    parser = argparse.ArgumentParser(description="Merge municipal property CSV into National Property CSV")
    parser.add_argument("--new-data", required=True, help="Path to new municipal public land CSV")
    parser.add_argument("--addr-col", default="소재지", help="Column name containing the address in the new CSV")
    parser.add_argument("--area-col", default="면적", help="Column name containing the area in the new CSV")
    parser.add_argument("--jimok-col", default="지목", help="Column name containing the land category (e.g. 대, 도)")
    parser.add_argument("--base", default="data_임시/region_data/국유부동산_위경도_v2.csv", help="Base national property CSV")
    args = parser.parse_args()

    if not os.path.exists(args.new_data):
        print(f"Error: {args.new_data} not found.")
        sys.exit(1)

    try:
        df_new = pd.read_csv(args.new_data, encoding='utf-8')
    except UnicodeDecodeError:
        df_new = pd.read_csv(args.new_data, encoding='euc-kr')
    
    if args.addr_col not in df_new.columns:
        print(f"Error: Column '{args.addr_col}' not found in {args.new_data}")
        sys.exit(1)

    print(f"Loading {len(df_new)} rows from {args.new_data}...")
    
    lats, lons = [], []
    success = 0
    for idx, row in df_new.iterrows():
        addr = str(row[args.addr_col]).strip()
        lat, lon = geocode_vworld(addr)
        lats.append(lat)
        lons.append(lon)
        if lat and lon:
            success += 1
        
        # throttle
        time.sleep(0.05)
        
        if (idx + 1) % 50 == 0:
            print(f"  Geocoded {idx + 1}/{len(df_new)}...")

    df_new["위도"] = lats
    df_new["경도"] = lons
    
    df_valid = df_new.dropna(subset=["위도", "경도"]).copy()
    print(f"Geocoding successful for {success}/{len(df_new)} rows.")

    # Base columns: 소재지(지번),지목(공부),대장면적(단위:㎡),경도,위도
    base_cols = ["소재지(지번)", "지목(공부)", "대장면적(단위:㎡)", "경도", "위도"]
    
    # map columns
    df_mapped = pd.DataFrame()
    df_mapped["소재지(지번)"] = df_valid[args.addr_col]
    
    if args.jimok_col in df_valid.columns:
        df_mapped["지목(공부)"] = df_valid[args.jimok_col]
    else:
        df_mapped["지목(공부)"] = "대" # fallback
        
    if args.area_col in df_valid.columns:
        df_mapped["대장면적(단위:㎡)"] = df_valid[args.area_col]
    else:
        df_mapped["대장면적(단위:㎡)"] = 0.0
        
    df_mapped["경도"] = df_valid["경도"]
    df_mapped["위도"] = df_valid["위도"]

    if os.path.exists(args.base):
        df_base = pd.read_csv(args.base)
        print(f"Base CSV loaded with {len(df_base)} rows.")
        df_combined = pd.concat([df_base, df_mapped], ignore_index=True)
    else:
        print(f"Base CSV not found at {args.base}, creating new one.")
        df_combined = df_mapped

    out_path = args.base.replace(".csv", "_통합.csv")
    if args.base.endswith("_통합.csv"):
        out_path = args.base
        
    df_combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Successfully saved combined CSV with {len(df_combined)} rows to: {out_path}")
    print("Now you can update NATIONAL_PROPERTY_CSV in app/config.py to point to this new file!")

if __name__ == "__main__":
    main()
