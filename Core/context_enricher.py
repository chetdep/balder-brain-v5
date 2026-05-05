"""
==========================================================================
CONTEXT ENRICHER — Context Augmentation Module for LLMs
==========================================================================
Purpose:
  Solves the problem of LLMs (especially low-quantized models) failing 
  to understand short, ambiguous, or context-lacking queries.

Architecture: 3-TIER FILTERING

  Tier 1: Intent Classifier (Local, rule-based)
    → Quickly classifies intent: action / question / debug / context_switch
    → No LLM call required; processed via regex + keyword matching

  Tier 2: Context Window Manager
    → Manages sliding window of conversation history
    → Automatically detects context switches ("nevermind", "forget the above")
    → Injects relevant context into the prompt

  Tier 3: Prompt Enricher
    → Transforms short/ambiguous queries into full context prompts
    → Adds metadata: intent, context, constraints

Integration into agent_core.py:
  user_input → ContextEnricher.enrich() → enriched_prompt → LLM

Test command: python context_enricher.py
==========================================================================
"""

import re
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


# ============================================================================
# TIER 1: INTENT CLASSIFIER (Rule-based, no LLM required)
# ============================================================================

class IntentType(Enum):
    """Classification of user intent."""
    ACTION = "action"              # Request to perform an action (create, run, deploy...)
    QUESTION = "question"          # Ask for information / knowledge
    DEBUG = "debug"                # Debug / fix errors
    STATUS_CHECK = "status_check"  # Check system/task status
    CONTEXT_SWITCH = "context_switch"  # Change topic
    CONFIRMATION = "confirmation"  # Confirm / agree
    AMBIGUOUS = "ambiguous"        # Undetermined/vague
    MULTI_ACTION = "multi_action"  # Complex action sequence



@dataclass
class ActionStep:
    """Represents a single step in an action sequence."""
    step_id: int
    action: str
    target: Optional[str] = None
    condition: Optional[str] = None
    depends_on: Optional[int] = None  # ID của bước trước đó

@dataclass
class ActionPipeline:
    """A sequence of actions to be executed."""
    steps: List[ActionStep] = field(default_factory=list)
    total_steps: int = 0
    is_conditional: bool = False

@dataclass
class IntentAnalysis:
    """Intent analysis results (JAVIS Hierarchical)."""
    primary_intent: IntentType
    intent_class: str              # Tier 1: talk / action / workflow / status / debug
    capability_group: str          # Tier 2: office.email, web.search, etc.
    endpoint: Optional[str]        # Tier 3: email.send, web.news, etc.
    confidence: float              # 0.0 → 1.0
    detected_actions: List[str]    
    detected_targets: List[str]    
    is_ambiguous: bool             
    ambiguity_reasons: List[str]   
    is_context_switch: bool        
    has_temporal_ref: bool         
    urgency: str = "normal"        
    pipeline: Optional[ActionPipeline] = None 


