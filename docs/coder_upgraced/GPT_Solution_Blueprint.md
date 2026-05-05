Bạn không còn bị lỗi “hạ tầng” nữa — vấn đề hiện tại là thiếu deterministic mapping logic. Nói thẳng: hệ thống của bạn đang “đoán” thay vì “compile”.

Dưới đây là blueprint ở mức Principal Engineer để đưa hệ thống từ 50 → 100/100.

📄 blueprint.md — Agentic Workflow Compiler V5 (Senior Benchmark)
🎯 Design Goal

Biến input tiếng Việt → Workflow IR (Intermediate Representation) với:

Dependency graph deterministic

Policy detection rule-based + extensible

Zero ambiguity cho downstream compiler

1. 🧱 Core Architecture
User Input (VN)
   ↓
Planner (intent segmentation)
   ↓
Parser (structured IR + dependency resolution)
   ↓
Validator (semantic + graph validation)
   ↓
Compiler (final workflow object)
2. 📦 schema.py
✅ Mục tiêu

Định nghĩa IR rõ ràng, không để parser “tự suy diễn”

Python
Run
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel

class ActionType(str, Enum):
    FETCH = "fetch"
    TRANSFORM = "transform"
    NOTIFY = "notify"

class FailurePolicy(str, Enum):
    STOP = "stop"
    CONTINUE = "continue"
    NOTIFY_ONLY = "notify_only"

class Action(BaseModel):
    id: int
    type: ActionType
    input: Optional[str] = None
    output: Optional[str] = None
    depends_on: List[int] = []

class Workflow(BaseModel):
    actions: List[Action]
    failure_policy: FailurePolicy

🔑 Key Fix

depends_on phải là list[int] rõ ràng

Không derive implicit dependencies ở compiler

3. 🧠 planner.py
❌ Lỗi hiện tại

Planner chưa “ép format” đủ chặt → parser phải đoán

✅ Fix: Planner phải emit structure gần-IR
Python
Run
class PlanStep(BaseModel):
    step_id: int
    raw_text: str
    action_hint: str  # fetch | transform | notify
Rule-based segmentation (VN)
Python
Run
KEYWORDS = {
    "fetch": ["lấy", "tải", "get"],
    "transform": ["xử lý", "lọc", "phân tích"],
    "notify": ["gửi", "báo", "thông báo"]
}
Output Example
1. "lấy dữ liệu thời tiết" → fetch
2. "lọc dữ liệu mưa" → transform
3. "báo tôi nếu có mưa" → notify

🔑 Key Fix

Planner KHÔNG được bỏ sót step

Mỗi step = 1 action

4. ⚙️ parser.py (CRITICAL FIX)
❌ Issue 1: Sai Dependency Logic
Nguyên nhân

Parser đang:

Gán dependency dựa trên keyword mơ hồ

Hoặc không giữ thứ tự tuyến tính

✅ FIX 1: Deterministic Dependency Chain
Python
Run
def assign_dependencies(actions):
    for i, action in enumerate(actions):
        if i == 0:
            action.depends_on = []
        else:
            action.depends_on = [actions[i - 1].id]

👉 Senior test EXPECTS:

Linear pipeline unless explicit branching

❌ Issue 2: Policy Detection FAIL ("báo tôi")
✅ FIX 2: Vietnamese Policy Mapping Table
Python
Run
POLICY_MAP = {
    "báo tôi": "notify_only",
    "thông báo": "notify_only",
    "dừng": "stop",
    "tiếp tục": "continue"
}
Detection Logic
Python
Run
def detect_policy(text: str) -> FailurePolicy:
    text = text.lower()

    for key, value in POLICY_MAP.items():
        if key in text:
            return FailurePolicy(value)

    return FailurePolicy.STOP  # default safe
✅ Full Parser Flow
Python
Run
def parse(plan_steps):
    actions = []

    for step in plan_steps:
        action = Action(
            id=step.step_id,
            type=map_action_type(step.action_hint),
            input=step.raw_text
        )
        actions.append(action)

    assign_dependencies(actions)

    policy = detect_policy(" ".join([s.raw_text for s in plan_steps]))

    return Workflow(actions=actions, failure_policy=policy)
5. 🛡️ validator.py
🎯 Mục tiêu

Catch lỗi trước khi compiler chạy

✅ Checks bắt buộc
1. Dependency integrity
Python
Run
def validate_dependencies(actions):
    ids = {a.id for a in actions}

    for action in actions:
        for dep in action.depends_on:
            if dep not in ids:
                raise ValueError(f"Invalid dependency: {dep}")
2. No circular dependency
Python
Run
def detect_cycle(actions):
    visited = set()

    def visit(a, path):
        if a.id in path:
            raise ValueError("Cycle detected")
        path.add(a.id)

        for dep in a.depends_on:
            visit(find_action(dep), path)

        path.remove(a.id)

    for a in actions:
        visit(a, set())
3. Policy must exist
Python
Run
if not workflow.failure_policy:
    raise ValueError("Missing failure policy")
6. 🏗️ compiler.py
❌ Hiện tại

Compiler đang làm quá nhiều (validation + logic)

✅ Fix: Compiler = Pure Transformer
Python
Run
def compile_workflow(workflow: Workflow):
    return {
        "steps": [
            {
                "id": a.id,
                "type": a.type,
                "depends_on": a.depends_on
            }
            for a in workflow.actions
        ],
        "failure_policy": workflow.failure_policy
    }
7. 🧪 Expected Behavior (PASS CASES)
Case 1: Dependency

Input:

lấy dữ liệu → xử lý → gửi

Output:

1 → []
2 → [1]
3 → [2]
Case 6: Policy

Input:

"báo tôi nếu lỗi"

Output:

failure_policy = notify_only ✅
8. 🔥 Critical Improvements Summary
Area	Fix
Dependency	Linear deterministic chain
Policy	Keyword mapping table
Planner	Force structured steps
Parser	No inference, only mapping
Validator	Graph safety
Compiler	Dumb + pure
9. 🚀 Final Insight (Quan trọng nhất)

Vấn đề của bạn không phải model yếu.
Mà là:

❌ Bạn đang build “AI system”
✅ Nhưng bài test yêu cầu “compiler system”

Nếu bạn muốn, mình có thể:

Review trực tiếp parser.py hiện tại của bạn

Hoặc 
viết luôn version pass 100/100 full code

Chỉ cần gửi file 👍