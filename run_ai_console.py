import asyncio
import json
from unittest.mock import MagicMock

# --- [중요] DB 의존성 완벽 차단 (Mocking) ---
# graph.py 모듈이 로드되면서 vector_db(PGVector)를 무조건 초기화하려 시도합니다.
# psycopg 모듈이 없어서 나는 에러를 방지하기 위해, 사전에 가짜(Mock) 객체로 바꿔치기(Patch) 합니다.
import app.core.sim_ai.vector_db

app.core.sim_ai.vector_db.RagVectorStorage = MagicMock

# 가짜 객체를 주입한 후 안전하게 LangGraph를 불러옵니다.
from app.core.sim_ai.graph import build_discussion_graph  # noqa: E402


async def main():
    print("🤖 [DB 없이 실행] AI 모의 심의 토론 콘솔 테스트 시작...\n")

    import csv
    import os

    # 1. 조례데이터.csv 파일 읽어오기
    rag_context_text = ""
    csv_file_path = "조례데이터.csv"

    if os.path.exists(csv_file_path):
        try:
            with open(csv_file_path, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader, None)  # 헤더 건너뛰기 혹은 저장
                if headers:
                    rag_context_text += (
                        f"[참고 조례 데이터 항목: {', '.join(headers)}]\n"
                    )

                for row in reader:
                    rag_context_text += " - " + " / ".join(row) + "\n"
            print(
                f"✅ '{csv_file_path}' 파일을 성공적으로 불러와 AI 배경지식(RAG)에 주입했습니다.\n"
            )
        except Exception as e:
            print(f"⚠️ '{csv_file_path}' 읽기 오류: {e}")
            rag_context_text = "조례 데이터 읽기 실패"
    else:
        print(f"⚠️ '{csv_file_path}' 파일이 없어서 기본 더미 데이터를 사용합니다.\n")
        rag_context_text = (
            "테스트 조례: 주거지역 인근 10m 이내 금연구역 지정 (DB Vector Search 생략)"
        )

    def parse_mock_audit_data(audit_data: dict) -> str:
        if not audit_data:
            return "프론트엔드 감리 데이터 없음"
        positive, negative, hard_exclusion = [], [], []
        for res in audit_data.get("results", []):
            for r in res.get("roles", []):
                role = r.get("role", "")
                rationale = r.get("rationale", "")
                if role == "positive_factor":
                    positive.append(f"- {rationale}")
                elif role == "negative_factor":
                    negative.append(f"- {rationale}")
                elif role == "hard_exclusion":
                    source = r.get("source", "출처 불명")
                    hard_exclusion.append(f"- [절대금지] {rationale} (근거: {source})")
        lines = []
        if positive:
            lines.append("## 설치 가점 요인\n" + "\n".join(positive))
        if negative:
            lines.append("## 설치 감점/갈등 요인\n" + "\n".join(negative))
        if hard_exclusion:
            lines.append("## 절대 배제(금지) 요인\n" + "\n".join(hard_exclusion))
        return "\n\n".join(lines) if lines else "유효한 감리 팩터가 발견되지 않았습니다."

    # 2. dummy_audit.json 파일 읽어오기
    audit_file_path = "dummy_audit.json"
    if os.path.exists(audit_file_path):
        try:
            with open(audit_file_path, mode="r", encoding="utf-8") as f:
                audit_data_json = json.load(f)
            audit_context = parse_mock_audit_data(audit_data_json)
            print(f"✅ '{audit_file_path}' 파일을 불러와 감리 결과를 성공적으로 정제했습니다.\n")
        except Exception as e:
            print(f"⚠️ '{audit_file_path}' 읽기 오류: {e}")
            audit_context = "프론트엔드 감리 데이터 없음"
    else:
        print(f"⚠️ '{audit_file_path}' 파일이 없어서 감리 데이터를 생략합니다.\n")
        audit_context = "프론트엔드 감리 데이터 없음"

    # DB 조회를 완벽히 대체하는 가상의(Mock) 데이터
    import random

    initial_state = {
        "messages": [],
        "css_pro": random.choice(["LOW", "MEDIUM", "HIGH"]),
        "css_con": random.choice(["LOW", "MEDIUM", "HIGH"]),
        "round_count": 0,
        "current_phase": "debate",
        "eval_score": 0.0,
        "spoken_this_round": [],
        "candidate_jibun": "서울특별시 용산구 이태원동 123-45",  # 가짜 지번
        "candidate_lat": 37.534,
        "candidate_lng": 126.994,
        "facility_type": "흡연부스",
        "intensity_level": "높음",
        "ahp_weights": {"보행혼잡도": 0.4, "소음민감도": 0.3, "상권활성화": 0.3},
        "timestamp": "2026-07-20T10:00:00",
        "common_rag": rag_context_text,
        "audit_context": audit_context,
        "evaluations": {},
        "final_scenarios": {},
        "is_finished": False,
        "next_speaker": "pro",
    }

    graph = build_discussion_graph()

    print("================ [토론 시작] ================\n")

    try:
        # DB 연결 없이 순수하게 LangGraph(AI 엔진)만 단독 실행!
        async for output in graph.astream(initial_state):
            for node_name, node_state in output.items():
                if "messages" in node_state and len(node_state["messages"]) > 0:
                    # 마지막으로 추가된 메시지 가져오기
                    msg = node_state["messages"][-1]
                    print(msg)
                    print("-" * 50)

                # 최종 결과 리포트 출력
                if node_name == "reporter":
                    print(
                        "\n[시스템] 토론이 종료되었습니다. 도출된 최종 단일 시나리오:\n"
                    )
                    if "final_scenarios" in node_state:
                        print(
                            json.dumps(
                                node_state["final_scenarios"],
                                ensure_ascii=False,
                                indent=2,
                            )
                        )

    except Exception as e:
        print(f"\n[오류 발생] {e}")
        print("💡 팁: OpenAI API Key가 .env에 제대로 설정되어 있는지 확인하세요!")


if __name__ == "__main__":
    asyncio.run(main())