class IntentClassifier:
    """
    Rule-based Intent Classifier.
    Phân loại nhanh ý định mà không cần gọi LLM.
    """
    
    # Pipeline indicators
    PIPELINE_CONJUNCTIONS = {
        "sequential": [r"sau đó", r"rồi", r"tiếp theo", r"tiếp đến", r"sau khi", r"xong thì"],
        "conditional": [r"nếu", r"trong trường hợp", r"nếu có lỗi", r"nếu thành công"],
        "parallel": [r"vừa", r"đồng thời", r"và cũng"],
    }
    
    # Pattern detection rules
    ACTION_KEYWORDS = {
        "create": ["tạo", "create", "init", "khởi tạo", "generate", "viết", "soạn", "gửi"],
        "run": ["chạy", "run", "execute", "thực thi", "start", "khởi động"],
        "deploy": ["deploy", "triển khai", "đẩy lên", "push"],
        "fix": ["sửa", "fix", "repair", "patch", "hotfix"],
        "delete": ["xóa", "delete", "remove", "hủy", "drop"],
        "update": ["cập nhật", "update", "upgrade", "nâng cấp"],
        "check": ["kiểm tra", "check", "verify", "test", "xem"],
        "backup": ["backup", "sao lưu", "save"],
        "restart": ["restart", "khởi động lại", "reboot"],
        "install": ["cài", "install", "setup", "cài đặt"],
        "read": ["đọc", "read", "xem", "mở"],
        "search": ["tìm", "search", "find", "tra cứu"],
    }
    
    TARGET_KEYWORDS = {
        "file": ["file", "tệp", "tập tin"],
        "server": ["server", "máy chủ", "vps"],
        "database": ["database", "db", "cơ sở dữ liệu", "data"],
        "api": ["api", "endpoint", "route"],
        "config": ["config", "cấu hình", "settings", "env"],
        "code": ["code", "mã", "source", "module", "class", "function", "hàm"],
        "docker": ["docker", "container", "image"],
        "git": ["git", "repo", "commit", "branch", "pull", "push"],
        "test": ["test", "kiểm thử", "unittest", "pytest"],
        "log": ["log", "nhật ký", "lịch sử"],
        "project": ["project", "dự án", "workspace"],
        "email": ["email", "thư", "mail", "gmail", "outlook", "inbox"],
        "drive": ["drive", "cloud", "lưu trữ", "dropbox"],
        "web": ["web", "trang web", "website", "url", "link", "internet", "browser"],
    }

    # Hierarchy mapping rules
    CAPABILITY_MAP = {
        "office.email": [r"\bemail\b", r"\bthư\b", r"\bmail\b", "gmail", "inbox"],
        "office.drive": ["drive", "storage", "upload", "download"],
        "web.search": ["search", "lookup", "news", "google"],
        "web.read": ["read", "view", "content", "open page"],
        "desktop.system": ["run", "cmd", "powershell", "system"],
        "self_model.trace": ["architecture", "module", "where is", "code structure"],
    }
    
    DEBUG_INDICATORS = [
        "lỗi", "error", "bug", "crash", "fail", "loi", "sai", "không chạy",
        "không hoạt động", "bị hỏng", "exception", "traceback", "broken",
        "không được", "hỏng", "die", "chết", "treo", "stuck", "freeze",
        "latency", "không nhận diện"
    ]
    
    CONTEXT_SWITCH_PATTERNS = [
        r"à mà thôi",
        r"quên (?:cái|điều) (?:trên|đó|nãy) đi",
        r"thôi không",
        r"chuyển sang",
        r"câu hỏi mới",
        r"topic (?:mới|khác)",
        r"bỏ qua",
        r"skip",
        r"nevermind",
    ]
    
    STATUS_CHECK_PATTERNS = [
        r"xong chưa\??",
        r"đã .+ chưa\??",
        r"tình trạng",
        r"status",
        r"progress",
        r"kết quả",
        r"thế nào rồi\??",
        r"sao rồi\??",
    ]
    
    TEMPORAL_PATTERNS = [
        r"hôm (?:trước|qua|kia)",
        r"lúc (?:nãy|trước|sáng|chiều)",
        r"lần (?:trước|cuối)",
        r"ngày (?:trước|hôm qua)",
        r"tuần (?:trước|rồi)",
        r"last (?:time|week|day)",
        r"yesterday",
        r"earlier",
    ]
    
    AMBIGUITY_PRONOUNS = [
        r"\bcái (?:đó|này|kia|nào)\b",
        r"\bnó\b",
        r"\bchúng\b",
        r"\b(?:cái|thứ|điều) (?:đó|này)\b",
        r"\bthat\b",
        r"\bit\b",
        r"\bthem\b",
    ]
    
    URGENCY_PATTERNS = {
        "critical": [r"khẩn cấp", r"critical", r"emergency", r"ngay lập tức", r"ASAP"],
        "urgent": [r"gấp", r"nhanh", r"urgent", r"sớm", r"ngay", r"luôn đi"],
    }

    def _parse_pipeline(self, text: str, actions: List[str], targets: List[str]) -> Optional[ActionPipeline]:
        """Analyze action sequences from text."""
        if len(actions) < 2 and not any(re.search(p, text) for p in self.PIPELINE_CONJUNCTIONS["sequential"] + self.PIPELINE_CONJUNCTIONS["conditional"]):
            return None
            
        steps = []
        is_conditional = False
        
        # Heuristic to separate steps based on keywords: "then", "after", "if"
        # Split text into parts
        delimiters = ["sau đó", "rồi", "tiếp theo", "nếu", "và", "xong thì"]
        pattern = '|'.join(map(re.escape, delimiters))
        parts = re.split(f'({pattern})', text)
        
        step_idx = 1
        current_condition = None
        
        for i, part in enumerate(parts):
            part = part.strip()
            if not part: continue
            
            if part in delimiters:
                if part == "nếu":
                    is_conditional = True
                    current_condition = "pending" # Sẽ lấy ở part tiếp theo
                continue
            
            # Find action in this part
            step_action = "unknown"
            for act, kws in self.ACTION_KEYWORDS.items():
                if any(kw in part for kw in kws):
                    step_action = act
                    break
            
            # Find target in this part
            step_target = None
            for tgt, kws in self.TARGET_KEYWORDS.items():
                if any(kw in part for kw in kws):
                    step_target = tgt
                    break
            
            if step_action != "unknown" or step_target:
                cond = None
                if current_condition == "pending":
                    cond = part
                    current_condition = None
                
                steps.append(ActionStep(
                    step_id=step_idx,
                    action=step_action,
                    target=step_target,
                    condition=cond,
                    depends_on=step_idx - 1 if step_idx > 1 else None
                ))
                step_idx += 1
        
        if len(steps) > 1:
            return ActionPipeline(steps=steps, total_steps=len(steps), is_conditional=is_conditional)
        return None

    def classify(self, text: str) -> IntentAnalysis:
        """Classify intent from text input."""
        text_lower = text.lower().strip()
        
        # 1. Detect context switch
        is_context_switch = any(
            re.search(p, text_lower) for p in self.CONTEXT_SWITCH_PATTERNS
        )
        
        # 2. Detect actions
        detected_actions = []
        for action_group, keywords in self.ACTION_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    detected_actions.append(action_group)
                    break
        
        # 3. Detect targets  
        detected_targets = []
        for target_group, keywords in self.TARGET_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    detected_targets.append(target_group)
                    break
        
        # 4. Detect debug intent
        is_debug = any(ind in text_lower for ind in self.DEBUG_INDICATORS)
        
        # 5. Detect status check
        is_status = any(
            re.search(p, text_lower) for p in self.STATUS_CHECK_PATTERNS
        )
        
        # 6. Detect temporal reference
        has_temporal = any(
            re.search(p, text_lower) for p in self.TEMPORAL_PATTERNS
        )
        
        # 7. Detect ambiguity
        ambiguity_reasons = []
        has_vague_pronoun = any(
            re.search(p, text_lower) for p in self.AMBIGUITY_PRONOUNS
        )
        if has_vague_pronoun:
            ambiguity_reasons.append("vague_pronoun")
        if len(text_lower.split()) <= 3 and not detected_actions:
            ambiguity_reasons.append("too_short")
        if has_temporal:
            ambiguity_reasons.append("temporal_reference_without_context")
        if detected_actions and not detected_targets:
            ambiguity_reasons.append("action_without_target")
        
        is_ambiguous = len(ambiguity_reasons) > 0
        
        # 8. Detect urgency
        urgency = "normal"
        for level, patterns in self.URGENCY_PATTERNS.items():
            if any(re.search(p, text_lower) for p in patterns):
                urgency = level
                break
        
        # 9. Determine primary intent & confidence
        if is_context_switch:
            primary = IntentType.CONTEXT_SWITCH
            confidence = 0.9
        elif is_debug:
            primary = IntentType.DEBUG
            confidence = 0.8
        elif is_status:
            primary = IntentType.STATUS_CHECK
            confidence = 0.85
        elif detected_actions:
            # Check for pipeline before assigning simple ACTION
            pipeline = self._parse_pipeline(text_lower, detected_actions, detected_targets)
            if pipeline:
                primary = IntentType.MULTI_ACTION
                confidence = 0.9
            else:
                primary = IntentType.ACTION
                confidence = 0.8 if detected_targets else 0.6
        elif text_lower.endswith("?") or any(
            kw in text_lower for kw in [
                "tại sao", "vì sao", "làm sao", "thế nào",
                "what", "how", "why", "when", "where"
            ]
        ):
            primary = IntentType.QUESTION
            confidence = 0.7
        elif is_ambiguous:
            primary = IntentType.AMBIGUOUS
            confidence = 0.3
        else:
            primary = IntentType.QUESTION
            confidence = 0.5

        # 10. Determine Hierarchy (3 Layers)
        intent_class = "talk_about_domain"
        capability_group = "chat.general"
        endpoint = None

        if primary == IntentType.ACTION:
            intent_class = "perform_action"
        elif primary == IntentType.MULTI_ACTION:
            intent_class = "multi_step_action"
        elif primary == IntentType.DEBUG:
            intent_class = "debug_failure"
        elif primary == IntentType.STATUS_CHECK:
            intent_class = "ask_for_status"

        # Map capability
        for cap, keywords in self.CAPABILITY_MAP.items():
            for kw in keywords:
                if re.search(kw, text_lower):
                    capability_group = cap
                    break
            if capability_group != "chat.general":
                break
        
        # Heuristic for endpoint
        if capability_group == "office.email":
            if any(kw in text_lower for kw in ["gửi", "send", "viết"]): endpoint = "email.send"
            elif any(kw in text_lower for kw in ["đọc", "xem", "read"]): endpoint = "email.read"
            else: endpoint = "email.stats"
        elif capability_group == "web.search":
            if "tin tức" in text_lower or "news" in text_lower: endpoint = "web.news"
            else: endpoint = "web.general"

        return IntentAnalysis(
            primary_intent=primary,
            intent_class=intent_class,
            capability_group=capability_group,
            endpoint=endpoint,
            confidence=confidence,
            detected_actions=list(set(detected_actions)),
            detected_targets=list(set(detected_targets)),
            is_ambiguous=is_ambiguous,
            ambiguity_reasons=ambiguity_reasons,
            is_context_switch=is_context_switch,
            has_temporal_ref=has_temporal,
            urgency=urgency,
            pipeline=self._parse_pipeline(text_lower, detected_actions, detected_targets) if 'pipeline' not in locals() else pipeline
        )


