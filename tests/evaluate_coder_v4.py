import os
import json
import sys

# Resolve paths từ project root (parent of tests/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLUTION_DIR = os.environ.get('AGENT_SOLUTION_DIR', os.path.join(PROJECT_ROOT, 'agent_v4_solution'))
sys.path.insert(0, SOLUTION_DIR)

def test_workflow_compiler():
    print("=" * 60)
    print("  EVALUATING WORKFLOW COMPILER (AGENT V4)")
    print("=" * 60)

    score = 0
    max_score = 100
    
    # 1. Kiểm tra tồn tại thư mục và file
    if not os.path.exists(SOLUTION_DIR):
        print("❌ Lỗi: Thư mục agent_v4_solution không tồn tại!")
        return 0
        
    expected_files = ["schema.py", "parser.py", "planner.py", "validator.py", "compiler.py", "__init__.py"]
    missing_files = []
    for f in expected_files:
        if not os.path.exists(os.path.join(SOLUTION_DIR, f)):
            missing_files.append(f)
            
    if missing_files:
        print(f"❌ Thiếu file: {', '.join(missing_files)}")
        score += 0 # Package Structure (0/10)
    else:
        print("✅ Đủ các file cơ bản.")
        score += 5 # Package Structure (5/10)

    try:
        from compiler import compile_workflow
        from validator import validate_workflow
        from planner import get_execution_order
        print("✅ Import thành công compiler, validator, planner.")
        score += 5 # Package Structure (10/10)
    except Exception as e:
        print(f"❌ Lỗi import: {e}")
        return score

    # 2. Test Cases (Theo spec)
    print("\n--- Chạy Test Cases ---")
    
    # Case 1: Simple Chain
    try:
        wf1 = compile_workflow("đọc email mới nhất rồi tóm tắt")
        nodes = wf1.get("nodes", [])
        types = [n["type"] for n in nodes]
        if "email.read" in types and "text.summarize" in types:
            print("✅ Case 1: Đọc và tóm tắt (Parser)")
            score += 10 # Parser (10/20)
            
            # Check dependency
            sum_node = next(n for n in nodes if n["type"] == "text.summarize")
            read_node = next(n for n in nodes if n["type"] == "email.read")
            if read_node["id"] in sum_node.get("depends_on", []):
                print("✅ Case 1: Dependency resolution")
                score += 5 # Dependency (5/15)
            else:
                 print("❌ Case 1: Sai dependency")
                 
            # Check execution order
            order = wf1.get("execution_order", [])
            if order.index(read_node["id"]) < order.index(sum_node["id"]):
                 print("✅ Case 1: Execution order")
                 score += 5 # Planner (5/15)
            else:
                 print("❌ Case 1: Sai execution order")
        else:
            print("❌ Case 1: Thiếu node")
    except Exception as e:
        print(f"❌ Case 1 Lỗi: {e}")

    # Case 3: Dangerous Action
    try:
        wf3 = compile_workflow("xoá email quảng cáo mới nhất")
        if wf3.get("requires_confirmation") is True:
            print("✅ Case 3: Nhận diện Dangerous Action")
            score += 5 # Graph Design (5/15)
        else:
            print("❌ Case 3: Không nhận diện được Dangerous Action")
    except Exception as e:
         print(f"❌ Case 3 Lỗi: {e}")
         
    # Case 4: Ambiguous Action
    try:
        wf4 = compile_workflow("xoá nó đi")
        if wf4.get("ambiguity") is True:
             print("✅ Case 4: Nhận diện Ambiguity")
             score += 10 # Ambiguity (10/10)
        else:
             print("❌ Case 4: Không nhận diện được Ambiguity")
    except Exception as e:
         print(f"❌ Case 4 Lỗi: {e}")

    # Case 6: Failure Policy
    try:
         wf6 = compile_workflow("tạo lịch họp chiều mai, nếu lỗi thì báo tôi")
         if wf6.get("failure_policy") == "notify_only" or "notify_only" in str(wf6.get("failure_policy", "")):
              print("✅ Case 6: Nhận diện Failure Policy")
              score += 5 # Policy (5/10)
         else:
              print("❌ Case 6: Không nhận diện được Failure Policy")
    except Exception as e:
          print(f"❌ Case 6 Lỗi: {e}")

    # Case 10: Cycle Detection
    try:
        nodes = [
            {"id": "n1", "type": "email.read", "depends_on": ["n2"]},
            {"id": "n2", "type": "text.summarize", "depends_on": ["n1"]}
        ]
        get_execution_order(nodes)
        print("❌ Case 10: KHÔNG phát hiện được Cycle")
    except ValueError:
        print("✅ Case 10: Phát hiện Cycle thành công (raise ValueError)")
        score += 10 # Planner (15/15)
    except Exception as e:
         print(f"❌ Case 10 Lỗi không mong đợi: {e}")
         
    print(f"\n✅ ĐIỂM TỔNG CỘNG: {score} / {max_score}")
    
    if score >= 85:
        print("Đánh giá: Rất Mạnh / Mạnh (Sẵn sàng production)")
    elif score >= 75:
        print("Đánh giá: Khá (Cần human review)")
    elif score >= 60:
         print("Đánh giá: Trung bình (Có khung logic cơ bản)")
    else:
         print("Đánh giá: Yếu (Chưa phù hợp làm workflow phức tạp)")

if __name__ == "__main__":
    test_workflow_compiler()
