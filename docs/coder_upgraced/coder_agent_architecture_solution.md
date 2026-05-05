# Phân Tích Gốc Rễ & Giải Pháp Tổng Thể cho CoderAgent

## Vấn đề thực sự là gì?

Tôi đã sửa 3 lần, mỗi lần chỉ giải quyết được 1 lớp vấn đề:

```
Lần 1: Fix JSON → Agent tạo được file nhưng toàn rỗng
Lần 2: Fix Prompt → Agent viết logic nhưng thiếu file
Lần 3: Sẽ fix gì tiếp? → Vòng lặp vá víu vô tận
```

**Gốc rễ không nằm ở JSON, không nằm ở Prompt, mà nằm ở KIẾN TRÚC.**

---

## Phân Tích Gốc Rễ

### Kiến trúc hiện tại: "Dump & Pray"

```
┌──────────────────────────────────────┐
│          SYSTEM PROMPT (~2K)         │
├──────────────────────────────────────┤
│       BENCHMARK SPEC (~10K)          │  ← 545 dòng spec nhồi hết vào
├──────────────────────────────────────┤
│     Step 1 response (~1K)            │
│     Step 2 response (~1K)            │
│     Step 3 response (~1K)            │
│     ...                              │
│     Step N response (~1K)            │  ← Mỗi bước thêm ~1K
├──────────────────────────────────────┤
│          CONTEXT LIMIT: 32K          │  ← Qwen 14B chỉ có 32K
└──────────────────────────────────────┘
```

**Sau 10 bước**: 2K + 10K + 10×1K = **22K tokens**. Model chỉ còn 10K để "suy nghĩ".
**Sau 15 bước**: Tràn context → Model bắt đầu quên spec → Viết placeholder → Dừng sớm.

### Tại sao model dừng sớm?

Không phải vì nó "lười". Mà vì:
1. **Context đầy** → Model không còn nhớ phần nào của spec chưa implement
2. **Mất focus** → 545 dòng spec trộn lẫn với 10 observation messages → Model không biết ưu tiên gì
3. **Không có feedback loop** → Không ai kiểm tra "file này đã đủ logic chưa?" trước khi chuyển file tiếp

---

## Giải Pháp: Task Decomposition Orchestrator

Thay vì nhồi mọi thứ vào 1 session duy nhất, ta tách thành **nhiều session nhỏ**, mỗi session chỉ làm **1 việc cụ thể**.

### Kiến trúc mới

```
┌─────────────────────────────────────────────┐
│              ORCHESTRATOR                    │
│  (Python script, không dùng LLM)            │
│                                              │
│  1. Đọc spec → Tạo danh sách file cần viết  │
│  2. Với MỖI file:                            │
│     ├── Tạo session MỚI cho CoderAgent       │
│     ├── Gửi CHỈ phần spec liên quan          │
│     ├── Agent viết file đó                   │
│     ├── Orchestrator KIỂM TRA kết quả        │
│     └── Nếu thiếu logic → gửi lại           │
│  3. Khi tất cả file xong → chạy pytest       │
└─────────────────────────────────────────────┘
```

### So sánh context usage

| | Kiến trúc cũ | Kiến trúc mới |
|---|---|---|
| Context mỗi session | 22K+ (tràn) | **~5K** (gọn nhẹ) |
| Số session | 1 (làm hết) | 7 (mỗi file 1 session) |
| Focus của model | Mơ hồ (545 dòng spec) | **Rõ ràng** (chỉ ~50 dòng liên quan) |
| Kiểm tra chất lượng | Không có | **Có** (orchestrator đọc file sau khi tạo) |
| Khả năng retry | Không (hết context) | **Có** (session mới = context sạch) |

### Luồng thực thi chi tiết

