"""
Runner V5: Orchestrator + LLM Planner (Hyper Architecture)
Bài test: Senior Workflow Compiler
Hỗ trợ 3 mode:
  1. LLM Planner (Gemma 4B) → sinh plan → Coder (Qwen 14B) viết code
  2. LLM Planner (Gemma 26B MoE) → sinh plan → Coder viết code
  3. Hardcoded tasks + contract → Coder viết code (fallback)
"""
import asyncio
import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Core'))
from orchestrator import TaskOrchestrator
from code_planner import CodePlanner, PLANNER_MODEL_4B, PLANNER_MODEL_MOE, benchmark_planners

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'agent_v5_test')
EVAL_SCRIPT = os.path.join(os.path.dirname(__file__), 'evaluate_coder_v4.py')

# ============================================================================
# BENCHMARK SPEC — Toàn bộ yêu cầu bài test Senior Workflow Compiler
# LLM Planner sẽ đọc spec này và tự sinh contract + tasks
# ============================================================================
BENCHMARK_SPEC = """
## Senior Coding Benchmark: Natural Language Workflow Compiler

Tạo một Python package `agent_v4_solution/` gồm 6 file:
schema.py, parser.py, planner.py, validator.py, compiler.py, __init__.py

### schema.py
- VALID_NODE_TYPES: list 14 phần tử (email.read, email.search, email.draft, email.send, 
  email.delete, text.summarize, text.extract_action_items, calendar.create_event, 
  calendar.delete_event, file.save_attachment, file.delete, search.web, system.restart, notify.user)
- DANGEROUS_ACTIONS: list 5 phần tử (email.send, email.delete, file.delete, system.restart, calendar.delete_event)

### parser.py 
- def parse_natural_language(text: str) -> dict
- Map tiếng Việt → node type: "đọc email"→email.read, "xoá email"→email.delete, 
  "tóm tắt"→text.summarize, "tạo lịch"/"lịch họp"→calendar.create_event, etc.
- Return: {"nodes": [list of {"id","type","params","depends_on"}], "requires_confirmation": bool, 
  "ambiguity": bool, "failure_policy": str|None}
- requires_confirmation = True nếu node type thuộc DANGEROUS_ACTIONS
- ambiguity = True nếu câu < 5 từ hoặc có đại từ mơ hồ (nó, đó, cái đó)
- failure_policy: "báo tôi"→"notify_only", "thử lại"→"retry", "bỏ qua"→"skip"

### planner.py
- def get_execution_order(nodes: list[dict]) -> list[str]
- Topological sort, raise ValueError("Cycle detected") nếu có cycle

### validator.py
- def validate_workflow(workflow: dict) -> tuple[bool, list[str]]
- Input: dict có key "nodes" là LIST (không phải dict)
- Validate: node types hợp lệ, depends_on trỏ tới id tồn tại, không có cycle

### compiler.py
- def compile_workflow(user_input: str) -> dict
- Gọi parse → plan → validate
- Return flat dict: nodes, requires_confirmation, ambiguity, failure_policy, 
  execution_order, is_valid, errors

### __init__.py
- Export: compile_workflow, validate_workflow, get_execution_order

### Test Cases bài test sẽ kiểm tra:
1. "đọc email mới nhất rồi tóm tắt" → 2 nodes, dependency đúng, execution order đúng
2. "xoá email quảng cáo mới nhất" → requires_confirmation = True
3. "xoá nó đi" → ambiguity = True (câu ngắn + đại từ mơ hồ)
4. "tạo lịch họp chiều mai, nếu lỗi thì báo tôi" → failure_policy = "notify_only"
5. Cycle detection: nodes với circular depends_on → raise ValueError
"""

