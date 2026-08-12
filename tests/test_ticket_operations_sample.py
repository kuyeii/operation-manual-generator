from pathlib import Path

from manual_generator.analyzer import collect_feature_evidence, static_features


def test_ticket_operations_sample_exposes_exact_business_inventory():
    sample = Path(__file__).parents[1] / "samples" / "ticket-operations-demo"

    features = static_features(collect_feature_evidence(sample))

    assert [feature.title for feature in features] == [
        "刷新运营统计",
        "创建工单",
        "搜索工单",
        "筛选优先级",
        "更新工单状态",
        "分配负责人",
        "添加处理备注",
    ]
