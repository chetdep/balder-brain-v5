"""
coder_agent.py — Balder Coder Agent V4
Kế thừa trực tiếp từ agent_core.py (ReActAgent), chỉ thay đổi:
  1. System Prompt: Markdown Fence format (không JSON cho code)
  2. _parse_markdown_fence: hỗ trợ File: + ``` block
  3. Model: qwen2.5-coder:14b

Tối ưu hardware V4:
  - Reuse AsyncOpenAI client (không tạo mới mỗi step)
  - num_ctx=8192 giữ VRAM ổn định cho 14B trên 12GB GPU
"""

import os
import re
import json
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI
from Core.agent_core import (
    ReActAgent,
    parse_action as core_parse_action,
    sanitize_text,
    MAX_OBSERVATION_CHARS,
)
from Core.tools import AVAILABLE_TOOLS
from Core.telemetry import BalderTelemetry

load_dotenv()

# Coder Agent dùng model riêng
CODER_MODEL = os.getenv("LLM_MODEL_CODER", "qwen2.5-coder:14b")

# ============================================================================
# HARDWARE: Tạo client 1 lần duy nhất — reuse cho mọi step
# Tránh tạo TCP connection mới mỗi run_step() (lãng phí ~50ms + RAM)
# ============================================================================
_coder_client = AsyncOpenAI(
    base_url=os.getenv("LLM_API_BASE", "http://localhost:11434/v1"),
    api_key=os.getenv("LLM_API_KEY", "ollama")
)

# ============================================================================
# SYSTEM PROMPT — Markdown Fence format
# ============================================================================
CODER_SYSTEM_PROMPT = """You are Balder Coder, an autonomous software engineer.

## RESPONSE FORMAT

Thought: <your reasoning>
Action: <tool_name>
<tool-specific arguments>

Then STOP and wait for Observation. ONE action per response.

## TOOLS

### create_file — Create or overwrite a file
Action: create_file
File: path/to/file.py
```
your code here
no JSON escaping needed
```

### read_file — Read file contents
Action: read_file
File: path/to/file.py

### finish_task — Use this tool when you have completed all tasks
Action: finish_task

### run_command — Execute shell command
Action: run_command
Command: python -c "print('hello')"

### list_directory — List directory contents
Action: list_directory
Path: ./src

## RULES
1. ONE action per response. Wait for Observation.
2. NEVER write placeholder code (# TODO, pass, return []). Write REAL logic.
3. NEVER use relative imports (e.g. `from .module import ...`). ALWAYS use absolute imports (e.g. `from module import ...`).
4. Do NOT say TASK_COMPLETE until all files have real working code.
5. When done, use the finish_task tool.
6. ALWAYS follow the INTERFACE CONTRACT if one is provided.

## SAFETY
- WINDOWS ONLY. No Linux commands.
- Use PowerShell syntax for run_command.
"""