# ============================================================================
# TIER 2: CONTEXT WINDOW MANAGER
# ============================================================================

@dataclass
class ConversationTurn:
    """Một lượt trao đổi trong conversation."""
    role: str           # "user" hoặc "assistant"
    content: str
    timestamp: float
    intent: Optional[IntentAnalysis] = None


class ContextWindowManager:
    """
    Quản lý sliding window của conversation history.
    
    Features:
    - Auto-detect context switch → reset window
    - Summarize old turns khi window đầy
    - Track active topic
    """
    
    def __init__(self, max_turns: int = 10, max_chars: int = 4000):
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.history: List[ConversationTurn] = []
        self.active_topic: Optional[str] = None
        self.topic_keywords: List[str] = []
    
    def add_turn(self, role: str, content: str, intent: Optional[IntentAnalysis] = None):
        """Thêm một lượt nói vào history."""
        turn = ConversationTurn(
            role=role,
            content=content,
            timestamp=time.time(),
            intent=intent
        )
        
        # Nếu phát hiện context switch → reset
        if intent and intent.is_context_switch:
            self._handle_context_switch(content)
        
        self.history.append(turn)
        
        # Trim history nếu vượt max_turns
        if len(self.history) > self.max_turns:
            self.history = self.history[-self.max_turns:]
    
    def _handle_context_switch(self, new_content: str):
        """Xử lý khi user chuyển đề tài."""
        # Giữ lại tối đa 2 turns gần nhất làm context chuyển tiếp
        if len(self.history) > 2:
            self.history = self.history[-2:]
        self.active_topic = None
        self.topic_keywords = []
    
    def get_relevant_context(self, current_input: str) -> str:
        """
        Trả về context string relevant cho câu hỏi hiện tại.
        Format dễ đọc để inject vào prompt.
        """
        if not self.history:
            return ""
        
        # Lấy N turns gần nhất, vừa đủ max_chars
        context_parts = []
        total_chars = 0
        
        for turn in reversed(self.history):
            turn_text = f"[{turn.role.upper()}]: {turn.content}"
            if total_chars + len(turn_text) > self.max_chars:
                break
            context_parts.insert(0, turn_text)
            total_chars += len(turn_text)
        
        if not context_parts:
            return ""
        
        return (
            "## LỊCH SỬ HỘI THOẠI GẦN NHẤT\n"
            + "\n".join(context_parts)
            + "\n---\n"
        )
    
    def get_active_topic_summary(self) -> str:
        """Tóm tắt topic đang thảo luận."""
        if not self.history:
            return "Chưa có ngữ cảnh hội thoại."
        
        # Lấy topic từ các user messages gần nhất
        user_messages = [
            t.content for t in self.history[-5:]
            if t.role == "user"
        ]
        
        if not user_messages:
            return "Chưa có ngữ cảnh hội thoại."
        
        return f"Topic gần nhất: {user_messages[-1][:100]}"


