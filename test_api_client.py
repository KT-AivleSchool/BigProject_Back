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

            current_event = None
            current_sender = None

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode("utf-8")

                    if decoded_line.startswith("event: "):
                        current_event = decoded_line[7:].strip()
                        continue

                    if decoded_line.startswith("data: "):
                        data_content = decoded_line[6:]
                        try:
                            msg_json = json.loads(data_content)
                            sender = msg_json.get("sender", "알 수 없음")
                            text = msg_json.get("text", "")

                            if current_event == "token":
                                # 화자가 바뀌면 이름 출력
                                if current_sender != sender:
                                    print(f"\n[{sender}]")
                                    current_sender = sender
                                # 토큰을 줄바꿈 없이 이어서 출력
                                sys.stdout.write(text)
                                sys.stdout.flush()

                            elif current_event == "message_end":
                                # 한 화자의 턴이 끝남
                                print()
                                current_sender = None

                            elif current_event == "message":
                                # 전체 메시지(시스템 메시지 등)
                                print(f"\n[{sender}]")
                                print(text)
                                current_sender = None

                        except json.JSONDecodeError:
                            print(data_content)

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
