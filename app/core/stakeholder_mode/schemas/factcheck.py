from enum import StrEnum


class FactCheckStatus(StrEnum):
    SUPPORTED = "supported"            # 제공 자료로 확인됨
    CONTRADICTED = "contradicted"      # 제공 자료와 명확히 반대
    DISTORTED = "distorted"            # 조례나 데이터를 교묘하게 왜곡함
    UNSUPPORTED = "unsupported"        # 조례/데이터에 없는 내용을 사실처럼 주장
    UNVERIFIABLE = "unverifiable"      # 현재 자료로 확인할 수 없는 합리적 가정
    INTERPRETATION = "interpretation"  # 사실이 아니라 데이터 주관적 해석
    VALUE_JUDGMENT = "value_judgment"  # 가치판단
    MISSING_DATA = "missing_data"      # 판단에 필요한 데이터 누락
