import json
import requests
import sys


def main():
    url = "http://localhost:8000/api/v1/simulation/stream"

    try:
        with open("dummy_audit.json", "r", encoding="utf-8") as f:
            audit_data = json.load(f)
    except FileNotFoundError:
        print("❌ 'dummy_audit.json' 파일을 찾을 수 없습니다.")
        sys.exit(1)

    payload = {"parcel_id": 1, "facility_type": "흡연부스", "audit_data": audit_data}

    print(f"📡 FastAPI 서버({url})로 POST 스트리밍 요청을 보냅니다...\n")
    print("-" * 50)

    try:
        # stream=True 옵션을 주어 실시간 SSE 데이터를 끊기지 않고 받아옵니다.
        with requests.post(url, json=payload, stream=True) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode("utf-8")
                    # SSE 규격(data: ...)에 맞춰서 출력
                    if decoded_line.startswith("data: "):
                        data_content = decoded_line[6:]
                        try:
                            msg_json = json.loads(data_content)
                            sender = msg_json.get("sender", "알 수 없음")
                            text = msg_json.get("text", "")

                            print(f"\n[{sender}]")
                            print(text)

                        except json.JSONDecodeError:
                            print(data_content)
                    elif decoded_line.startswith("event: "):
                        pass  # event: message 줄은 무시하고 깔끔하게 출력
                    else:
                        print(decoded_line)

            print("-" * 50)
            print("✅ 실시간 스트리밍이 종료되었습니다.\n")

    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다!")
        print(
            "💡 팁: 터미널 창을 하나 더 열어서 'uvicorn app.main:app --reload' 명령어로 FastAPI 서버를 먼저 켜주세요."
        )
    except Exception as e:
        print(f"❌ 오류 발생: {e}")


if __name__ == "__main__":
    main()
