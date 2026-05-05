import json
import os
import re
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI
from Core.tools import AVAILABLE_TOOLS
from Core.context_enricher import PromptEnricher, create_enriched_system_prompt
from Core.telemetry import BalderTelemetry

load_dotenv()

# Địa chỉ mặc định cho Ollama hoặc Llama.cpp Local Server
API_BASE = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
API_KEY = os.getenv("LLM_API_KEY", "ollama")
MODEL_NAME = os.getenv("LLM_MODEL_V5", "gemma4:e4b")

client = AsyncOpenAI(
    base_url=API_BASE,
    api_key=API_KEY
)

# ============================================================================
# TEXT-BASED REACT PROMPT
# Thay vì dùng OpenAI function calling (JSON schema), ta nhúng mô tả tool
# trực tiếp vào system prompt. Model chỉ cần sinh text theo format đơn giản.
# Điều này hoạt động tốt hơn nhiều với model quantize thấp (IQ3_XS).
# ============================================================================

SYSTEM_PROMPT = """You are Balder, an elite autonomous AI engineering agent.
## RESPONSE FORMAT

You MUST output your reasoning in a `Thought` block.
If you need to use a tool, you MUST immediately output `Action:` and `Action Input:` after your thought.

Example for taking an action:
Thought: User wants to read a file.
Action: read_file
Action Input: {"filepath": "config.json"}

Example for just talking:
Thought: User is asking a general question.
TRẢ LỜI: <your response>

## ONE-SHOT EXAMPLE (PERFORM ACTION)
User: "đọc file config.json"
Thought: User wants to read a configuration file. I should check if it exists.
```json
{
  "mode": "act",
  "intent_class": "PERFORM_ACTION",
  "confidence": 1.0,
  "ambiguity_state": "none",
  "risk_level": "low",
  "needs_confirmation": false,
  "steps": [{ "capability": "filesystem.read", "endpoint": "file" }]
}
```
Action: read_file
Action Input: {"filepath": "config.json"}

## INTENT HINT (ROUTING GUIDANCE)
Use the [ROUTING HIERARCHY] metadata provided in the user message to fill your TurnRoutePlan.

## SAFETY & OS RULES
1. WINDOWS ONLY: Never use Linux commands (rm, chmod, sudo, nohup, dd, etc.).
2. DELETION: Use PowerShell `Remove-Item -Recurse -Force` via `filesystem_delete`.
3. CAUTION: For any 'high' risk step, ask for user confirmation.
4. ACTION ENFORCEMENT: If the user requests a file operation (create, read) or terminal command, you MUST IMMEDATELY call the corresponding tool. Do NOT just say "I will do this".

## AVAILABLE TOOLS

1. `run_command` — Execute PowerShell commands (non-destructive).
2. `filesystem_delete` — Safety wrapper for deleting files/folders.
3. `read_file` / `create_file` / `list_directory` — Basic file operations.

## HOW TO USE TOOLS

When you need to use a tool, respond in this EXACT format:

Thought: <your reasoning about what to do next>
Action: <tool_name>
Action Input: <JSON object with the arguments>

Then STOP and wait. The system will execute the tool and show you an Observation with the result.
After seeing the Observation, continue reasoning with another Thought, or give your final answer.

## WHEN YOU ARE DONE

When you have gathered enough information and are ready to give a final answer, just respond normally in plain text. Do NOT include Action or Action Input in your final answer.
"""

# Giới hạn tối đa số bước ReAct để tránh vòng lặp vô hạn
MAX_REACT_STEPS = 15

# Giới hạn kích thước observation đưa lại vào context (tránh tràn context window)
MAX_OBSERVATION_CHARS = 4000


def sanitize_text(text: str) -> str:
    """
    Loai bo surrogate characters khoi text.
    Ollama doi khi tra ve text chua ky tu UTF-16 surrogate bi loi,
    khien openai client crash khi serialize JSON o lan goi tiep theo.
    """
    if not text:
        return text
    return text.encode('utf-8', errors='replace').decode('utf-8')