class CoderAgent(ReActAgent):
    """
    Kế thừa ReActAgent từ agent_core.py.
    Override: system prompt, model, action parser.
    V4: reuse client, hardware-aware context limit.
    """

    def __init__(self, verbose: bool = True, allow_commands: bool = False):
        super().__init__(use_enricher=False, verbose=verbose)
        self.allow_commands = allow_commands
        
        # Lọc tools tuỳ theo allow_commands
        self.tools = {k: v for k, v in AVAILABLE_TOOLS.items()}
        if not allow_commands:
            self.tools.pop("run_command", None)
            self.tools.pop("list_directory", None)
            
            # Cập nhật prompt để bỏ hướng dẫn dùng lệnh
            prompt = CODER_SYSTEM_PROMPT.replace(
                "### run_command — Execute shell command\nAction: run_command\nCommand: python -c \"print('hello')\"\n\n", ""
            ).replace(
                "### list_directory — List directory contents\nAction: list_directory\nPath: ./src\n\n", ""
            ).replace(
                "- Use PowerShell syntax for run_command.", ""
            )
        else:
            prompt = CODER_SYSTEM_PROMPT

        self.messages = [
            {"role": "system", "content": prompt}
        ]
        self.telemetry = BalderTelemetry(log_file="traces_coder.jsonl")

    # ================================================================
    # Override run_step — Markdown Fence + JSON fallback
    # Tối ưu: reuse _coder_client, không tạo mới mỗi step
    # ================================================================
    async def run_step(self) -> dict:
        self.step_count += 1
        if self.step_count > 30:
            return {"type": "max_steps", "content": "Đã đạt giới hạn 30 bước."}

        try:
            # Context trim — giữ system + first user + 20 messages gần nhất
            if len(self.messages) > 24:
                self.messages = [self.messages[0]] + self.messages[-22:]

            # Sanitize messages
            clean_messages = []
            for msg in self.messages:
                clean_msg = dict(msg)
                if isinstance(clean_msg.get("content"), str):
                    clean_msg["content"] = sanitize_text(clean_msg["content"])
                clean_messages.append(clean_msg)

            # Gọi LLM — REUSE client (V4 hardware fix)
            response = await _coder_client.chat.completions.create(
                model=CODER_MODEL,
                messages=clean_messages,
                temperature=0.1
            )

            content = sanitize_text(response.choices[0].message.content or "")
            self.messages.append({"role": "assistant", "content": content})
            self.telemetry.add_node("thought", content)

            # Check TASK_COMPLETE
            if "TASK_COMPLETE" in content:
                self.telemetry.end_trace(content)
                return {"type": "text", "content": "TASK_COMPLETE"}

            # Parse: Markdown Fence trước → JSON fallback
            parsed = self._parse_markdown_fence(content)
            if parsed:
                action_name, action_args = parsed
            else:
                action_name, action_args = core_parse_action(content)

            # Execute
            if action_name and action_args is not None:
                thought = ""
                thought_match = re.search(
                    r'Thought:\s*(.*?)(?=\n\s*Action:)', content, re.DOTALL
                )
                if thought_match:
                    thought = thought_match.group(1).strip()
                    
                if action_name == "finish_task":
                    self.telemetry.end_trace(content)
                    return {"type": "text", "content": "TASK_COMPLETE"}

                if action_name not in self.tools:
                    result = f"Error: Tool '{action_name}' not found. Available: {list(self.tools.keys())} + ['finish_task']."
                else:
                    try:
                        if hasattr(self.tools[action_name], "__code__"):
                            # Là hàm bình thường
                            result = await asyncio.to_thread(self.tools[action_name], **action_args)
                        else:
                            # Là tool từ Langchain/BaseTool
                            result = await asyncio.to_thread(self.tools[action_name].invoke, action_args)
                    except Exception as e:
                        result = f"Error executing '{action_name}': {e}"

                self.telemetry.add_node("action", action_name, {"input": action_args})

                result_str = sanitize_text(str(result))
                if len(result_str) > MAX_OBSERVATION_CHARS:
                    result_str = result_str[:MAX_OBSERVATION_CHARS] + "… [TRUNCATED]"

                self.messages.append({"role": "user", "content": f"Observation: {result_str}"})
                self.telemetry.add_node("observation", result_str)

                return {
                    "type": "tool_call",
                    "thought": thought,
                    "action": action_name,
                    "action_input": action_args,
                    "observation": result_str
                }

            elif action_name and action_args is None:
                error_msg = (
                    f"Observation: Error — Could not parse arguments for '{action_name}'. "
                    f"Use this format:\n\n"
                    f"Action: create_file\nFile: path.py\n```\nyour code\n```"
                )
                self.messages.append({"role": "user", "content": error_msg})
                return {
                    "type": "parse_error",
                    "action": action_name,
                    "content": f"Parse failed for '{action_name}'."
                }

            else:
                self.telemetry.end_trace(content)
                return {"type": "text", "content": content}

        except Exception as e:
            return {"type": "error", "content": str(e)}

    # ================================================================
    # Markdown Fence Parser
    # ================================================================
    def _parse_markdown_fence(self, content: str):
        action_match = re.search(r'Action:\s*(\w+)', content)
        if not action_match:
            return None

        action_name = action_match.group(1).strip()
        after = content[action_match.end():]

        if action_name in ("create_file", "write_to_file"):
            file_m = re.search(r'File:\s*(.+?)(?:\n|$)', after)
            code_m = re.search(r'```(?:\w*)\n(.*?)```', after, re.DOTALL)
            if file_m and code_m:
                return "create_file", {
                    "filepath": file_m.group(1).strip(),
                    "content": code_m.group(1)
                }
            return None
            
        if action_name == "finish_task":
            return "finish_task", {}

        if action_name == "read_file":
            m = re.search(r'File:\s*(.+?)(?:\n|$)', after)
            if m:
                return "read_file", {"filepath": m.group(1).strip()}
            return None

        if action_name == "run_command":
            m = re.search(r'Command:\s*(.+?)(?:\n|$)', after)
            if m:
                return "run_command", {"command": m.group(1).strip()}
            return None

        if action_name == "list_directory":
            m = re.search(r'Path:\s*(.+?)(?:\n|$)', after)
            if m:
                return "list_directory", {"path": m.group(1).strip()}
            return None

        return None

    # ================================================================
    # High-level API: full ReAct loop
    # ================================================================
    async def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        self.telemetry.start_trace(user_input, model_name=CODER_MODEL)
        self.step_count = 0

        content = ""
        consecutive_errors = 0

        while self.step_count < 30:
            result = await self.run_step()

            if self.verbose:
                step_type = result.get("type", "?")
                if step_type == "tool_call":
                    print(f"  [Coder] Step {self.step_count}: ▶ {result.get('action', '?')}")
                elif step_type == "parse_error":
                    print(f"  [Coder] Step {self.step_count}: ⚠️ Parse error")
                    consecutive_errors += 1
                elif step_type == "text":
                    print(f"  [Coder] Step {self.step_count}: 💬 Text/Complete")
                elif step_type == "max_steps":
                    print(f"  [Coder] ⛔ Max steps reached")
                elif step_type == "error":
                    print(f"  [Coder] ❌ Error: {result.get('content', '')[:100]}")
                    consecutive_errors += 1

            # Circuit breaker
            if consecutive_errors >= 3:
                self.messages.append({
                    "role": "user",
                    "content": (
                        "SYSTEM: Actions could not be parsed. Use this format:\n\n"
                        "Action: create_file\nFile: path/to/file.py\n"
                        "```\nyour code here\n```"
                    )
                })
                consecutive_errors = 0

            if result["type"] in ("text", "max_steps", "error"):
                content = result.get("content", "")
                break

            if result["type"] == "tool_call":
                consecutive_errors = 0

        return content
