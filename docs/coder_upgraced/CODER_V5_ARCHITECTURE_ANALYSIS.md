# Phân Tích Tổng Thể: Coder Agent vs Core Agent Architecture

## 1. So Sánh Kiến Trúc: Core Agent (96%) vs Coder Agent (40%)

### Core Agent — Đạt 96% routing accuracy

```
┌─────────────────────────────────────────────────────────────┐
│                    CORE AGENT (agent_core.py)                │
│                                                             │
│  TẦNG 1: ContextEnricher (Rule-based, KHÔNG gọi LLM)       │
│  ├── IntentClassifier: regex + keyword → IntentType         │
│  ├── ActionPipeline: parse "rồi", "sau đó" → multi-step    │
│  ├── AmbiguityDetector: đại từ mơ hồ, câu ngắn             │
│  ├── CapabilityMap: office.email, web.search, etc.          │
│  └── Output: IntentAnalysis + enriched_prompt               │
│           ↓                                                 │
│  TẦNG 2: LLM (Gemma 4B / Qwen 14B)                         │
│  ├── CHỈ LÀM: Thought + Action (suy luận + chọn tool)      │
│  ├── KHÔNG LÀM: sinh JSON, routing, intent classification  │
│  └── Output: text thuần "Thought: ... Action: ..."          │
│           ↓                                                 │
│  TẦNG 3: Auto-Serializer (Python code, KHÔNG phải LLM)      │
│  ├── Đọc IntentAnalysis (Tầng 1) + Action (Tầng 2)         │
│  ├── Tự generate TurnRoutePlan JSON chuẩn 100%              │
│  └── Execute tool + Telemetry                               │
└─────────────────────────────────────────────────────────────┘
```

**Bí quyết 96%**: LLM KHÔNG BAO GIỜ phải làm việc mà nó yếu (sinh JSON phức tạp).
Python code làm hết phần "cấu trúc hoá". LLM chỉ tập trung "suy luận".

### Coder Agent — Đạt 40% benchmark

```
┌─────────────────────────────────────────────────────────────┐
│                    CODER AGENT (hiện tại)                    │
│                                                             │
│  TẦNG 1: KHÔNG CÓ                                          │
│  ├── ❌ Không có IntentClassifier                            │
│  ├── ❌ Không có ContextEnricher                             │
│  ├── ❌ Không có pre-analysis trước khi gọi LLM             │
│           ↓                                                 │
│  TẦNG 2: LLM (Qwen 14B)                                    │
│  ├── Phải TỰ LÀM TẤT CẢ:                                  │
│  │   - Đọc hiểu spec tiếng Việt                            │
│  │   - Lập kế hoạch file nào viết trước                    │
│  │   - Viết code Python                                     │
│  │   - Tự nhớ data format giữa các file                    │
│  │   - Tự biết khi nào xong                                │
│  └── Output: Thought + Action + Code                        │
│           ↓                                                 │
│  TẦNG 3: Markdown Fence Parser (chỉ parse output)           │
│  ├── ✅ Parse Action + File + Code block                    │
│  └── ❌ Không auto-serialize, không auto-validate           │
└─────────────────────────────────────────────────────────────┘
```

**Vấn đề**: LLM 14B phải gánh TẤT CẢ — parsing, planning, coding, integration.
Đây chính là mô hình "Simple" trong bảng benchmark → 10/100.

---

## 2. Tại Sao "Pro Arch" Đạt 100/100?

Nhìn lại bảng benchmark của bạn:

| Kiến trúc | Điểm |
|:---|:---:|
| Qwen 14B (Simple — LLM làm hết) | 10/100 |
| Qwen 14B (Pro Arch — 3 tầng + hyper rules) | **100/100** |

Sự khác biệt 10x KHÔNG phải do model khác — **cùng model Qwen 14B**.
Khác biệt hoàn toàn do KIẾN TRÚC:

### Pro Arch đã làm gì?

1. **Tầng 1 (Python/Rules)**: Phân tích spec trước → biết cần bao nhiêu file, data format, dependencies
2. **Tầng 2 (LLM)**: Chỉ viết code cho 1 file tại 1 thời điểm, với spec CỤ THỂ
3. **Tầng 3 (Python/Validator)**: Tự động validate output, detect placeholder, run test

### Coder Agent V4 hiện tại thiếu gì?

| Thành phần | Pro Arch (100/100) | Coder V4 (40/100) | Gap |
|:---|:---:|:---:|:---|
| Pre-analysis (Tầng 1) | ✅ | ❌ | **Thiếu hoàn toàn** |
| Contract injection | ✅ | ✅ | Đã có |
| Isolated sessions | ✅ | ✅ | Đã có |
| Auto-validate output | ✅ | ⚠️ Sơ sài | Chỉ check string, không check logic |
| Integration test loop | ✅ | ⚠️ Fixer yếu | Fixer ghi đè code tốt |
| LLM cognitive load | Thấp | **Cao** | LLM phải tự parse tiếng Việt + code |

---

## 3. Giải Pháp: Code Planner (Gemma 4 26B MoE) vs Tất-cả-1-model?

### Option A: Qwen 14B làm hết (hiện tại)

```
Qwen 14B = Planner + Coder + Validator
→ Quá tải → 40/100
```

