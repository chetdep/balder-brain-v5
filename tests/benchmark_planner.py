"""
Benchmark chuyên biệt cho Code Planner (Gemma 26B MoE).

Đánh giá 5 tiêu chí:
  1. Plan Completeness   — Có đủ 6 file blocks không?
  2. Function Coverage   — Có extract đủ function signatures không?
  3. Contract Quality    — Contract có đủ chi tiết (keyword map, data types)?
  4. Domain Specificity  — Có nhắc đến tiếng Việt keywords, DANGEROUS_ACTIONS?
  5. Task Prompt Quality — Prompt sinh ra cho Coder có đủ context không?

Chạy: python tests/benchmark_planner.py
"""
import asyncio
import sys
import os
import time
import re
import json

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Core'))
from code_planner import CodePlanner, PLANNER_MODEL_4B, PLANNER_MODEL_MOE

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'agent_v4_solution')

# ============================================================================
# BENCHMARK SPEC — Giống bài test Senior Workflow Compiler
# ============================================================================
BENCHMARK_SPEC = """
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
# SCORING: Đánh giá chất lượng plan output
# ============================================================================
def score_plan(plan: dict, model_name: str) -> dict:
    """Chấm điểm plan output theo 5 tiêu chí."""
    results = {
        "model": model_name,
        "scores": {},
        "details": {},
        "total": 0,
        "max": 100
    }
    
    reasoning = plan.get("reasoning", "")
    contract = plan.get("contract", "")
    tasks = plan.get("tasks", [])
    
    # ── Tiêu chí 1: Plan Completeness (20 điểm) ──
    # Kiểm tra xem LLM có phát hiện đủ 6 file không
    expected_files = {"schema.py", "parser.py", "planner.py", "validator.py", "compiler.py", "__init__.py"}
    # Normalize: strip directory prefix (MoE may write "agent_v4_solution/parser.py")
    found_files = {os.path.basename(t["file"]) for t in tasks}
    matched = expected_files & found_files
    completeness_score = int(len(matched) / len(expected_files) * 20)
    
    results["scores"]["1_completeness"] = completeness_score
    results["details"]["1_completeness"] = {
        "found": sorted(found_files),
        "missing": sorted(expected_files - found_files),
        "extra": sorted(found_files - expected_files)
    }
    
    # ── Tiêu chí 2: Function Coverage (20 điểm) ──
    # Kiểm tra function signatures trong reasoning
    required_functions = {
        "parse_natural_language": False,
        "get_execution_order": False,
        "validate_workflow": False,
        "compile_workflow": False,
        "VALID_NODE_TYPES": False,
        "DANGEROUS_ACTIONS": False,
    }
    
    reasoning_lower = reasoning.lower()
    for fn in required_functions:
        if fn.lower() in reasoning_lower:
            required_functions[fn] = True
    
    fn_found = sum(1 for v in required_functions.values() if v)
    fn_score = int(fn_found / len(required_functions) * 20)
    
    results["scores"]["2_function_coverage"] = fn_score
    results["details"]["2_function_coverage"] = {
        "found": [k for k, v in required_functions.items() if v],
        "missing": [k for k, v in required_functions.items() if not v]
    }
    
    # ── Tiêu chí 3: Contract Quality (20 điểm) ──
    contract_checks = {
        "has_data_types": bool(re.search(r'(list|dict|str|bool|tuple)', contract, re.I)),
        "has_return_type": bool(re.search(r'(->|return|→)', contract, re.I)),
        "sufficient_length": len(contract) >= 200,
        "has_dependencies": bool(re.search(r'(import|depend|from)', contract, re.I)),
    }
    
    contract_score = sum(5 for v in contract_checks.values() if v)
    
    results["scores"]["3_contract_quality"] = contract_score
    results["details"]["3_contract_quality"] = contract_checks
    results["details"]["3_contract_length"] = len(contract)
    
    # ── Tiêu chí 4: Domain Specificity (20 điểm) ──
    # Kiểm tra xem LLM có đề cập đến domain-specific terms không
    domain_checks = {
        "vietnamese_keywords": bool(re.search(r'(đọc email|xoá email|tóm tắt|tạo lịch|lịch họp)', reasoning)),
        "dangerous_actions_list": bool(re.search(r'(email\.send|email\.delete|file\.delete|system\.restart)', reasoning)),
        "ambiguity_detection": bool(re.search(r'(ambig|mơ hồ|nó.*đó|pronoun)', reasoning, re.I)),
        "failure_policy": bool(re.search(r'(failure.policy|notify_only|báo tôi|retry)', reasoning, re.I)),
        "topological_sort": bool(re.search(r'(topolog|kahn|cycle|BFS)', reasoning, re.I)),
    }
    
    domain_found = sum(1 for v in domain_checks.values() if v)
    domain_score = int(domain_found / len(domain_checks) * 20)
    
    results["scores"]["4_domain_specificity"] = domain_score
    results["details"]["4_domain_specificity"] = domain_checks
    
    # ── Tiêu chí 5: Task Prompt Quality (20 điểm) ──
    # Kiểm tra quality của task prompts sinh ra cho Coder
    prompt_checks = {
        "parser_has_keyword_map": False,
        "parser_has_logic_rules": False,
        "planner_has_algorithm": False,
        "compiler_has_pipeline": False,
        "has_test_cases": False,
    }
    
    for task in tasks:
        prompt = task.get("prompt", "").lower()
        fname = os.path.basename(task["file"])
        if "parser" in fname:
            if any(kw in prompt for kw in ["keyword_map", "đọc email", "email.read"]):
                prompt_checks["parser_has_keyword_map"] = True
            if any(kw in prompt for kw in ["ambig", "dangerous", "failure", "confirm"]):
                prompt_checks["parser_has_logic_rules"] = True
        elif "planner" in fname:
            if any(kw in prompt for kw in ["kahn", "topolog", "bfs", "cycle"]):
                prompt_checks["planner_has_algorithm"] = True
        elif "compiler" in fname:
            if any(kw in prompt for kw in ["parse", "validate", "execution_order"]):
                prompt_checks["compiler_has_pipeline"] = True
    
    # Check reasoning for test cases
    if re.search(r'(test.case|kiểm tra|đọc email.*tóm tắt)', reasoning, re.I):
        prompt_checks["has_test_cases"] = True
    
    prompt_found = sum(1 for v in prompt_checks.values() if v)
    prompt_score = int(prompt_found / len(prompt_checks) * 20)
    
    results["scores"]["5_task_prompt_quality"] = prompt_score
    results["details"]["5_task_prompt_quality"] = prompt_checks
    
    # ── Tổng điểm ──
    results["total"] = sum(results["scores"].values())
    
    return results


def print_report(results: dict, latency: float, reasoning_len: int):
    """In báo cáo chi tiết."""
    print(f"\n{'='*70}")
    print(f"  PLANNER BENCHMARK REPORT — {results['model']}")
    print(f"{'='*70}")
    print(f"  Latency: {latency:.1f}s | Reasoning: {reasoning_len} chars")
    print(f"{'─'*70}")
    
    for key, score in results["scores"].items():
        label = key.split("_", 1)[1].replace("_", " ").title()
        bar = "█" * (score // 2) + "░" * ((20 - score) // 2)
        status = "✅" if score >= 16 else ("⚠️" if score >= 8 else "❌")
        print(f"  {status} {label:<25} {bar} {score:>2}/20")
    
    print(f"{'─'*70}")
    total = results["total"]
    grade = "🏆 Xuất sắc" if total >= 85 else ("✅ Tốt" if total >= 70 else ("⚠️ Trung bình" if total >= 50 else "❌ Yếu"))
    print(f"  TỔNG ĐIỂM: {total}/{results['max']}  — {grade}")
    
    # Chi tiết lỗi
    print(f"\n{'─'*70}")
    print(f"  CHI TIẾT:")
    
    details = results["details"]
    
    # Missing files
    missing = details.get("1_completeness", {}).get("missing", [])
    if missing:
        print(f"  ❌ Files thiếu: {', '.join(missing)}")
    
    # Missing functions
    fn_missing = details.get("2_function_coverage", {}).get("missing", [])
    if fn_missing:
        print(f"  ❌ Functions thiếu: {', '.join(fn_missing)}")
    
    # Contract quality
    cq = details.get("3_contract_quality", {})
    for k, v in cq.items():
        if not v:
            print(f"  ❌ Contract: {k} = False")
    print(f"  📏 Contract length: {details.get('3_contract_length', 0)} chars")
    
    # Domain specificity
    ds = details.get("4_domain_specificity", {})
    for k, v in ds.items():
        if not v:
            print(f"  ❌ Domain: {k} missing")
    
    # Task prompt quality
    tq = details.get("5_task_prompt_quality", {})
    for k, v in tq.items():
        if not v:
            print(f"  ❌ Task: {k} = False")


async def benchmark_single_model(model_id: str, model_name: str):
    """Benchmark 1 model planner."""
    print(f"\n{'='*70}")
    print(f"  BENCHMARKING: {model_name} ({model_id})")
    print(f"{'='*70}")
    
    planner = CodePlanner(model=model_id, verbose=True)
    
    start = time.time()
    plan = await planner.plan(BENCHMARK_SPEC, OUTPUT_DIR)
    latency = time.time() - start
    
    # Score
    results = score_plan(plan, model_name)
    reasoning_len = len(plan.get("reasoning", ""))
    
    # Report
    print_report(results, latency, reasoning_len)
    
    return {
        "model": model_name,
        "model_id": model_id,
        "latency": latency,
        "reasoning_len": reasoning_len,
        "contract_len": len(plan.get("contract", "")),
        "num_tasks": len(plan.get("tasks", [])),
        "scores": results["scores"],
        "total": results["total"],
        "plan": plan,  # Keep full plan for comparison
    }


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Code Planner Benchmark")
    parser.add_argument("--model", choices=["4b", "moe", "both"], default="moe",
                        help="4b=Gemma4B, moe=Gemma26B MoE, both=compare")
    args = parser.parse_args()
    
    results = {}
    
    if args.model in ("moe", "both"):
        results["moe"] = await benchmark_single_model(PLANNER_MODEL_MOE, "Gemma 26B MoE")
    
    if args.model in ("4b", "both"):
        results["4b"] = await benchmark_single_model(PLANNER_MODEL_4B, "Gemma 4B")
    
    if len(results) == 2:
        # Comparison table
        print(f"\n{'='*70}")
        print(f"  COMPARISON TABLE")
        print(f"{'='*70}")
        print(f"  {'Metric':<30} {'Gemma 4B':<20} {'Gemma 26B MoE':<20}")
        print(f"  {'─'*70}")
        
        r4b = results.get("4b", {})
        rmoe = results.get("moe", {})
        
        metrics = [
            ("Latency", f"{r4b.get('latency', 0):.1f}s", f"{rmoe.get('latency', 0):.1f}s"),
            ("Reasoning Length", f"{r4b.get('reasoning_len', 0)} chars", f"{rmoe.get('reasoning_len', 0)} chars"),
            ("Contract Length", f"{r4b.get('contract_len', 0)} chars", f"{rmoe.get('contract_len', 0)} chars"),
            ("Tasks Found", str(r4b.get("num_tasks", 0)), str(rmoe.get("num_tasks", 0))),
        ]
        
        # Add score comparisons
        for key in (r4b.get("scores") or rmoe.get("scores", {})):
            label = key.split("_", 1)[1].replace("_", " ").title()
            s4b = r4b.get("scores", {}).get(key, 0)
            smoe = rmoe.get("scores", {}).get(key, 0)
            metrics.append((label, f"{s4b}/20", f"{smoe}/20"))
        
        metrics.append(("TOTAL", f"{r4b.get('total', 0)}/100", f"{rmoe.get('total', 0)}/100"))
        
        for label, v4b, vmoe in metrics:
            print(f"  {label:<30} {v4b:<20} {vmoe:<20}")
    
    # Save results
    output_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'coder_upgraced', 'planner_benchmark_results.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Remove non-serializable plan data
    save_data = {}
    for k, v in results.items():
        save_data[k] = {key: val for key, val in v.items() if key != "plan"}
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)
    print(f"\n  📄 Results saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