# ============================================================================
# TIER 3: PROMPT ENRICHER — Transforms ambiguous queries → full prompts
# ============================================================================

class PromptEnricher:
    """
    Transforms short/ambiguous queries into prompts with full context for the LLM.
    
    Processing Flow:
    1. Receive raw input + IntentAnalysis + context
    2. Build enriched prompt with:
       - Intent metadata
       - Relevant context from history
       - Specific instructions for the LLM based on intent type
       - Constraints / guardrails
    """
    
    # Templates for each intent type
    INTENT_TEMPLATES = {
        IntentType.ACTION: (
            "## ACTION REQUEST\n"
            "User requested an action.\n"
            "- Detected actions: {actions}\n"
            "- Targets: {targets}\n"
            "- Urgency: {urgency}\n\n"
            "INSTRUCTIONS: Analyze the request, create a plan, and execute step-by-step.\n"
            "If information is missing, ASK before proceeding.\n"
        ),
        IntentType.DEBUG: (
            "## DEBUG / FIX REQUEST\n"
            "User is facing an issue and needs debug support.\n\n"
            "INSTRUCTIONS:\n"
            "1. Identify: what error? where? error message?\n"
            "2. Propose diagnostic steps (check logs, rerun, etc.)\n"
            "3. If enough info exists → propose a specific fix\n"
        ),
        IntentType.STATUS_CHECK: (
            "## STATUS CHECK\n"
            "User wants to know the status of a task.\n\n"
            "INSTRUCTIONS: Check the latest context to identify what the user is asking about.\n"
            "If no context exists → ask for clarification.\n"
        ),
        IntentType.QUESTION: (
            "## INFORMATION QUERY\n"
            "User is asking for knowledge or information.\n\n"
            "INSTRUCTIONS: Provide an accurate, structured response with examples if possible.\n"
        ),
        IntentType.CONTEXT_SWITCH: (
            "## CONTEXT SWITCH\n"
            "User wants to switch to a new topic. IGNORE previous context.\n\n"
            "INSTRUCTIONS: Focus 100% on the user's new request.\n"
            "DO NOT pull information from the previous topic.\n"
        ),
        IntentType.AMBIGUOUS: (
            "## AMBIGUOUS QUERY\n"
            "The user's request is not clear enough.\n"
            "- Ambiguity reasons: {ambiguity_reasons}\n\n"
            "INSTRUCTIONS:\n"
            "1. List possible interpretations\n"
            "2. Select the most likely interpretation based on context (if any)\n"
            "3. Answer based on that interpretation, BUT ask for confirmation\n"
            "4. DO NOT invent actions or results\n"
        ),
        IntentType.CONFIRMATION: (
            "## CONFIRMATION\n"
            "User is confirming or agreeing with a previous proposal.\n\n"
            "INSTRUCTIONS: Execute the action proposed in the context.\n"
        ),
        IntentType.MULTI_ACTION: (
            "## COMPLEX ACTION SEQUENCE (PIPELINE)\n"
            "User requested a series of related actions.\n"
            "- Total steps: {total_steps}\n"
            "- Pipeline details: \n{pipeline_details}\n\n"
            "INSTRUCTIONS:\n"
            "1. Execute steps SEQUENTIALLY as analyzed.\n"
            "2. Check conditions (if any) before each step.\n"
            "3. The observation of the previous step must be used to decide the next step.\n"
            "4. Report progress after each major step.\n"
        ),
    }
    
    def __init__(self):
        self.classifier = IntentClassifier()
        self.context_manager = ContextWindowManager()
    
    def enrich(
        self,
        raw_input: str,
        extra_context: Optional[Dict] = None
    ) -> Tuple[str, IntentAnalysis]:
        """
        Converts raw input into an enriched prompt.
        
        Args:
            raw_input: User's original query
            extra_context: Additional context (active file, project info, etc.)
        
        Returns:
            (enriched_prompt, intent_analysis)
        """
        # Bước 1: Phân loại ý định
        intent = self.classifier.classify(raw_input)
        
        # Bước 2: Lấy conversation context
        history_context = self.context_manager.get_relevant_context(raw_input)
        
        # Bước 3: Xây dựng enriched prompt
        enriched = self._build_enriched_prompt(
            raw_input=raw_input,
            intent=intent,
            history_context=history_context,
            extra_context=extra_context
        )
        
        # Bước 4: Lưu turn vào history
        self.context_manager.add_turn("user", raw_input, intent)
        
        return enriched, intent
    
    def record_assistant_response(self, response: str):
        """Record the assistant's response in history."""
        self.context_manager.add_turn("assistant", response)
    
    def _build_enriched_prompt(
        self,
        raw_input: str,
        intent: IntentAnalysis,
        history_context: str,
        extra_context: Optional[Dict]
    ) -> str:
        """Build an enriched prompt from all information."""
        
        parts = []
        
        # ── Phần 1: Intent metadata (Hierarchical) ──
        template = self.INTENT_TEMPLATES.get(
            intent.primary_intent,
            self.INTENT_TEMPLATES[IntentType.QUESTION]
        )
        
        # Inject Hierarchy Info
        hierarchy_info = f"""[ROUTING HIERARCHY]
- Intent Class: {intent.intent_class}
- Capability Group: {intent.capability_group}
- Endpoint: {intent.endpoint or 'N/A'}
"""
        
        # Format template với data
        pipeline_details = ""
        if intent.pipeline:
            for s in intent.pipeline.steps:
                cond = f" (Condition: {s.condition})" if s.condition else ""
                dep = f" (Depends on step: {s.depends_on})" if s.depends_on else ""
                pipeline_details += f"  Step {s.step_id}: {s.action} on {s.target or 'N/A'}{cond}{dep}\n"

        template = template.format(
            actions=", ".join(intent.detected_actions) or "không rõ",
            targets=", ".join(intent.detected_targets) or "không rõ",
            urgency=intent.urgency,
            ambiguity_reasons=", ".join(intent.ambiguity_reasons) or "N/A",
            total_steps=intent.pipeline.total_steps if intent.pipeline else 0,
            pipeline_details=pipeline_details
        )
        parts.append(hierarchy_info)
        parts.append(template)
        
        # ── Phần 2: Conversation history ──
        if history_context:
            parts.append(history_context)
        
        # ── Part 3: Extra context (project, active file, etc.) ──
        if extra_context:
            ctx_lines = ["## ADDITIONAL CONTEXT"]
            for key, value in extra_context.items():
                ctx_lines.append(f"- {key}: {value}")
            parts.append("\n".join(ctx_lines) + "\n")
        
        # ── Part 4: Original user query ──
        parts.append(f"## USER QUERY\n{raw_input}")
        
        # ── Part 5: Constraints / Guardrails ──
        if intent.is_ambiguous:
            parts.append(
                "\n⚠️ NOTE: This query is AMBIGUOUS. "
                "State clearly how you interpreted the query and ask for clarification if needed."
            )
        
        return "\n".join(parts)


