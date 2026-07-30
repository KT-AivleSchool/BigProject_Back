import os
import asyncio
from unittest.mock import MagicMock

# 1. DB(PostgreSQL) 연결 없이 빠르게 AI 토론만 단독 테스트하기 위한 Mocking
# (Docker DB가 꺼져있어도 에러 없이 실행되도록 가짜 객체로 대체합니다)
# ⚠️ Mock 대체를 graph import보다 먼저 해야 하므로 E402는 의도된 것
import app.core.sim_ai.vector_db  # noqa: E402

app.core.sim_ai.vector_db.RagVectorStorage = MagicMock

from app.core.sim_ai.document_loader import statute_document_loader  # noqa: E402
from app.core.sim_ai.graph import build_discussion_graph  # noqa: E402


def load_all_documents_from_folder(folder_path: str) -> str:
    """
    지정된 폴더 안의 모든 파일(PDF, HWP, DOCX)을 순회하며 파싱 후 RAG 텍스트로 합성
    """
    if not os.path.exists(folder_path):
        os.makedirs(folder_path, exist_ok=True)
        print(
            f"📁 '{folder_path}' 폴더가 자동 생성되었습니다. 테스트할 PDF/HWP/DOCX 파일들을 이 폴더에 넣어주세요!"
        )
        return ""

    supported_extensions = [".pdf", ".hwp", ".docx", ".doc"]
    files = [
        f
        for f in os.listdir(folder_path)
        if os.path.splitext(f)[1].lower() in supported_extensions
    ]

    if not files:
        print(
            f"⚠️ '{folder_path}' 폴더 안에 테스트할 문서 파일(PDF, HWP, DOCX)이 없습니다."
        )
        return "조례 문서 없음"

    print(f"📚 총 {len(files)}개의 조례 문서를 감지했습니다: {files}\n")

    rag_chunks = []

    for file_name in files:
        file_path = os.path.join(folder_path, file_name)
        _, ext = os.path.splitext(file_name)

        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            # app/core/sim_ai/document_loader.py를 사용한 확장자별 파싱 & 청킹
            chunks = statute_document_loader.process_document(file_bytes, ext)
            print(f"  - [{file_name}] 파싱 성공 -> {len(chunks)}개 청크 생성")

            # 출처 파일명을 태깅하여 청크 보관
            for i, chunk in enumerate(chunks):
                rag_chunks.append(f"[출처 문서: {file_name} (청크 {i + 1})]\n{chunk}")

        except Exception as e:
            print(f"  - ❌ [{file_name}] 파싱 실패: {e}")

    # 모든 파일의 청크를 하나로 결합
    combined_rag_text = "\n\n".join(rag_chunks[:15])
    print(
        f"\n✅ 총 {len(rag_chunks)}개 청크 중 상위 청크들을 성공적으로 통합 RAG 데이터로 묶었습니다.\n"
    )
    return combined_rag_text


async def main():
    docs_folder = "seeds"  # 여기에 pdf, hwp, docx 파일들을 복사해 넣으시면 됩니다.

    rag_context = load_all_documents_from_folder(docs_folder)

    if not rag_context or rag_context == "조례 문서 없음":
        print(
            "💡 'seeds' 폴더에 조례 파일(PDF, HWP 등)을 1개 이상 넣고 다시 실행해 주세요!"
        )
        return

    # 2. 페르소나 토론 초기 상태 준비
    initial_state = {
        "messages": [],
        "css_pro": "HIGH",
        "css_con": "HIGH",
        "round_count": 0,
        "current_phase": "debate",
        "eval_score": 0.0,
        "spoken_this_round": [],
        "candidate_jibun": "서울특별시 용산구 이태원동 123-45",
        "facility_type": "흡연부스",
        "intensity_level": "높음",
        "ahp_weights": {"보행혼잡도": 0.4, "소음민감도": 0.3, "상권활성화": 0.3},
        "common_rag": rag_context,  # 👈 파싱된 다중 문서 RAG 데이터 주입
        "audit_context": "프론트엔드 감리 데이터 없음",
        "evaluations": {},
        "final_scenarios": {},
        "is_finished": False,
        "next_speaker": "pro",
    }

    graph = build_discussion_graph()
    print(
        "================ [다중 문서 RAG 기반 AI 페르소나 토론 시작] ================\n"
    )

    try:
        async for output in graph.astream(initial_state):
            for node_name, node_state in output.items():
                if "messages" in node_state and len(node_state["messages"]) > 0:
                    print(node_state["messages"][-1])
                    print("-" * 50)
    except Exception as e:
        print(f"\n❌ 실행 오류: {e}")
        print("💡 팁: .env 파일에 OPENAI_API_KEY가 설정되어 있는지 확인해 주세요!")


if __name__ == "__main__":
    asyncio.run(main())