```
Orchestrator
│
├── Task 1: Tạo schema.py
│   ├── Prompt: "Tạo file schema.py với VALID_NODE_TYPES = [...] và DANGEROUS_ACTIONS = [...]"
│   ├── Agent viết → Orchestrator đọc file → Kiểm tra có đủ 2 list không
│   └── ✅ Pass → Tiếp
│
├── Task 2: Tạo parser.py
│   ├── Prompt: "Tạo parser.py. Import schema.py. Parse tiếng Việt theo các rule sau: [chỉ paste phần 1-3 của spec]"
│   ├── Agent viết → Orchestrator kiểm tra: có regex không? Có ambiguity detection không?
│   └── ⚠️ Thiếu ambiguity → Gửi lại: "File parser.py thiếu ambiguity detection. Hãy thêm vào."
│
├── Task 3: Tạo planner.py
│   ├── Prompt: "Tạo planner.py. Implement topological sort + cycle detection. [paste phần 4 của spec]"
│   └── ✅ Pass
│
├── Task 4: Tạo validator.py
│   ├── Prompt: "Tạo validator.py. Import schema + planner. Validate 3 thứ: node types, dependencies, cycles."
│   └── ✅ Pass
│
├── Task 5: Tạo compiler.py + __init__.py
│   └── ✅ Pass
│
├── Task 6: Tạo tests
│   ├── Prompt: "Viết pytest cho 10 test cases sau: [paste phần test cases của spec]"
│   └── ✅ Pass
│
└── Task 7: Chạy pytest
    ├── Orchestrator chạy: pytest agent_v3_solution/tests/ -v
    ├── Nếu fail → Gửi error log cho Agent session mới → Sửa
    └── ✅ All pass → DONE
```

---

## Implementation Plan

### File cần tạo/sửa

| File | Thay đổi |
|------|----------|
| `Core/orchestrator.py` | **TẠO MỚI** — Bộ điều phối task decomposition |
| `Core/coder_agent.py` | Giữ nguyên logic Markdown Fence, chỉ sửa nhỏ để hỗ trợ single-file mode |
| `Core/task_specs/` | **TẠO MỚI** — Thư mục chứa các mini-spec cho từng file |

### Pseudo-code cho Orchestrator

```python
class TaskOrchestrator:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.tasks = []        # Danh sách task đã plan
        self.completed = []    # Task đã hoàn thành
    
    def plan(self, full_spec: str):
        """Tách spec thành danh sách task nhỏ."""
        self.tasks = [
            {"file": "schema.py",    "spec_section": extract_section(full_spec, "node types")},
            {"file": "parser.py",    "spec_section": extract_section(full_spec, "parsing + ambiguity + policy")},
            {"file": "planner.py",   "spec_section": extract_section(full_spec, "topological sort + cycle")},
            {"file": "validator.py", "spec_section": extract_section(full_spec, "validation")},
            {"file": "compiler.py",  "spec_section": "Kết hợp parser + planner + validator"},
            {"file": "__init__.py",  "spec_section": "Export compile_workflow, validate_workflow"},
            {"file": "tests/",       "spec_section": extract_section(full_spec, "test cases")},
        ]
    
    async def execute(self):
        """Thực thi từng task một, mỗi task dùng session Agent MỚI."""
        for task in self.tasks:
            success = False
            retries = 0
            
            while not success and retries < 3:
                # Session MỚI cho mỗi file → context sạch
                agent = CoderAgent(verbose=True)
                
                prompt = f"Tạo file {self.output_dir}/{task['file']}.\n\n{task['spec_section']}"
                await agent.chat(prompt)
                
                # KIỂM TRA kết quả
                success = self.validate_output(task['file'])
                if not success:
                    retries += 1
            
            self.completed.append(task['file'])
    
    def validate_output(self, filename):
        """Kiểm tra file đã tạo có logic thực không."""
        content = open(f"{self.output_dir}/{filename}").read()
        
        # Các red flag
        if "# Placeholder" in content: return False
        if "pass" == content.strip().split('\n')[-1].strip(): return False
        if "return []" in content and "topological" not in content: return False
        if len(content) < 50: return False  # File quá ngắn
        
        return True
```

### Ưu điểm của giải pháp này

1. **Không cần sửa model** — Qwen 14B vẫn là Qwen 14B, chỉ thay đổi cách ta giao việc
2. **Context luôn gọn** — Mỗi session chỉ ~5K tokens thay vì 22K+
3. **Có quality gate** — Orchestrator kiểm tra trước khi chuyển task tiếp
4. **Có retry** — Nếu file sai, tạo session mới với context sạch để sửa
5. **Tái sử dụng được** — Orchestrator dùng được cho BẤT KỲ bài benchmark nào, không chỉ workflow compiler

---

## Tóm Lại

> [!IMPORTANT]
> **Vấn đề không phải Qwen 14B yếu. Vấn đề là ta đang bắt nó làm việc của 7 người trong 1 phòng chật.**
> 
> Giải pháp: Cho nó 7 phòng riêng, mỗi phòng 1 việc, có người kiểm tra trước khi chuyển phòng tiếp.

Thay vì tiếp tục vá víu `coder_agent.py`, ta cần xây **1 tầng mới phía trên** — `orchestrator.py` — để điều phối Agent một cách có hệ thống.