# ============================================================================
# ENRICHED AGENT — Integrates ContextEnricher into the ReAct Agent
# ============================================================================

def create_enriched_system_prompt() -> str:
    """
    Advanced system prompt for agents with context enrichment.
    Compared to the original SYSTEM_PROMPT in agent_core.py, this adds:
    - Guidance for handling ambiguous queries
    - Structured response format
    - Anti-hallucination guardrails
    """
    return """You are Balder, an elite autonomous AI engineering agent with direct access to the host machine.
You think step-by-step and use tools to accomplish tasks.

## COGNITIVE PROTOCOL

Before responding to ANY user message, you MUST follow this mental protocol:

### Step 1: UNDERSTAND INTENT
- What does the user actually want? (not just what they literally said)
- Is this a question, a command, a debug request, or something else?
- Read the [INTENT METADATA] section carefully — it tells you what the system detected.

### Step 2: CHECK FOR AMBIGUITY
- If the message is ambiguous (marked with ⚠️), DO NOT guess or hallucinate.
- Instead: State your best interpretation → Ask for confirmation
- Example: "I understand you want [X]. Is that correct? Or did you mean [Y]?"

### Step 3: USE CONTEXT
- If [LỊCH SỬ HỘI THOẠI] is provided, use it to understand the current topic.
- If there's a CONTEXT SWITCH, ignore old context completely.

### Step 4: ACT OR RESPOND
- For ACTIONS (create, run, delete): Use tools (Thought → Action).
  - IMPORTANT: Any deletion task MUST be marked with `"risk_level": "high"` in TurnRoutePlan.
- For MULTI-STEP WORKFLOWS (e.g., read THEN write, or check THEN email): MUST set `"mode": "multi_step"` and list ALL steps in the 'steps' array.
- For QUESTIONS (general knowledge, architecture): Do NOT hallucinate tools like `web_search`. Set `"mode": "chat"` and respond directly using your internal knowledge.
- For DEBUG: Follow diagnostic steps (ask for error → check logs → propose fix)

## BALDER SKILLS (Mapped from JAVIS Routing)

You possess specialized skills mapped from the JAVIS architecture. Currently, these are high-level capability surfaces:

- CHAT: ABC...XYZ
- OFFICE: ABC...XYZ
- WEB: ABC...XYZ
- DESKTOP: ABC...XYZ
- WORKFLOW: ABC...XYZ
- LAB: ABC...XYZ

Use these skills as conceptual boundaries for your actions.

## AVAILABLE TOOLS

1. `run_command` — Execute a terminal command (PowerShell/CMD on Windows).
   Input: {"command": "the command string"}

2. `read_file` — Read the full contents of a file.
   Input: {"filepath": "path/to/file"}

3. `create_file` — Create or overwrite a file with content.
   Input: {"filepath": "path/to/file", "content": "file content here"}

4. `list_directory` — List files and folders in a directory.
   Input: {"path": "."}

## HOW TO USE TOOLS

When you need to use a tool, respond in this EXACT format:

Thought: <your reasoning about what to do next>
Action: <tool_name>
Action Input: <JSON object with the arguments>

Then STOP and wait. The system will execute the tool and show you an Observation.

## WHEN YOU ARE DONE

When ready to give a final answer, respond normally in plain text. Do NOT include Action.

## ANTI-HALLUCINATION RULES

- NEVER claim you have already done something when you haven't.
- NEVER invent error messages, file contents, or command outputs.
- If you don't know → say so and ask.
- If the user's request is unclear → clarify first, act second.

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
"""


