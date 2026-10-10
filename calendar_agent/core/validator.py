from datetime import datetime, timedelta


def validate_plan(plan, slots, deadline, minutes, options):
    errors = []
    if plan.intent not in ("create_plan", "add_task"):
        return errors
    if plan.goal.deadline != deadline: errors.append("goal.deadline 与代码解析的 deadline 不一致，必须复制给定值")
    if abs(plan.goal.estimated_hours * 60 - minutes) > 0.01: errors.append("goal.estimated_hours 必须复制代码调整后的预算")
    durations = [s.duration_min for s in plan.subtasks]
    if durations not in options: errors.append(f"时长组合必须完整选择 duration_options：{options}")
    by_start = {datetime.fromisoformat(s["slot_start"].replace("Z", "+00:00")): s for s in slots}
    seen = set()
    for i, sub in enumerate(plan.subtasks):
        slot = by_start.get(sub.slot_start)
        if slot is None: errors.append(f"subtasks[{i}].slot_start 不是真实可用 slot：{sub.slot_start.isoformat()}")
        elif sub.duration_min > slot["capacity_min"]: errors.append(f"subtasks[{i}] 时长超过 slot 容量")
        if sub.slot_start in seen: errors.append(f"subtasks[{i}] 重复占用同一个 slot")
        seen.add(sub.slot_start)
        if sub.slot_start + timedelta(minutes=sub.duration_min) > deadline: errors.append(f"subtasks[{i}] 越过 deadline")
        dep = sub.depends_on
        if dep is not None:
            if dep >= i: errors.append(f"subtasks[{i}].depends_on 必须指向前序步骤，不能自依赖/循环")
            elif plan.subtasks[dep].slot_start + timedelta(minutes=plan.subtasks[dep].duration_min) > sub.slot_start: errors.append(f"subtasks[{i}] 在依赖步骤结束前开始")
        for prev in plan.subtasks[:i]:
            if prev.slot_start < sub.slot_start + timedelta(minutes=sub.duration_min) and sub.slot_start < prev.slot_start + timedelta(minutes=prev.duration_min):
                errors.append(f"subtasks[{i}] 与其他步骤重叠")
    return errors