# ============================================================================
# FALLBACK: Hardcoded contract + tasks (nếu không dùng LLM Planner)
# ============================================================================
HARDCODED_CONTRACT = """
## Senior Coding Benchmark: Natural Language Workflow Compiler

Tạo một Python package `agent_v4_solution/` gồm 6 file:
schema.py, parser.py, planner.py, validator.py, compiler.py, __init__.py

### schema.py
- VALID_NODE_TYPES: list 14 phần tử (email.read, email.search, email.draft, email.send, email.delete,
  text.summarize, text.extract_action_items, calendar.create_event, calendar.delete_event,
  file.save_attachment, file.delete, search.web, system.restart, notify.user)
- DANGEROUS_ACTIONS: list 5 phần tử (email.send, email.delete, file.delete, system.restart, calendar.delete_event)

### parser.py
- def parse_natural_language(text: str) -> dict
- Map tiếng Việt → node type: "đọc email"→email.read, "xoá email"→email.delete,
  "gửi email"→email.send, "tóm tắt"→text.summarize, "tạo lịch"/"lịch họp"→calendar.create_event
- Return: {"nodes": [{"id","type","params","depends_on": [list of id strings]},...],
  "requires_confirmation": bool, "ambiguity": bool, "failure_policy": str|None}
- requires_confirmation = True nếu BẤT KỲ node type nào thuộc DANGEROUS_ACTIONS
- ambiguity = True nếu câu < 5 từ HOẶC có đại từ mơ hồ (nó, đó, cái đó)
- failure_policy: "báo tôi"→"notify_only", "thử lại"→"retry", "bỏ qua"→"skip"

### planner.py
- def get_execution_order(nodes: list[dict]) -> list[str]
- Topological sort (Kahn's Algorithm), raise ValueError("Cycle detected") nếu có cycle
- Input: nodes là list of dict có "id" (str) và "depends_on" (list[str])

### validator.py
- def validate_workflow(workflow: dict) -> tuple[bool, list[str]]
- Input: dict có key "nodes" là LIST (không phải dict)
- Validate: node types hợp lệ, depends_on trỏ tới id tồn tại, không có cycle

### compiler.py
- def compile_workflow(user_input: str) -> dict
- Pipeline: parse → plan → validate
- Return flat dict: {nodes, requires_confirmation, ambiguity, failure_policy,
  execution_order, is_valid, errors}

### __init__.py
- Export: compile_workflow, validate_workflow, get_execution_order

### TEST CASES BÀI TEST SẼ KIỂM TRA:
1. compile_workflow("đọc email mới nhất rồi tóm tắt") → 2 nodes (email.read + text.summarize), dependency, order
2. compile_workflow("xoá email quảng cáo mới nhất") → requires_confirmation = True
3. compile_workflow("xoá nó đi") → ambiguity = True (câu ngắn + đại từ mơ hồ)
4. compile_workflow("tạo lịch họp chiều mai, nếu lỗi thì báo tôi") → failure_policy = "notify_only"
5. get_execution_order([circular deps]) → raise ValueError("Cycle detected")
"""