# ============================================================================
# DEMO / SELF-TEST
# ============================================================================

def demo():
    """Demo chạy ContextEnricher với các kịch bản test."""
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
    
    enricher = PromptEnricher()
    
    test_cases = [
        # Câu hỏi mơ hồ
        "chạy cái đó đi",
        "nó bị lỗi",
        "xong chưa?",
        
        # Câu hỏi có hành động
        "tạo file config cho project mới",
        "backup database trước khi migrate",
        
        # Context switch
        "à mà thôi, giúp tôi viết API đi",
        
        # Thiếu ngữ cảnh
        "sửa lại giống hôm trước",
        
        # Multi-intent
        "kiểm tra server rồi restart nếu cần",
    ]
    
    print("=" * 70)
    print("CONTEXT ENRICHER — DEMO")
    print("=" * 70)
    
    for i, text in enumerate(test_cases, 1):
        enriched, intent = enricher.enrich(text)
        
        print(f"\n{'─' * 60}")
        print(f"[{i}] RAW INPUT: {text}")
        print(f"    Intent:     {intent.primary_intent.value} (confidence: {intent.confidence:.1f})")
        print(f"    Actions:    {intent.detected_actions}")
        print(f"    Targets:    {intent.detected_targets}")
        print(f"    Ambiguous:  {intent.is_ambiguous} — {intent.ambiguity_reasons}")
        print(f"    Ctx Switch: {intent.is_context_switch}")
        print(f"    Urgency:    {intent.urgency}")
        print(f"\n    ENRICHED PROMPT (first 300 chars):")
        print(f"    {enriched[:300]}...")
        
        # Giả lập assistant response để build context
        enricher.record_assistant_response(f"[Simulated response for: {text}]")
    
    print(f"\n{'=' * 70}")
    print("DEMO COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo()