**Vấn đề**:
- 14B phải hiểu tiếng Việt + viết Python + nhớ contract → quá nhiều "vai diễn"
- Latency cao: mỗi file ~15-30s inference
- Error propagation: nếu plan sai → code sai → validate sai

### Option B: Tách Planner (Gemma 4 26B MoE) + Coder (Qwen 14B) ← ĐỀ XUẤT

```
┌──────────────────────────────────────────────────────────────┐
│                    CODER SYSTEM V5                            │
│                                                              │
│  TẦNG 1: CODE PLANNER (Gemma 4 26B MoE / hoặc Python Rules) │
│  ├── Đọc spec tiếng Việt → parse yêu cầu                    │
│  ├── Sinh ra: file list, dependencies, data contract         │
│  ├── Tương tự IntentClassifier của core agent                │
│  └── Output: structured plan (JSON/dict)                     │
│           ↓                                                  │
│  TẦNG 2: CODER (Qwen 14B — CHỈ VIẾT CODE)                   │
│  ├── Nhận: 1 file + contract + mini-spec                     │
│  ├── CHỈ LÀM: viết Python code                              │
│  ├── KHÔNG LÀM: planning, spec parsing, integration          │
│  └── Output: code trong Markdown Fence                       │
│           ↓                                                  │
│  TẦNG 3: CODE VALIDATOR (Python, KHÔNG phải LLM)             │
│  ├── Syntax check (ast.parse)                                │
│  ├── Import check (module resolution)                        │
│  ├── Contract compliance (function signatures)               │
│  ├── Integration test (subprocess + evaluate script)         │
│  └── Output: pass/fail + error feedback                      │
└──────────────────────────────────────────────────────────────┘
```

### So sánh chi tiết:

| Tiêu chí | Option A (14B làm hết) | Option B (Planner + Coder) |
|:---|:---|:---|
| **VRAM** | ~8GB (1 model) | ~12GB (2 model, luân phiên) |
| **Latency** | Cao (mỗi step 15-30s) | Tương đương (nhưng ít step hơn) |
| **Chất lượng Plan** | LLM tự plan → hay sai | Gemma 26B plan → chính xác hơn |
| **Chất lượng Code** | 14B bị quá tải → placeholder | 14B tập trung code → logic tốt hơn |
| **Error recovery** | Fixer phải làm lại từ đầu | Validator chỉ ra lỗi cụ thể |
| **Scalability** | Khó scale — 1 model gánh hết | Dễ scale — thay model từng tầng |

### Tại sao Gemma 4 26B MoE cho Planner?

1. **MoE (Mixture of Experts)** chỉ activate ~8B params tại 1 thời điểm → VRAM ~6-8GB
2. Gemma 4 hiểu tiếng Việt tốt hơn Qwen Coder (vì Gemma là general model)
3. Planner KHÔNG cần viết code — chỉ cần hiểu spec và output structured plan
4. Có thể chạy LUÂN PHIÊN: Planner chạy xong → unload → load Coder → viết code

### Workflow thực tế:

```
1. [Gemma 26B MoE] Đọc benchmark spec (545 dòng)
   → Output: { files: [...], contract: "...", task_prompts: [...] }
   → Unload model (giải phóng VRAM)

2. [Qwen 14B] Viết từng file theo task_prompts
   → Mỗi session: contract + 1 task prompt + code output
   → Load model 1 lần, viết 6 file liên tiếp

3. [Python] Validate + Integration Test
   → ast.parse() syntax check
   → evaluate_coder_v4.py
   → Nếu fail → feedback loop với Qwen 14B
```

---

## 4. Kết Luận & Đề Xuất

### Nên dùng Option B (Planner + Coder) vì:

1. **Đúng pattern đã chứng minh**: Core Agent đạt 96% nhờ tách 3 tầng — áp dụng y hệt cho Coder
2. **Giảm cognitive load**: Qwen 14B chỉ viết code, không phải nghĩ về architecture
3. **Hardware phù hợp**: 2 model luân phiên trên 12GB vẫn ổn (MoE chỉ dùng ~6-8GB active)
4. **Dự đoán điểm**: 75-90/100 (từ 40 hiện tại)

### NHƯNG: có thể dùng Python Rules thay Gemma 26B

Nếu bài test có format cố định (như Senior Benchmark), ta có thể viết Python rules cho Planner (giống IntentClassifier) mà KHÔNG cần thêm model:

```python
# code_planner.py — Pure Python, không cần LLM
class CodePlanner:
    def analyze_spec(self, spec_text: str) -> dict:
        """Parse spec → structured plan."""
        # Regex detect file list từ spec
        # Regex detect API signatures
        # Regex detect dependencies
        # Generate contract từ spec
        return {
            "files": [...],
            "contract": "...",
            "task_prompts": [...]
        }
```

Cách này **KHÔNG tốn VRAM thêm** và latency gần 0ms cho phase planning.

### Roadmap đề xuất:

| Phase | Mô tả | VRAM | Dự đoán điểm |
|:---:|:---|:---:|:---:|
| **Hiện tại** | Coder V4 (14B làm hết + Contract) | 8GB | 40/100 |
| **Phase A** | + Python CodePlanner (Rule-based) | 8GB | 60-70/100 |
| **Phase B** | + Gemma 26B MoE Planner (thay rules) | 12GB | 75-90/100 |
| **Phase C** | + Auto-Fixer với targeted repair | 12GB | 85-95/100 |