def build_hardcoded_tasks(output_dir: str) -> list[dict]:
    """Hardcoded tasks (fallback nếu LLM Planner không hoạt động)."""
    return [
        {
            "file": "schema.py",
            "prompt": f"""Tạo file {output_dir}/schema.py chứa:
1. VALID_NODE_TYPES = list 14 phần tử:
   email.read, email.search, email.draft, email.send, email.delete,
   text.summarize, text.extract_action_items,
   calendar.create_event, calendar.delete_event,
   file.save_attachment, file.delete,
   search.web, system.restart, notify.user
2. DANGEROUS_ACTIONS = list 5 phần tử:
   email.send, email.delete, file.delete, system.restart, calendar.delete_event""",
            "checks": ["VALID_NODE_TYPES", "DANGEROUS_ACTIONS", "email.read", "system.restart"]
        },
        {
            "file": "parser.py",
            "prompt": f"""Tạo file {output_dir}/parser.py
IMPORT: from schema import VALID_NODE_TYPES, DANGEROUS_ACTIONS

def parse_natural_language(text: str) -> dict:
    Return dict theo CONTRACT.

KEYWORD MAP BẮT BUỘC (dùng chính xác dict này):
    keyword_map = {{
        "đọc email": "email.read",
        "tìm email": "email.search",
        "xoá email": "email.delete",
        "xóa email": "email.delete",
        "gửi email": "email.send",
        "tóm tắt": "text.summarize",
        "tạo lịch": "calendar.create_event",
        "lịch họp": "calendar.create_event",
        "lưu file": "file.save_attachment",
        "xoá file": "file.delete",
        "xóa file": "file.delete",
        "xoá": "email.delete",
        "xóa": "email.delete",
        "tìm": "search.web",
    }}

LOGIC BẮT BUỘC:
1. Split text theo: dấu phẩy, "sau đó", "rồi", "tiếp theo"
2. Lặp qua từng phần, scan keyword_map (so khớp chuỗi con dài nhất trước)
3. Tạo node cho mỗi keyword match: {{"id": "n1", "type": matched_type, "params": {{}}, "depends_on": []}}
4. DEPENDENCY: node[i]["depends_on"] = [node[i-1]["id"]] (list chứa id string)
5. AMBIGUITY: len(text.split()) < 5 HOẶC bất kỳ từ nào trong ["nó", "đó", "cái"] xuất hiện → True
6. CONFIRMATION: nếu BẤT KỲ node nào có type thuộc DANGEROUS_ACTIONS → True
7. FAILURE POLICY: nếu "báo tôi" in text → "notify_only", "thử lại" → "retry", "bỏ qua" → "skip", else None

## FEW-SHOT EXAMPLES (phải pass chính xác):

Input: "đọc email mới nhất rồi tóm tắt"
Output: {{
  "nodes": [
    {{"id": "n1", "type": "email.read", "params": {{}}, "depends_on": []}},
    {{"id": "n2", "type": "text.summarize", "params": {{}}, "depends_on": ["n1"]}}
  ],
  "requires_confirmation": false,
  "ambiguity": false,
  "failure_policy": null
}}

Input: "xoá email quảng cáo mới nhất"
Output: {{"nodes": [...], "requires_confirmation": true, "ambiguity": false, ...}}
(requires_confirmation=true vì email.delete ∈ DANGEROUS_ACTIONS)

Input: "xoá nó đi"
Output: {{"nodes": [...], "requires_confirmation": true, "ambiguity": true, ...}}
(ambiguity=true vì câu < 5 từ VÀ có "nó")

Input: "tạo lịch họp chiều mai, nếu lỗi thì báo tôi"
Output: {{"nodes": [...], "failure_policy": "notify_only", ...}}
""",
            "checks": ["def parse_natural_language", "ambiguity", "requires_confirmation", "failure_policy", "depends_on", "DANGEROUS_ACTIONS"]
        },
        {
            "file": "planner.py",
            "prompt": f"""Tạo file {output_dir}/planner.py

def get_execution_order(nodes: list[dict]) -> list[str]:
    Input: list of dict có "id" và "depends_on"
    Output: list of id strings (topological order)
    Nếu có cycle: raise ValueError("Cycle detected")
    Dùng Kahn's Algorithm.""",
            "checks": ["def get_execution_order", "ValueError", "depends_on"]
        },
        {
            "file": "validator.py",
            "prompt": f"""Tạo file {output_dir}/validator.py
IMPORT: from schema import VALID_NODE_TYPES
IMPORT: from planner import get_execution_order

def validate_workflow(workflow: dict) -> tuple[bool, list[str]]:
    Truy cập nodes: workflow["nodes"] (LIST)
    Validate: type ∈ VALID_NODE_TYPES, depends_on trỏ id tồn tại, no cycle""",
            "checks": ["def validate_workflow", "VALID_NODE_TYPES", "get_execution_order", "errors"]
        },
        {
            "file": "compiler.py",
            "prompt": f"""Tạo file {output_dir}/compiler.py
IMPORT: from parser import parse_natural_language
IMPORT: from planner import get_execution_order
IMPORT: from validator import validate_workflow

def compile_workflow(user_input: str) -> dict:
    1. raw = parse_natural_language(user_input)
    2. try: execution_order = get_execution_order(raw["nodes"])
       except ValueError: execution_order = [], errors thêm cycle msg
    3. is_valid, errors = validate_workflow(raw)
    4. Return FLAT dict (merge raw + thêm execution_order, is_valid, errors):

EXACT OUTPUT STRUCTURE:
{{
    "nodes": raw["nodes"],           # list of node dicts
    "requires_confirmation": raw["requires_confirmation"],  # bool
    "ambiguity": raw["ambiguity"],   # bool
    "failure_policy": raw["failure_policy"],  # str or None
    "execution_order": execution_order,  # list of id strings
    "is_valid": is_valid,            # bool
    "errors": errors                 # list of str
}}""",
            "checks": ["def compile_workflow", "parse_natural_language", "get_execution_order", "execution_order"]
        },
        {
            "file": "__init__.py",
            "prompt": f"""Tạo file {output_dir}/__init__.py:
from compiler import compile_workflow
from validator import validate_workflow
from planner import get_execution_order""",
            "checks": ["compile_workflow"]
        }
    ]


