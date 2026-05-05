"""
orchestrator.py — Task Decomposition Orchestrator V5
Kiến trúc Hyper 3 tầng (giống agent_core Brain V5):

  Tầng 1a: IntentClassifier (Rule-based) — Pre-analyze task
  Tầng 1b: CodePlanner (LLM Gemma/MoE) — Deep reasoning → plan
    → LLM chỉ Thought, Python Auto-Serializer tạo structured plan
    → Giống Auto-Serializer trong agent_core đạt 96%

  Tầng 2: CoderAgent (LLM Qwen 14B) — CHỈ viết code
    → Nhận enriched prompt + contract từ Planner
    → Không phải lo: planning, spec parsing, integration

  Tầng 3: CodeValidator (Python) — Auto-validate
    → ast.parse() syntax check
    → String-based check cho required patterns
    → Integration test via evaluate script
"""

import os
import ast
import subprocess
import asyncio
from Core.coder_agent import CoderAgent
from Core.context_enricher import IntentClassifier, PromptEnricher
from Core.code_planner import CodePlanner


class CodeAnalyzer:
    """
    Tầng 1 cho Coder — Tận dụng IntentClassifier từ context_enricher.py
    để pre-analyze task prompt TRƯỚC KHI gửi cho LLM.
    
    Giống cách agent_core dùng ContextEnricher cho routing:
    - agent_core: user_input → IntentClassifier → enriched_prompt → LLM
    - orchestrator: task_prompt → CodeAnalyzer → enriched_task → LLM
    """
    
    def __init__(self):
        self.classifier = IntentClassifier()
    
    def analyze_task(self, task_prompt: str, filename: str) -> dict:
        """
        Pre-analyze coding task bằng IntentClassifier rules.
        Output: metadata hữu ích cho LLM khi viết code.
        """
        intent = self.classifier.classify(task_prompt)
        
        return {
            "detected_actions": intent.detected_actions,
            "detected_targets": intent.detected_targets,
            "is_pipeline": intent.pipeline is not None,
            "pipeline_steps": intent.pipeline.total_steps if intent.pipeline else 0,
            "is_ambiguous": intent.is_ambiguous,
            "ambiguity_reasons": intent.ambiguity_reasons,
            "capability_group": intent.capability_group,
            "urgency": intent.urgency,
        }
    
    def enrich_task_prompt(self, task_prompt: str, filename: str, contract: str) -> str:
        """
        Biến raw task prompt thành enriched prompt:
        1. Pre-analyze → tìm keywords, actions, targets
        2. Inject contract
        3. Thêm hướng dẫn cụ thể dựa trên loại file
        """
        analysis = self.analyze_task(task_prompt, filename)
        
        parts = []
        
        # Contract (ưu tiên #1)
        if contract:
            parts.append(f"## INTERFACE CONTRACT (BẮT BUỘC TUÂN THỦ)\n{contract}")
        
        # Pre-analysis metadata (Tầng 1 output)
        if analysis["detected_actions"] or analysis["detected_targets"]:
            meta = "## PRE-ANALYSIS (Hệ thống đã phân tích trước)\n"
            if analysis["detected_actions"]:
                meta += f"- Actions phát hiện: {', '.join(analysis['detected_actions'])}\n"
            if analysis["detected_targets"]:
                meta += f"- Targets phát hiện: {', '.join(analysis['detected_targets'])}\n"
            if analysis["is_pipeline"]:
                meta += f"- Pipeline: {analysis['pipeline_steps']} steps (sequential)\n"
            parts.append(meta)
        
        # File-specific guidance
        if "parser" in filename:
            parts.append(
                "## HƯỚNG DẪN CHO PARSER\n"
                "File này CẦN xử lý tiếng Việt. Logic BẮT BUỘC:\n"
                "- Dùng dict keyword_map để map từ khóa → node type\n"
                "  VD: 'đọc email'→'email.read', 'xoá email'/'xoá'→'email.delete', 'tóm tắt'→'text.summarize'\n"
                "- Split câu theo 'rồi', 'sau đó', dấu phẩy → mỗi phần = 1 node\n"
                "- depends_on phải là LIST of strings: node[i]['depends_on'] = [node[i-1]['id']]\n"
                "- Check DANGEROUS_ACTIONS (from schema) cho requires_confirmation\n"
                "- Check đại từ mơ hồ (nó, đó, cái) HOẶC câu < 5 từ → ambiguity = True\n"
                "- Check 'báo tôi'→'notify_only' cho failure_policy\n"
                "- QUAN TRỌNG: 'xoá nó đi' phải return ambiguity=True VÀ requires_confirmation=True\n"
            )
        elif "planner" in filename:
            parts.append(
                "## HƯỚNG DẪN CHO PLANNER\n"
                "File này CẦN thuật toán graph. Dùng Kahn's Algorithm:\n"
                "- Build in-degree map từ depends_on\n"
                "- BFS queue: bắt đầu từ nodes có in-degree = 0\n"
                "- Nếu cuối cùng còn node chưa visit → raise ValueError('Cycle detected')\n"
            )
        elif "validator" in filename:
            parts.append(
                "## HƯỚNG DẪN CHO VALIDATOR\n"
                "File này validate data THEO CONTRACT:\n"
                "- workflow['nodes'] là LIST (không phải dict)\n"
                "- Import VALID_NODE_TYPES từ schema.py\n"
                "- Import get_execution_order từ planner.py\n"
            )
        
        # Task prompt gốc
        parts.append(f"## TASK\n{task_prompt}")
        
        return "\n\n".join(parts)


