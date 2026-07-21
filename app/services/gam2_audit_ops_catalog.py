# 최소 스텁 (배선 검증 전용 — 실제 파일은 D:\B_P\gam2 에 있음)
def describe_all():
    return [{"op_id": "trim_whitespace", "desc": "공백 제거"},
            {"op_id": "spatial_join_admin", "desc": "좌표→행정동"}]
EXPECTED_RECIPES = {
    "A1": {"ops": ["trim_whitespace"]},
    "A2": {"ops": ["trim_whitespace"]},
}
