"""
code_planner.py — LLM Code Planner (Kiến trúc Hyper)

Áp dụng CÙNG kiến trúc 3 tầng như agent_core.py (Brain V5 đạt 96%):

  Tầng 1: IntentClassifier (Rule-based) — Pre-analyze spec
  Tầng 1b: LLM Deep Reasoning — Đọc spec, suy luận kiến trúc
  Auto-Serializer: Python tự extract plan từ LLM output (không bắt LLM sinh JSON)

LLM CHỈ suy luận (Thought). Python tự tạo structured plan.
Hỗ trợ 2 model để benchmark:
  - gemma4:e4b (4B, 9.6GB) — nhẹ, nhanh
  - gemma4:26b-moe-iq3xs (26B MoE, 12GB) — deep reasoning
"""

import os
import re
import json
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI
from Core.context_enricher import IntentClassifier
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

load_dotenv()

API_BASE = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
API_KEY = os.getenv("LLM_API_KEY", "ollama")

# Model cho Planner — có thể override từ env
PLANNER_MODEL_4B = "gemma4:e4b"
PLANNER_MODEL_MOE = "gemma4:26b-moe-iq3xs"

_planner_client = AsyncOpenAI(base_url=API_BASE, api_key=API_KEY)

# ============================================================================
# PLANNER SYSTEM PROMPT — Hyper Architecture
# LLM CHỈ suy luận, KHÔNG phải sinh JSON
# ============================================================================
PLANNER_SYSTEM_PROMPT = """You are a Senior Software Architect. You analyze coding specifications and create implementation plans.

## YOUR JOB
Read the specification and reason about:
1. What files need to be created
2. What functions each file should contain (name, params, return type)
3. What data structures (dict keys, list contents) flow between files
4. What edge cases need handling
5. What keyword mappings or lookup tables are needed
6. What test cases must pass

## OUTPUT FORMAT
For each file, use EXACTLY this format:

FILE: filename.py
FUNCTIONS: function_name(param: type) -> return_type
DATA_FLOW: Receives X, Returns Y with keys: {key1: type, key2: type}
EDGE_CASES: list specific edge cases
DEPENDENCIES: from module import name
KEYWORD_MAP: if applicable, list keyword->value mappings
TEST_CASES: if applicable, list input->output few-shot examples

Do NOT skip any file including __init__.py.

At the end, write a section:

INTERFACE CONTRACT:
- List ALL function signatures with exact param and return types
- List ALL data structure schemas with dict keys
- List ALL constants with their exact values
- List test cases that must pass

## RULES
- Be SPECIFIC: list exact dict keys, exact string values, exact list contents
- Include ALL keyword-to-value mappings (e.g., Vietnamese text -> node type)
- Include ALL edge cases from the spec
- Include test cases the code must pass
- ALWAYS include __init__.py as a FILE block
"""


