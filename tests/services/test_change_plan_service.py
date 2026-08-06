from server.services import change_plan_service as svc


def test_normalize_ops_filters_bad_target():
    existing_ids = {12, 34}
    raw = {"ops": [
        {"action": "add", "title": "新增登录成功"},
        {"action": "modify", "target_case_id": 12, "title": "改登录"},
        {"action": "delete", "target_case_id": 999, "title": "删不存在"},
        {"action": "delete", "title": "缺 id"},
        {"action": "weird", "title": "非法动作"},
    ]}
    ops = svc._normalize_ops(raw, existing_ids)
    actions = [(o["action"], o.get("target_case_id")) for o in ops]
    assert ("add", None) in actions
    assert ("modify", 12) in actions
    assert all(not (o["action"] == "delete" and o["target_case_id"] == 999) for o in ops)
    assert all(o["action"] != "weird" for o in ops)
    assert [o["id"] for o in ops] == list(range(len(ops)))
