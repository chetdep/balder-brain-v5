import uuid
import time
import json
import os

class BalderTelemetry:
    """
    Hệ thống Telemetry cho Balder Brain V5.
    Quản lý Trace ID và ghi lại đồ thị thực thi (Execution Graph).
    
    Performance: Batch flush — chỉ ghi file khi end_trace() được gọi,
    tránh I/O overhead khi add_node() liên tục trong ReAct loop.
    """
    
    def __init__(self, log_file="traces.jsonl"):
        self.log_file = log_file
        self.current_trace_id = None
        self.current_turn_data = {}
        self._dirty = False  # Track nếu có dữ liệu chưa flush

    def start_trace(self, user_input, intent_analysis=None, model_name="gemma-e4b"):
        """Khởi tạo một trace mới theo blueprint."""
        # Flush trace cũ nếu còn pending (phòng trường hợp end_trace bị skip)
        if self._dirty:
            self._flush()
        
        self.current_trace_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.current_turn_data = {
            "trace_id": self.current_trace_id,
            "input": user_input,
            "route_plan": {},
            "tool_calls": [],
            "outcome": {},
            "latency_ms": 0,
            "model": model_name,
            "error": None
        }
        self._dirty = True
        return self.current_trace_id

    def record_plan(self, route_plan):
        """Ghi lại bản kế hoạch định tuyến (TurnRoutePlan)."""
        self.current_turn_data["route_plan"] = route_plan
        # Không flush — sẽ được ghi cùng lúc ở end_trace()

    def add_node(self, node_type, content, metadata=None):
        """Ghi nhận tool call hoặc thought (buffered, không flush ngay)."""
        if node_type == "thought":
            self.current_turn_data["thought"] = content
        elif node_type == "action":
            self.current_turn_data["tool_calls"].append({
                "action": content,
                "input": metadata.get("input", {}) if metadata else {}
            })
        elif node_type == "observation":
            # Ghi observation vào tool_call gần nhất (nếu có)
            if self.current_turn_data["tool_calls"]:
                self.current_turn_data["tool_calls"][-1]["observation"] = content[:500]
        # Không flush — batch write ở end_trace()

    def end_trace(self, final_response, status="handled", error=None):
        """Kết thúc trace, tính latency, và flush toàn bộ dữ liệu ra file."""
        self.current_turn_data["outcome"] = {
            "status": status,
            "reason": final_response[:200],  # Tóm tắt kết quả
            "retryable": False
        }
        self.current_turn_data["latency_ms"] = int((time.time() - self.start_time) * 1000)
        self.current_turn_data["error"] = error
        self._flush()

    def _flush(self):
        """Ghi dữ liệu vào file log (JSONL). Chỉ gọi khi thực sự cần."""
        if not self._dirty:
            return
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.current_turn_data, ensure_ascii=False) + "\n")
            self._dirty = False
        except Exception as e:
            print(f"Telemetry Error: {e}")