class CodePlanner:
    """
    LLM-powered Code Planner.
    Kiến trúc Hyper (giống agent_core Brain V5):
      1. IntentClassifier pre-analyze spec (rule-based)
      2. LLM deep reasoning (Thought only)
      3. Python Auto-Serializer extract structured plan
    """

    def __init__(self, model: str = None, verbose: bool = True):
        self.model = model or PLANNER_MODEL_4B
        self.verbose = verbose
        self.classifier = IntentClassifier()

    async def plan(self, spec_text: str, output_dir: str) -> dict:
        """
        Đọc spec → LLM reasoning → Auto-Serializer → structured plan.

        Returns:
            {
                "contract": str,          # Interface contract
                "tasks": [                # Task list cho orchestrator
                    {"file": "...", "prompt": "...", "checks": [...]},
                    ...
                ],
                "model_used": str,
                "reasoning": str          # Raw LLM reasoning (for debug)
            }
        """
        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  CODE PLANNER — Deep Reasoning")
            print(f"  Model: {self.model}")
            print(f"{'='*60}")

        # ── Tầng 0: URL Detection & Enrichment ──
        if spec_text.strip().startswith(("http://", "https://")):
            if self.verbose:
                print(f"  [Tầng 0] Phát hiện URL: {spec_text.strip()}")
            url = spec_text.strip()
            fetched_content = await self._fetch_url_content(url)
            if fetched_content:
                spec_text = fetched_content
                if self.verbose:
                    print(f"  [Tầng 0] Đã lấy được nội dung từ URL ({len(spec_text)} chars)")
            else:
                if self.verbose:
                    print(f"  [Tầng 0] CẢNH BÁO: Không thể lấy nội dung từ URL. Sử dụng text gốc.")

        # ── Tầng 1: IntentClassifier pre-analyze ──
        intent = self.classifier.classify(spec_text[:500])  # First 500 chars
        if self.verbose:
            print(f"  [Tầng 1] Intent: {intent.primary_intent.value}")
            print(f"  [Tầng 1] Actions: {intent.detected_actions}")
            print(f"  [Tầng 1] Targets: {intent.detected_targets}")

        # ── Tầng 1b: LLM Deep Reasoning ──
        if self.verbose:
            print(f"\n  [LLM Planner] Đang suy luận...")

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"## SPECIFICATION\n{spec_text}\n\n"
                f"## OUTPUT DIRECTORY\n{output_dir}\n\n"
                f"## PRE-ANALYSIS (from IntentClassifier)\n"
                f"- Actions detected: {intent.detected_actions}\n"
                f"- Targets detected: {intent.detected_targets}\n"
                f"- Is pipeline: {intent.pipeline is not None}\n\n"
                f"Analyze this spec and create an implementation plan.\n"
                f"List ALL files, ALL functions with EXACT signatures, "
                f"ALL data structures, and a complete INTERFACE CONTRACT."
            )}
        ]

        response = await _planner_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2
        )

        reasoning = response.choices[0].message.content or ""

        if self.verbose:
            print(f"  [LLM Planner] Reasoning: {len(reasoning)} chars")
            # Print first 500 chars of reasoning
            for line in reasoning[:800].split('\n'):
                print(f"    │ {line}")
            if len(reasoning) > 800:
                print(f"    │ ... ({len(reasoning) - 800} chars more)")

        # ── Auto-Serializer: Python extract plan từ LLM output ──
        plan = self._auto_serialize(reasoning, output_dir)
        plan["model_used"] = self.model
        plan["reasoning"] = reasoning

        if self.verbose:
            print(f"\n  [Auto-Serializer] Contract: {len(plan['contract'])} chars")
            print(f"  [Auto-Serializer] Tasks: {len(plan['tasks'])}")
            for t in plan["tasks"]:
                print(f"    - {t['file']} ({len(t['checks'])} checks)")

        return plan

    async def _fetch_url_content(self, url: str) -> str:
        """
        Sử dụng Playwright để lấy nội dung từ URL.
        Hỗ trợ đăng nhập thông qua Persistent Context nếu có CHROME_PROFILE_PATH.
        """
        if not PLAYWRIGHT_AVAILABLE:
            if self.verbose:
                print("  [Error] Playwright chưa được cài đặt. Không thể fetch URL.")
            return None

        # Lấy đường dẫn profile từ .env để dùng session đã đăng nhập
        profile_path = os.getenv("CHROME_PROFILE_PATH") 
        
        try:
            async with async_playwright() as p:
                if profile_path and os.path.exists(profile_path):
                    if self.verbose:
                        print(f"  [WebReader] Sử dụng Profile: {profile_path}")
                    # Mở trình duyệt với session cũ (đã đăng nhập)
                    browser_context = await p.chromium.launch_persistent_context(
                        user_data_dir=profile_path,
                        headless=False, # ChatGPT thường block headless nếu có login
                        args=["--disable-blink-features=AutomationControlled"] 
                    )
                    page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
                else:
                    if self.verbose:
                        print("  [WebReader] Chế độ Guest (Không đăng nhập)")
                    browser = await p.chromium.launch(headless=True)
                    browser_context = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
                    )
                    page = await browser_context.new_page()
                
                if self.verbose:
                    print(f"  [WebReader] Đang kết nối tới {url}...")
                
                await page.goto(url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(5) # Đợi ChatGPT render nội dung
                
                content = await page.evaluate("""() => {
                    // Ưu tiên lấy nội dung chính của chat
                    const selectors = ['article', '.markdown', 'main', 'body'];
                    for (const s of selectors) {
                        const el = document.querySelector(s);
                        if (el && el.innerText.length > 200) return el.innerText;
                    }
                    return document.body.innerText;
                }""")
                
                await browser_context.close()
                return content
        except Exception as e:
            if self.verbose:
                print(f"  [Error] Lỗi khi fetch URL: {str(e)}")
            return None

    async def ask_chatgpt(self, question: str) -> str:
        """
        Agent tự động đặt câu hỏi lên ChatGPT và lấy câu trả lời.
        """
        if not PLAYWRIGHT_AVAILABLE:
            return "Error: Playwright not installed."

        profile_path = os.getenv("CHROME_PROFILE_PATH")
        if not profile_path:
            return "Error: CHROME_PROFILE_PATH not set in .env"

        try:
            async with async_playwright() as p:
                # Mở trình duyệt có login
                browser_context = await p.chromium.launch_persistent_context(
                    user_data_dir=profile_path,
                    headless=False, # Để bạn có thể quan sát Agent đang làm gì
                    args=["--disable-blink-features=AutomationControlled"]
                )
                page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
                
                if self.verbose:
                    print(f"  [Agent] Đang đi hỏi ChatGPT: {question[:50]}...")
                
                await page.goto("https://chatgpt.com", wait_until="networkidle")
                
                # Thử nhiều selector khác nhau cho ô nhập liệu
                prompt_selectors = [
                    'textarea#prompt-textarea',
                    'div[contenteditable="true"]',
                    '[data-testid="chat-input"]',
                    'textarea'
                ]
                
                target_selector = None
                for selector in prompt_selectors:
                    try:
                        await page.wait_for_selector(selector, timeout=10000)
                        target_selector = selector
                        break
                    except:
                        continue
                
                if not target_selector:
                    # Nếu không tìm thấy, chụp ảnh màn hình để debug
                    await page.screenshot(path="scratch/chatgpt_error.png")
                    return "Error: Could not find ChatGPT input area. Screenshot saved to scratch/chatgpt_error.png"
                
                # Gõ câu hỏi
                if "div" in target_selector:
                    await page.click(target_selector)
                    await page.keyboard.type(question)
                else:
                    await page.fill(target_selector, question)
                
                # Nhấn Enter để gửi
                await page.keyboard.press("Enter")
                
                if self.verbose:
                    print(f"  [Agent] Đã gửi câu hỏi, đang đợi câu trả lời...")

                # Đợi cho đến khi nút "Stop generating" biến mất hoặc nút "Send" hiện lại
                # Đây là dấu hiệu ChatGPT đã trả lời xong
                await asyncio.sleep(10) # Đợi tối thiểu 10s để bắt đầu sinh
                
                # Theo dõi sự thay đổi của nội dung để biết khi nào dừng
                last_content = ""
                print(f"  [Agent] Đang chờ ChatGPT soạn thảo bài giải...")
                
                # Đợi cho đến khi bắt đầu có nội dung markdown xuất hiện
                for _ in range(30):
                    await asyncio.sleep(2)
                    current_content = await page.evaluate("""() => {
                        const markdowns = document.querySelectorAll('.markdown');
                        return markdowns.length > 0 ? markdowns[markdowns.length - 1].innerText : "";
                    }""")
                    if len(current_content) > 50:
                        break
                
                # Đợi cho đến khi nội dung dừng thay đổi (đã viết xong)
                for _ in range(60): # Timeout tối đa 120s
                    await asyncio.sleep(3)
                    current_content = await page.evaluate("""() => {
                        const markdowns = document.querySelectorAll('.markdown');
                        return markdowns.length > 0 ? markdowns[markdowns.length - 1].innerText : "";
                    }""")
                    
                    if current_content == last_content and len(current_content) > 100:
                        # Nội dung không đổi nữa nghĩa là đã xong
                        break
                    last_content = current_content
                    if self.verbose:
                        print(f"    │ Đang nhận dữ liệu: {len(current_content)} chars...")

                if self.verbose:
                    print(f"  [Agent] Đã lấy được câu trả lời ({len(last_content)} chars)")

                await browser_context.close()
                return last_content
        except Exception as e:
            return f"Error: {str(e)}"

    def _auto_serialize(self, reasoning: str, output_dir: str) -> dict:
        """
        Auto-Serializer: Extract structured plan từ LLM reasoning text.
        Giống cách agent_core extract TurnRoutePlan từ LLM output.
        LLM KHÔNG phải sinh JSON — Python tự đọc text và serialize.
        """
        contract = self._extract_contract(reasoning)
        tasks = self._extract_tasks(reasoning, output_dir, contract)

        return {
            "contract": contract,
            "tasks": tasks
        }

    def _extract_contract(self, reasoning: str) -> str:
        """Extract INTERFACE CONTRACT từ LLM reasoning."""
        # Tìm section INTERFACE CONTRACT hoặc CONTRACT trong reasoning
        contract_patterns = [
            r'(?:INTERFACE\s+)?CONTRACT[:\s]*\n(.*?)(?=\n##[^#]|\Z)',
            r'DATA\s+(?:FLOW|STRUCTURES?)[:\s]*\n(.*?)(?=\n##[^#]|\Z)',
        ]

        for pattern in contract_patterns:
            m = re.search(pattern, reasoning, re.DOTALL | re.IGNORECASE)
            if m and len(m.group(1).strip()) > 50:
                return m.group(1).strip()

        # Fallback: extract tất cả FILE blocks and build rich contract
        blocks = []
        file_pattern = r'(?:#{1,3}\s*)?\*{0,2}FILE:?\*{0,2}\s*(\S+\.py)\s*\n(.*?)(?=(?:#{1,3}\s*)?\*{0,2}FILE:?\*{0,2}\s*\S+\.py|\Z)'
        for m in re.finditer(file_pattern, reasoning, re.DOTALL | re.IGNORECASE):
            fname = m.group(1)
            block_text = m.group(2)
            
            # Extract multi-line sections
            functions = re.findall(r'\*{0,2}FUNCTIONS?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:DATA_FLOW|EDGE_CASES|DEPENDENCIES|KEYWORD_MAP):).)*)', block_text, re.DOTALL | re.IGNORECASE)
            data_flow = re.findall(r'\*{0,2}DATA_FLOW:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:EDGE_CASES|DEPENDENCIES|KEYWORD_MAP):).)*)', block_text, re.DOTALL | re.IGNORECASE)
            edge_cases = re.findall(r'\*{0,2}EDGE_CASES?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:DEPENDENCIES|KEYWORD_MAP):).)*)', block_text, re.DOTALL | re.IGNORECASE)
            deps = re.findall(r'\*{0,2}DEPENDENCIES?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:KEYWORD_MAP|FILE):).)*)', block_text, re.DOTALL | re.IGNORECASE)
            keywords = re.findall(r'\*{0,2}KEYWORD_MAP:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:FILE|DEPENDENCIES|EDGE_CASES):).)*)', block_text, re.DOTALL | re.IGNORECASE)
            
            block = f"### {fname}\n"
            for f in functions:
                cleaned = f.strip()
                if cleaned:
                    block += f"FUNCTIONS: {cleaned}\n"
            for d in data_flow:
                cleaned = d.strip()
                if cleaned:
                    block += f"DATA_FLOW: {cleaned}\n"
            for e in edge_cases:
                cleaned = e.strip()
                if cleaned:
                    block += f"EDGE_CASES: {cleaned}\n"
            for dep in deps:
                cleaned = dep.strip()
                if cleaned:
                    block += f"DEPENDENCIES: {cleaned}\n"
            for kw in keywords:
                cleaned = kw.strip()
                if cleaned:
                    block += f"KEYWORD_MAP: {cleaned}\n"
            
            if len(block) > len(f"### {fname}\n") + 5:
                blocks.append(block)

        if blocks:
            return "\n".join(blocks)

        # Last resort: return relevant portion of reasoning
        return reasoning[:2000] if len(reasoning) > 100 else ""

    def _extract_tasks(self, reasoning: str, output_dir: str, contract: str) -> list:
        """Extract task list từ LLM reasoning."""
        tasks = []

        # Tìm FILE blocks (hỗ trợ Markdown: ### FILE:, **FILE:**, FILE:)
        file_pattern = r'(?:#{1,3}\s*)?\*{0,2}FILE:?\*{0,2}\s*(\S+\.py)\s*\n(.*?)(?=(?:#{1,3}\s*)?\*{0,2}FILE:?\*{0,2}\s*\S+\.py|\Z)'
        file_blocks = re.finditer(file_pattern, reasoning, re.DOTALL | re.IGNORECASE)

        found_files = []
        for m in file_blocks:
            fname = os.path.basename(m.group(1))
            block = m.group(2)
            found_files.append(fname)

            # Extract (hỗ trợ multi-line)
            functions = re.findall(r'\*{0,2}FUNCTIONS?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:DATA_FLOW|EDGE_CASES|DEPENDENCIES|KEYWORD_MAP|TEST_CASES):).)*)', block, re.DOTALL | re.IGNORECASE)
            data_flow = re.findall(r'\*{0,2}DATA_FLOW:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:EDGE_CASES|DEPENDENCIES|KEYWORD_MAP|TEST_CASES):).)*)', block, re.DOTALL | re.IGNORECASE)
            edge_cases = re.findall(r'\*{0,2}EDGE_CASES?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:DEPENDENCIES|KEYWORD_MAP|TEST_CASES):).)*)', block, re.DOTALL | re.IGNORECASE)
            deps = re.findall(r'\*{0,2}DEPENDENCIES?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:KEYWORD_MAP|TEST_CASES|FILE):).)*)', block, re.DOTALL | re.IGNORECASE)
            keywords = re.findall(r'\*{0,2}KEYWORD_MAP:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:FILE|DEPENDENCIES|EDGE_CASES|TEST_CASES):).)*)', block, re.DOTALL | re.IGNORECASE)
            test_cases = re.findall(r'\*{0,2}TEST_CASES?:?\*{0,2}[\s\n]*((?:(?!\*{0,2}(?:FILE|DEPENDENCIES|EDGE_CASES|KEYWORD_MAP):).)*)', block, re.DOTALL | re.IGNORECASE)

            # Build enriched task prompt (với data_flow context)
            prompt = f"Tạo file {output_dir}/{fname}\n"
            
            def append_section(title, items):
                nonlocal prompt
                if items:
                    prompt += f"\n{title}:\n"
                    for item in items:
                        cleaned = item.strip()
                        if cleaned:
                            prompt += f"{cleaned}\n"

            append_section("IMPORT", deps)
            append_section("FUNCTIONS", functions)
            append_section("DATA FLOW", data_flow)
            append_section("EDGE CASES", edge_cases)
            append_section("KEYWORD MAP", keywords)
            append_section("TEST CASES (FEW-SHOT)", test_cases)


            # Build checks từ function signatures
            checks = []
            for f_block in functions:
                for line in f_block.split('\n'):
                    line_clean = line.strip()
                    if not line_clean: continue
                    
                    # Tránh các dòng mô tả logic gọi hàm hoặc header mô tả (Calls, Uses, Depends, Input, Output, etc.)
                    headers = ["calls ", "uses ", "depends ", "input", "output", "signature", "schema", "constant", "case", "few-shot"]
                    if any(x in line_clean.lower() for x in headers):
                        continue

                    # Handle: "- parse_natural_language(text: str) -> dict" 
                    # Also handle: "- `VALID_NODE_TYPES: list[str]`"
                    fn_match = re.search(r'[`\s\-\*]*([a-zA-Z_]\w*)\s*[\(:]', line_clean)
                    if fn_match:
                        fn_name = fn_match.group(1)
                        if fn_name[0].isupper():
                            if fn_name not in checks:
                                checks.append(fn_name)  # Constants like VALID_NODE_TYPES
                        elif fn_name not in ["def", "class", "return", "import", "from"]:
                            check_val = f"def {fn_name}" if fname != "__init__.py" else fn_name
                            if check_val not in checks:
                                checks.append(check_val)

            tasks.append({
                "file": fname,
                "prompt": prompt,
                "checks": checks if checks else [fname.replace('.py', '')]
            })

        # Fallback: standard file list nếu LLM không dùng FILE: format
        if not found_files:
            tasks = self._fallback_standard_tasks(output_dir)

        return tasks

    def _fallback_standard_tasks(self, output_dir: str) -> list:
        """Fallback nếu LLM reasoning không parse được → dùng standard tasks."""
        return [
            {"file": "schema.py", "prompt": f"Tạo file {output_dir}/schema.py với VALID_NODE_TYPES và DANGEROUS_ACTIONS.",
             "checks": ["VALID_NODE_TYPES", "DANGEROUS_ACTIONS"]},
            {"file": "parser.py", "prompt": f"Tạo file {output_dir}/parser.py với def parse_natural_language(text).",
             "checks": ["def parse_natural_language"]},
            {"file": "planner.py", "prompt": f"Tạo file {output_dir}/planner.py với def get_execution_order(nodes).",
             "checks": ["def get_execution_order"]},
            {"file": "validator.py", "prompt": f"Tạo file {output_dir}/validator.py với def validate_workflow(workflow).",
             "checks": ["def validate_workflow"]},
            {"file": "compiler.py", "prompt": f"Tạo file {output_dir}/compiler.py với def compile_workflow(user_input).",
             "checks": ["def compile_workflow"]},
            {"file": "__init__.py", "prompt": f"Tạo file {output_dir}/__init__.py export compile_workflow.",
             "checks": ["compile_workflow"]},
        ]


async def benchmark_planners(spec_text: str, output_dir: str):
    """
    Benchmark 2 model planner: Gemma 4B vs Gemma 26B MoE.
    So sánh chất lượng plan và latency.
    """
    import time
    results = {}

    for model_name, model_id in [
        ("Gemma 4B", PLANNER_MODEL_4B),
        ("Gemma 26B MoE", PLANNER_MODEL_MOE),
    ]:
        print(f"\n{'='*60}")
        print(f"  BENCHMARK: {model_name}")
        print(f"{'='*60}")

        planner = CodePlanner(model=model_id, verbose=True)

        start = time.time()
        plan = await planner.plan(spec_text, output_dir)
        elapsed = time.time() - start

        results[model_name] = {
            "model": model_id,
            "latency": f"{elapsed:.1f}s",
            "contract_length": len(plan["contract"]),
            "num_tasks": len(plan["tasks"]),
            "tasks": [t["file"] for t in plan["tasks"]],
            "checks_per_task": [len(t["checks"]) for t in plan["tasks"]],
            "reasoning_length": len(plan["reasoning"]),
        }

        print(f"\n  Latency: {elapsed:.1f}s")
        print(f"  Contract: {len(plan['contract'])} chars")
        print(f"  Tasks: {len(plan['tasks'])}")
        print(f"  Reasoning: {len(plan['reasoning'])} chars")

    # Report
    print(f"\n{'='*60}")
    print(f"  PLANNER BENCHMARK RESULTS")
    print(f"{'='*60}")
    print(f"  {'Metric':<25} {'Gemma 4B':<20} {'Gemma 26B MoE':<20}")
    print(f"  {'─'*65}")
    for metric in ["latency", "contract_length", "num_tasks", "reasoning_length"]:
        v1 = results.get("Gemma 4B", {}).get(metric, "N/A")
        v2 = results.get("Gemma 26B MoE", {}).get(metric, "N/A")
        print(f"  {metric:<25} {str(v1):<20} {str(v2):<20}")

    return results