async def run_with_planner(planner_model: str):
    """Mode 1/2: LLM Planner → Orchestrator → Coder → Validator"""
    print(f"\n{'='*60}")
    print(f"  MODE: LLM PLANNER ({planner_model})")
    print(f"  Architecture: Hyper 3-Layer (giống Brain V5)")
    print(f"{'='*60}")

    import shutil
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)

    start = time.time()

    orchestrator = TaskOrchestrator(
        output_dir=OUTPUT_DIR,
        planner_model=planner_model,
        verbose=True
    )

    # Phase 0: LLM Planner đọc spec → sinh plan
    tasks = await orchestrator.plan_from_spec(BENCHMARK_SPEC)

    # Phase 2+3: Coder viết code + Integration test
    await orchestrator.run(
        tasks,
        max_retries=2,
        eval_script=EVAL_SCRIPT,
        eval_max_retries=2
    )

    elapsed = time.time() - start
    print(f"\n  Total time: {elapsed:.1f}s")
    orchestrator.report()
    return orchestrator.results


async def run_with_hardcoded():
    """Mode 3: Hardcoded tasks (fallback)"""
    print(f"\n{'='*60}")
    print(f"  MODE: HARDCODED TASKS + CONTRACT")
    print(f"{'='*60}")

    import shutil
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)

    start = time.time()

    orchestrator = TaskOrchestrator(
        output_dir=OUTPUT_DIR,
        contract=HARDCODED_CONTRACT,
        verbose=True
    )

    tasks = build_hardcoded_tasks(OUTPUT_DIR)
    await orchestrator.run(
        tasks,
        max_retries=2,
        eval_script=EVAL_SCRIPT,
        eval_max_retries=2
    )

    elapsed = time.time() - start
    print(f"\n  Total time: {elapsed:.1f}s")
    orchestrator.report()
    return orchestrator.results


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Coder V5 Benchmark Runner")
    parser.add_argument("--mode", choices=["4b", "moe", "hardcoded", "benchmark_planners"],
                        default="hardcoded",
                        help="4b=Gemma4B planner, moe=Gemma26B MoE planner, "
                             "hardcoded=no planner, benchmark_planners=compare both")
    args = parser.parse_args()

    if args.mode == "benchmark_planners":
        # Chỉ benchmark 2 planner, không chạy coder
        await benchmark_planners(BENCHMARK_SPEC, OUTPUT_DIR)
    elif args.mode == "4b":
        await run_with_planner(PLANNER_MODEL_4B)
    elif args.mode == "moe":
        await run_with_planner(PLANNER_MODEL_MOE)
    else:
        await run_with_hardcoded()


if __name__ == "__main__":
    asyncio.run(main())