class TaskOrchestrator:
    def __init__(self, output_dir: str, contract: str = "", 
                 planner_model: str = None, verbose: bool = True):
        self.output_dir = output_dir
        self.contract = contract
        self.verbose = verbose
        self.results = {}
        # Tầng 1a: IntentClassifier (rule-based)
        self.analyzer = CodeAnalyzer()
        # Tầng 1b: LLM Planner (nếu có model)
        self.planner = CodePlanner(model=planner_model, verbose=verbose) if planner_model else None

    async def plan_from_spec(self, spec_text: str) -> list[dict]:
        """
        Phase 0: LLM Planner đọc spec → sinh contract + tasks.
        Áp dụng kiến trúc Hyper: LLM Thought → Python Auto-Serializer.
        """
        if not self.planner:
            raise ValueError("Planner model chưa được cấu hình. Truyền planner_model vào __init__.")
        
        plan = await self.planner.plan(spec_text, self.output_dir)
        
        # Cập nhật contract từ planner output
        if plan["contract"]:
            self.contract = plan["contract"]
        
        return plan["tasks"]

    async def run(self, tasks: list[dict], max_retries: int = 2,
                  eval_script: str = None, eval_max_retries: int = 2):
        """3-phase pipeline: Analyze → Code → Test"""
        os.makedirs(self.output_dir, exist_ok=True)

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  ORCHESTRATOR V5 — HYPER 3-LAYER ARCHITECTURE")
            print(f"  Tầng 1a: IntentClassifier (rule-based)")
            print(f"  Tầng 1b: CodePlanner ({self.planner.model if self.planner else 'N/A'})")
            print(f"  Tầng 2:  CoderAgent (Qwen 14B)")
            print(f"  Tầng 3:  CodeValidator (ast.parse + eval)")
            print(f"  Tasks: {len(tasks)}")
            print(f"{'='*60}")

        # ── Phase 2: Isolated Coding (với enriched prompts từ Tầng 1) ──
        for i, task in enumerate(tasks):
            fname = task["file"]
            checks = task.get("checks", [])

            if self.verbose:
                print(f"\n{'─'*60}")
                print(f"  Task {i+1}/{len(tasks)}: {fname}")
                print(f"{'─'*60}")

            # Tầng 1: Pre-analyze task
            analysis = self.analyzer.analyze_task(task["prompt"], fname)
            if self.verbose and (analysis["detected_actions"] or analysis["detected_targets"]):
                print(f"    [Tầng 1] Actions: {analysis['detected_actions']}")
                print(f"    [Tầng 1] Targets: {analysis['detected_targets']}")

            success = False
            for attempt in range(max_retries + 1):
                if attempt > 0 and self.verbose:
                    print(f"\n  ⟳ Retry {attempt}/{max_retries}")

                agent = CoderAgent(verbose=self.verbose, allow_commands=False)

                # Tầng 1: Enrich prompt (contract + pre-analysis + guidance)
                enriched = self.analyzer.enrich_task_prompt(
                    task["prompt"], fname, self.contract
                )
                
                # Retry feedback
                if attempt > 0:
                    filepath = os.path.join(self.output_dir, fname)
                    extra = self._retry_feedback(filepath, checks)
                    if extra:
                        enriched += f"\n\n## FEEDBACK TỪ LẦN TRƯỚC\n{extra}"

                await agent.chat(enriched)

                # Post-process: Tự động sửa lỗi relative import (LLM hay mắc lỗi này)
                self._sanitize_file(fname)

                # Tầng 3: Validate output
                success = self._validate(fname, checks)
                if success:
                    break

            icon = "✅" if success else "❌"
            self.results[fname] = {"pass": success, "attempts": attempt + 1}
            if self.verbose:
                print(f"  {icon} {fname} (attempts: {attempt + 1})")

        # ── Phase 3: Integration Test Loop ──
        if eval_script:
            await self._integration_test_loop(eval_script, eval_max_retries)

        return self.results

    def _sanitize_file(self, filename: str):
        """Hậu xử lý code: Tự động sửa các lỗi phổ biến của LLM."""
        filepath = os.path.join(self.output_dir, filename)
        if not os.path.exists(filepath):
            return

        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()

        # 1. Sửa relative import: from .schema -> from schema
        import re
        new_code = re.sub(r'from \.([a-zA-Z_]\w*) import', r'from \1 import', code)
        
        if new_code != code:
            if self.verbose:
                print(f"    [Sanitizer] Đã sửa relative import trong {filename}")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_code)

    # ================================================================
    # Phase 3: Integration Test Loop
    # ================================================================
    async def _integration_test_loop(self, eval_script: str, max_retries: int):
        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  PHASE 3: INTEGRATION TEST (Tầng 3)")
            print(f"{'='*60}")

        for attempt in range(max_retries + 1):
            if self.verbose:
                print(f"\n  Integration test attempt {attempt + 1}/{max_retries + 1}...")

            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["AGENT_SOLUTION_DIR"] = os.path.abspath(self.output_dir)
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(eval_script)))
                result = subprocess.run(
                    ["python", os.path.abspath(eval_script)],
                    capture_output=True, timeout=60,
                    cwd=project_root,
                    env=env
                )
                output = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
            except Exception as e:
                output = f"Error running eval: {e}"

            if self.verbose:
                print(output[:1500])

            all_pass = "ĐIỂM TỔNG CỘNG" in output and "❌" not in output

            if all_pass:
                if self.verbose:
                    print(f"\n  ✅ Integration test PASSED!")
                self.results["_integration"] = {"pass": True, "attempts": attempt + 1}
                return

            if attempt >= max_retries:
                if self.verbose:
                    print(f"\n  ❌ Integration test FAILED (hết retry)")
                self.results["_integration"] = {"pass": False, "attempts": attempt + 1}
                return

            # Fixer: phân tích lỗi → sửa file đúng
            failed_cases = [line.strip() for line in output.split('\n') if '❌' in line]
            failed_summary = '\n'.join(failed_cases[:10])
            all_code = self._read_all_files()

            # Phân tích lỗi import → xác định file cần sửa
            files_to_fix = set()
            if "attempted relative import" in output:
                # Nếu có lỗi relative import, yêu cầu sửa toàn bộ file để đảm bảo dùng absolute import
                files_to_fix.update(["parser.py", "planner.py", "validator.py", "compiler.py"])
            
            if "cannot import" in output and "schema" in output:
                files_to_fix.add("schema.py")
            if "cannot import" in output and "parser" in output:
                files_to_fix.add("parser.py")
            if "cannot import" in output and "planner" in output:
                files_to_fix.add("planner.py")
            
            if "Dangerous" in failed_summary or "Ambiguity" in failed_summary or "Failure Policy" in failed_summary:
                files_to_fix.add("parser.py")
            if "Cycle" in failed_summary:
                files_to_fix.add("planner.py")
            
            if not files_to_fix:
                files_to_fix = {"schema.py", "parser.py", "compiler.py"}  # default expanded
            
            fix_targets = ", ".join([os.path.join(self.output_dir, f) for f in files_to_fix])
            if self.verbose:
                print(f"\n  🔧 Fixer Agent (sửa: {fix_targets})...")

            fixer = CoderAgent(verbose=self.verbose, allow_commands=False)
            fix_prompt = (
                f"## INTERFACE CONTRACT (BẮT BUỘC)\n{self.contract}\n\n"
                f"## CÁC LỖI PHÁT HIỆN\n{failed_summary}\n\n"
                f"## CODE HIỆN TẠI\n{all_code}\n\n"
                f"## YÊU CẦU SỬA LỖI\n"
                f"1. Sửa các file: {fix_targets}\n"
                f"2. TUYỆT ĐỐI KHÔNG dùng relative import (vd: from .schema import ...). "
                f"PHẢI dùng absolute import (vd: from schema import ...).\n"
                f"3. Dùng Action: create_file để ghi đè file cần sửa (DÙNG ĐÚNG ĐƯỜNG DẪN TRÊN)\n"
                f"4. KHÔNG dùng run_command\n"
                f"5. Viết lại TOÀN BỘ file, viết đầy đủ logic\n\n"
            )
            
            if "schema.py" in files_to_fix:
                fix_prompt += (
                    f"## schema.py PHẢI CÓ:\n"
                    f"VALID_NODE_TYPES = ['email.read','email.search','email.draft','email.send','email.delete',"
                    f"'text.summarize','text.extract_action_items','calendar.create_event','calendar.delete_event',"
                    f"'file.save_attachment','file.delete','search.web','system.restart','notify.user']\n"
                    f"DANGEROUS_ACTIONS = ['email.send','email.delete','file.delete','system.restart','calendar.delete_event']\n\n"
                )
            
            if "parser.py" in files_to_fix:
                fix_prompt += (
                    f"## parser.py KEYWORD MAP BẮT BUỘC:\n"
                    f"'đọc email'→email.read, 'xoá email'→email.delete, 'gửi email'→email.send\n"
                    f"'tóm tắt'→text.summarize, 'tạo lịch'/'lịch họp'→calendar.create_event\n"
                    f"Ambiguity: câu < 5 từ + 'nó'/'đó'/'cái đó' → True\n"
                    f"Confirmation: type ∈ DANGEROUS_ACTIONS → True\n"
                    f"Failure: 'báo tôi'→notify_only\n\n"
                )
            
            await fixer.chat(fix_prompt)
            
            # Sanitizer cho các file vừa sửa
            for f_to_fix in files_to_fix:
                self._sanitize_file(f_to_fix)

    def _read_all_files(self) -> str:
        parts = []
        if not os.path.exists(self.output_dir):
            return "(no files)"
        for fname in sorted(os.listdir(self.output_dir)):
            if fname.endswith(".py"):
                fpath = os.path.join(self.output_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        code = f.read()
                    parts.append(f"### {fname}\n```python\n{code}\n```")
                except Exception:
                    parts.append(f"### {fname}\n(read error)")
        return "\n\n".join(parts) if parts else "(no files)"

    # ================================================================
    # Tầng 3: CodeValidator
    # ================================================================
    def _retry_feedback(self, filepath: str, checks: list) -> str:
        if not os.path.exists(filepath):
            return "File CHƯA tồn tại. PHẢI dùng Action: create_file."
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        missing = [c for c in checks if c not in content]
        if missing:
            return (
                "File hiện tại thiếu:\n" +
                "\n".join(f"- {m}" for m in missing) +
                "\n\nViết lại file với ĐẦY ĐỦ logic."
            )
        return ""

    def _validate(self, filename: str, checks: list[str]) -> bool:
        filepath = os.path.join(self.output_dir, filename)
        if not os.path.exists(filepath):
            if self.verbose: print(f"    ✗ File chưa tồn tại")
            return False

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if len(content.strip()) < 30:
            if self.verbose: print(f"    ✗ File quá ngắn ({len(content.strip())} chars)")
            return False

        for flag in ["# Placeholder", "# placeholder", "# TODO"]:
            if flag in content:
                if self.verbose: print(f"    ✗ Chứa '{flag}'")
                return False

        # Tầng 3: ast.parse — giống Auto-Serializer validate JSON
        if filename.endswith('.py'):
            try:
                ast.parse(content)
            except SyntaxError as e:
                if self.verbose: print(f"    ✗ Syntax error: {e}")
                return False

        missing = [c for c in checks if c not in content]
        if missing:
            for m in missing:
                if self.verbose: print(f"    ✗ Thiếu: {m}")
            return False

        if self.verbose: print(f"    ✓ OK")
        return True

    def report(self) -> tuple[int, int]:
        print(f"\n{'='*60}")
        print(f"  REPORT")
        print(f"{'='*60}")
        passed = 0
        for fname, r in self.results.items():
            icon = "✅" if r["pass"] else "❌"
            print(f"  {icon} {fname} ({r['attempts']} attempts)")
            if r["pass"]: passed += 1
        total = len(self.results)
        print(f"\n  Result: {passed}/{total}")
        print(f"{'='*60}")
        return passed, total