def parse_action(text: str):
    """
    Parse Action và Action Input từ response text của LLM.
    
    Returns:
        (action_name, action_args) - hoặc (None, None) nếu không tìm thấy Action.
        action_args sẽ là None nếu parse JSON thất bại.
    """
    # Tìm dòng Action:
    action_match = re.search(r'Action:\s*[`]?(\w+)[`]?', text)
    if not action_match:
        return None, None

    action_name = action_match.group(1).strip()

    # Tìm Action Input: sau dòng Action
    remaining_text = text[action_match.end():]
    input_match = re.search(r'Action\s*Input:\s*', remaining_text, re.IGNORECASE)
    if not input_match:
        return action_name, None

    raw_input = remaining_text[input_match.end():].strip()

    # Loại bỏ markdown code fences nếu model thêm vào
    raw_input = re.sub(r'^```(?:json)?\s*\n?', '', raw_input)

    # Trích xuất JSON bằng cách đếm ngoặc nhọn (xử lý JSON nhiều dòng)
    try:
        json_start = raw_input.index('{')
        brace_count = 0
        for i in range(json_start, len(raw_input)):
            if raw_input[i] == '{':
                brace_count += 1
            elif raw_input[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    json_str = raw_input[json_start:i + 1]
                    return action_name, json.loads(json_str)
    except (ValueError, json.JSONDecodeError):
        pass

    return action_name, None


class ReActAgent:
    def __init__(self, use_enricher=True, verbose=False):
        self.use_enricher = use_enricher
        self.enricher = PromptEnricher() if use_enricher else None
        
        system_prompt = create_enriched_system_prompt() if use_enricher else SYSTEM_PROMPT
        self.messages = [
            {"role": "system", "content": system_prompt}
        ]
        self.verbose = verbose
        self.step_count = 0
        self.telemetry = BalderTelemetry()

    def add_user_message(self, message: str, extra_context: str = ""):
        """Thêm tin nhắn người dùng và nén lịch sử nếu quá dài."""
        # Truncate history to keep last 10 messages (plus system prompt)
        if len(self.messages) > 11:
            self.messages = [self.messages[0]] + self.messages[-10:]
            
        if self.use_enricher:
            enriched_prompt, intent = self.enricher.enrich(message, extra_context)
            self.current_intent = intent
            self.messages.append({"role": "user", "content": enriched_prompt})
            self.telemetry.start_trace(message, intent, model_name=MODEL_NAME)
            if self.verbose:
                 from rich.console import Console
                 c = Console()
                 c.print(f"  [dim][Context Enricher] Phát hiện ý định: {intent.primary_intent.value} (Confidence: {intent.confidence:.2f})[/]")
                 if intent.is_ambiguous:
                     c.print(f"  [yellow][Context Enricher] ⚠️ Câu hỏi mơ hồ: {intent.ambiguity_reasons}[/]")
                 c.print(f"\n[dim cyan]=== ENRICHED PROMPT ===\n{enriched_prompt}\n========================[/]\n")
        else:
            self.messages.append({"role": "user", "content": message})
            
        self.step_count = 0

    async def run_step(self) -> dict:
        """
        Thực thi một bước giao tiếp với LLM (Text-based ReAct).
        
        Returns:
            dict với type:
            - "tool_call": LLM muốn gọi tool (có thought, action, action_input, observation)
            - "text": Phản hồi cuối cùng bằng text thuần
            - "error": Có lỗi xảy ra
            - "cancelled": Bị người dùng ngắt
            - "max_steps": Đã vượt quá số bước tối đa
        """
        # Kiểm tra giới hạn bước
        self.step_count += 1
        if self.step_count > MAX_REACT_STEPS:
            return {
                "type": "max_steps",
                "content": f"Đã đạt giới hạn {MAX_REACT_STEPS} bước. Dừng lại để tránh vòng lặp vô hạn."
            }

        try:
            # Sanitize toàn bộ message history trước khi gửi
            # (phòng trường hợp surrogate chars tích lũy từ các bước trước)
            clean_messages = []
            for msg in self.messages:
                clean_msg = dict(msg)
                if isinstance(clean_msg.get("content"), str):
                    clean_msg["content"] = sanitize_text(clean_msg["content"])
                clean_messages.append(clean_msg)

            # Gọi LLM KHÔNG có tham số tools — chỉ sinh text thuần
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=clean_messages,
                temperature=0.3
            )

            message = response.choices[0].message
            content = sanitize_text(message.content or "")

            # Lưu phản hồi assistant vào lịch sử
            self.messages.append({"role": "assistant", "content": content})
            
            # Record Thought in Telemetry
            self.telemetry.add_node("thought", content)
            
            # 🌟 STEP 2: CODE SERIALIZER (Auto-generate TurnRoutePlan)
            # Thay vì bắt LLM sinh JSON dễ lỗi ngoặc, ta tự generate JSON dựa vào Intent Tầng 1
            # và kết hợp với Quyết định Action của LLM.
            action_name, action_args = parse_action(content)
            
            intent_obj = getattr(self, "current_intent", None)
            plan_json = {
                "mode": "act" if action_name else "chat",
                "intent_class": intent_obj.primary_intent.value if intent_obj else "talk_about_domain",
                "confidence": intent_obj.confidence if intent_obj else 0.8,
                "ambiguity_state": "vague_pronoun" if (intent_obj and intent_obj.is_ambiguous) else "none",
                "risk_level": "high" if action_name in ["filesystem_delete", "run_command"] else "low",
                "needs_confirmation": False,
                "steps": []
            }
            if plan_json["intent_class"] == "multi_action":
                 plan_json["mode"] = "multi_step"
            
            # Record plan into Telemetry / Upstream Routers
            self.telemetry.record_plan(plan_json)

            if action_name and action_args is not None:
                # ---- LLM muốn gọi tool ----
                
                # Trích xuất Thought (phần trước Action:)
                thought = ""
                thought_match = re.search(
                    r'Thought:\s*(.*?)(?=\n\s*Action:)', content, re.DOTALL
                )
                if thought_match:
                    thought = thought_match.group(1).strip()

                # 3. Handle Tool Hallucination (Internal Validator)
                if action_name not in AVAILABLE_TOOLS:
                    result = f"Error: Tool '{action_name}' not found. Available tools: {list(AVAILABLE_TOOLS.keys())}. Please use only available tools."
                else:
                    # 4. Execute Action (Safe Layer)
                    try:
                        result = AVAILABLE_TOOLS[action_name](**action_args)
                    except Exception as e:
                        result = f"Error executing tool '{action_name}': {str(e)}"

                # Record Action in Telemetry
                self.telemetry.add_node("action", action_name, {"input": action_args})
                
                # Cắt bớt observation nếu quá dài (tránh tràn context)
                result_str = sanitize_text(str(result))
                if len(result_str) > MAX_OBSERVATION_CHARS:
                    result_str = (
                        result_str[:MAX_OBSERVATION_CHARS]
                        + f"\n\n... [TRUNCATED — showing first {MAX_OBSERVATION_CHARS} chars of {len(result_str)} total]"
                    )

                # Thêm observation vào lịch sử để LLM thấy kết quả
                self.messages.append({
                    "role": "user",
                    "content": f"Observation: {result_str}"
                })

                # Record Observation in Telemetry
                self.telemetry.add_node("observation", result_str)

                return {
                    "type": "tool_call",
                    "thought": thought,
                    "action": action_name,
                    "action_input": action_args,
                    "observation": result_str
                }

            elif action_name and action_args is None:
                # ---- LLM gọi tool nhưng JSON bị lỗi ----
                error_msg = (
                    f"Observation: Error — Could not parse Action Input JSON for tool '{action_name}'. "
                    f"Please provide valid JSON. Example: Action Input: {{\"key\": \"value\"}}"
                )
                self.messages.append({"role": "user", "content": error_msg})

                return {
                    "type": "parse_error",
                    "action": action_name,
                    "content": f"Failed to parse JSON for '{action_name}'. Asking LLM to retry."
                }

            else:
                # ---- Không có Action → đây là câu trả lời cuối cùng ----
                if self.use_enricher:
                    self.enricher.record_assistant_response(content)
                self.telemetry.end_trace(content)
                return {"type": "text", "content": content}

        except asyncio.CancelledError:
            return {"type": "cancelled", "content": "Generation stopped by user."}
        except Exception as e:
            return {"type": "error", "content": str(e)}
