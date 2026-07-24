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

            current_sender = None

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode("utf-8")

                    if decoded_line.startswith("event: "):
                        continue

                    if decoded_line.startswith("data: "):
                        data_content = decoded_line[6:]
                        try:
                            msg_json = json.loads(data_content)

                            # 에러 응답 처리
                            if "error_code" in msg_json:
                                print(f"\n[시스템 오류] {msg_json.get('message')}")
                                continue

                            sender = msg_json.get("sender") or ""
                            text = msg_json.get("text", "")

                            # 줄바꿈만 들어오는 개행 메시지는 이름 출력 없이 텍스트만 출력
                            if not text.strip():
                                sys.stdout.write(text)
                                sys.stdout.flush()
                                continue

                            # 화자가 변경되었을 때만 화자 태그 출력
                            if sender and current_sender != sender:
                                if current_sender is not None:
                                    print()  # 화자가 바뀔 때 가독성을 위해 한 줄 띄움
                                print(f"\n[{sender}]")
                                current_sender = sender

                            # 실시간 토큰(글자)을 줄바꿈 없이 이어서 출력
                            sys.stdout.write(text)
                            sys.stdout.flush()

                        except json.JSONDecodeError:
                            print(data_content)

            print("\n" + "-" * 50)
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
