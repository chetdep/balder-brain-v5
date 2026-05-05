import json
import os

def self_audit(limit: int = 5) -> str:
    """
    Công cụ tự kiểm toán (Self-Audit). 
    Đọc lại lịch sử các Trace ID gần nhất để học hỏi kinh nghiệm.
    """
    log_file = "traces.jsonl"
    if not os.path.exists(log_file):
        return "Không tìm thấy dữ liệu trace nào để kiểm toán."
        
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        recent_traces = [json.loads(line) for line in lines[-limit:]]
        
        output = "### KẾT QUẢ TỰ KIỂM TOÁN (SELF-AUDIT)\n\n"
        for trace in recent_traces:
            output += f"- **Trace ID**: {trace['trace_id']}\n"
            output += f"  - Input: {trace['user_input']}\n"
            output += f"  - Status: {trace.get('status', 'unknown')}\n"
            if trace.get('plan'):
                output += f"  - Plan: {json.dumps(trace['plan'], ensure_ascii=False)}\n"
            output += "\n"
            
        return output
    except Exception as e:
        return f"Lỗi khi thực hiện Self-Audit: {str(e)}"
