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

# Default address for Ollama or Llama.cpp Local Server
API_BASE = os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
API_KEY = os.getenv("LLM_API_KEY", "ollama")
MODEL_NAME = os.getenv("LLM_MODEL_V5", "gemma4:e4b")

client = AsyncOpenAI(
    base_url=API_BASE,
    api_key=API_KEY
)

# ============================================================================
# TEXT-BASED REACT PROMPT
# Instead of using OpenAI function calling (JSON schema), we embed tool 
# descriptions directly into the system prompt. The model generates simple text.
# This works significantly better for low-quantized models (IQ3_XS).
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
ANSWER: <your response>

## ONE-SHOT EXAMPLE (PERFORM ACTION)
User: "read config.json"
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

# Max ReAct steps to avoid infinite loops
MAX_REACT_STEPS = 15

# Max observation size to put back into context (avoid context window overflow)
MAX_OBSERVATION_CHARS = 4000


def sanitize_text(text: str) -> str:
    """
    Remove surrogate characters from text.
    Ollama sometimes returns text containing invalid UTF-16 surrogate characters,
    which causes the openai client to crash during JSON serialization.
    """
    if not text:
        return text
    return text.encode('utf-8', errors='replace').decode('utf-8')


def parse_action(text: str):
    """
    Parse Action and Action Input from the LLM response text.
    
    Returns:
        (action_name, action_args) - or (None, None) if no Action is found.
        action_args will be None if JSON parsing fails.
    """
    # Look for Action: line
    action_match = re.search(r'Action:\s*[`]?(\w+)[`]?', text)
    if not action_match:
        return None, None

    action_name = action_match.group(1).strip()

    # Find Action Input: after the Action line
    remaining_text = text[action_match.end():]
    input_match = re.search(r'Action\s*Input:\s*', remaining_text, re.IGNORECASE)
    if not input_match:
        return action_name, None

    raw_input = remaining_text[input_match.end():].strip()

    # Remove markdown code fences if added by the model
    raw_input = re.sub(r'^```(?:json)?\s*\n?', '', raw_input)

    # Extract JSON by counting braces (handles multi-line JSON)
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
        """Add user message and truncate history if too long."""
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
             c.print(f"  [dim][Context Enricher] Intent detected: {intent.primary_intent.value} (Confidence: {intent.confidence:.2f})[/]")
             if intent.is_ambiguous:
                 c.print(f"  [yellow][Context Enricher] ⚠️ Ambiguous query: {intent.ambiguity_reasons}[/]")
             c.print(f"\n[dim cyan]=== ENRICHED PROMPT ===\n{enriched_prompt}\n========================[/]\n")
        else:
            self.messages.append({"role": "user", "content": message})
            
        self.step_count = 0

    async def run_step(self) -> dict:
        """
        Execute one interaction step with the LLM (Text-based ReAct).
        
        Returns:
            dict with type:
            - "tool_call": LLM wants to call a tool (contains thought, action, action_input, observation)
            - "text": Final plain text response
            - "error": Error occurred
            - "cancelled": Interrupted by user
            - "max_steps": Exceeded maximum steps
        """
        # Check step limit
        self.step_count += 1
        if self.step_count > MAX_REACT_STEPS:
            return {
                "type": "max_steps",
                "content": f"Reached {MAX_REACT_STEPS} step limit. Stopping to avoid infinite loop."
            }

        try:
            # Sanitize entire message history before sending
            # (prevents surrogate chars from accumulating across steps)
            clean_messages = []
            for msg in self.messages:
                clean_msg = dict(msg)
                if isinstance(clean_msg.get("content"), str):
                    clean_msg["content"] = sanitize_text(clean_msg["content"])
                clean_messages.append(clean_msg)

            # Call LLM WITHOUT tools parameter — pure text generation
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=clean_messages,
                temperature=0.3
            )

            message = response.choices[0].message
            content = sanitize_text(message.content or "")

            # Save assistant response to history
            self.messages.append({"role": "assistant", "content": content})
            
            # Record Thought in Telemetry
            self.telemetry.add_node("thought", content)
            
            # 🌟 STEP 2: CODE SERIALIZER (Auto-generate TurnRoutePlan)
            # Instead of forcing the LLM to generate error-prone JSON, we auto-generate
            # the JSON based on Layer 1 Intent and the LLM's Action decision.
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
                # ---- LLM wants to call a tool ----
                
                # Extract Thought (part before Action:)
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
                
                # Truncate observation if too long (avoid context overflow)
                result_str = sanitize_text(str(result))
                if len(result_str) > MAX_OBSERVATION_CHARS:
                    result_str = (
                        result_str[:MAX_OBSERVATION_CHARS]
                        + f"\n\n... [TRUNCATED — showing first {MAX_OBSERVATION_CHARS} chars of {len(result_str)} total]"
                    )

                # Add observation to history for LLM awareness
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
                # ---- LLM called a tool but JSON is malformed ----
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
                # ---- No Action → this is the final answer ----
                if self.use_enricher:
                    self.enricher.record_assistant_response(content)
                self.telemetry.end_trace(content)
                return {"type": "text", "content": content}

        except asyncio.CancelledError:
            return {"type": "cancelled", "content": "Generation stopped by user."}
        except Exception as e:
            return {"type": "error", "content": str(e)}
