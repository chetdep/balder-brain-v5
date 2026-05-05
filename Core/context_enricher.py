"""
==========================================================================
CONTEXT ENRICHER — Module bổ sung ngữ cảnh cho LLM
==========================================================================
Mục đích:
  Giải quyết vấn đề LLM (đặc biệt model quantize thấp) không hiểu được
  câu hỏi ngắn, mơ hồ, thiếu ngữ cảnh.

Kiến trúc: 3 TẦNG LỌC

  Tầng 1: Intent Classifier (Local, rule-based)
    → Phân loại nhanh ý định: action / question / debug / context_switch
    → Không cần gọi LLM, xử lý bằng regex + keyword matching

  Tầng 2: Context Window Manager
    → Quản lý sliding window của conversation history
    → Tự động detect context switch ("à mà thôi", "quên cái trên đi")
    → Inject relevant context vào prompt

  Tầng 3: Prompt Enricher
    → Biến câu hỏi ngắn/mơ hồ thành prompt đầy đủ cho LLM
    → Thêm metadata: intent, context, constraints

Tích hợp vào agent_core.py:
  user_input → ContextEnricher.enrich() → enriched_prompt → LLM

Chạy test: python context_enricher.py
==========================================================================
"""

import re
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


# ============================================================================
# TẦNG 1: INTENT CLASSIFIER (Rule-based, không cần LLM)
# ============================================================================

class IntentType(Enum):
    """Phân loại ý định người dùng."""
    ACTION = "action"              # Yêu cầu thực hiện hành động (tạo, chạy, deploy...)
    QUESTION = "question"          # Hỏi thông tin / kiến thức
    DEBUG = "debug"                # Debug / sửa lỗi
    STATUS_CHECK = "status_check"  # Kiểm tra trạng thái
    CONTEXT_SWITCH = "context_switch"  # Chuyển đề tài
    CONFIRMATION = "confirmation"  # Xác nhận / đồng ý
    AMBIGUOUS = "ambiguous"        # Không xác định được
    MULTI_ACTION = "multi_action"  # Chuỗi hành động phức tạp



@dataclass
class ActionStep:
    """Biểu diễn một bước trong chuỗi hành động."""
    step_id: int
    action: str
    target: Optional[str] = None
    condition: Optional[str] = None
    depends_on: Optional[int] = None  # ID của bước trước đó

@dataclass
class ActionPipeline:
    """Chuỗi các hành động cần thực hiện."""
    steps: List[ActionStep] = field(default_factory=list)
    total_steps: int = 0
    is_conditional: bool = False

@dataclass
class IntentAnalysis:
    """Kết quả phân tích ý định (Phân tầng theo JAVIS)."""
    primary_intent: IntentType
    intent_class: str              # Tầng 1: talk / action / workflow / status / debug
    capability_group: str          # Tầng 2: office.email, web.search, etc.
    endpoint: Optional[str]        # Tầng 3: email.send, web.news, etc.
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
        "office.drive": ["drive", "lưu trữ", "upload", "download"],
        "web.search": ["tìm", "tra cứu", "tin tức", "news", "google"],
        "web.read": ["đọc", "xem", "nội dung", "mở trang"],
        "desktop.system": ["chạy", "cmd", "powershell", "hệ thống"],
        "self_model.trace": ["kiến trúc", "module", "nằm ở đâu", "cấu trúc code"],
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
        """Phân tích chuỗi hành động từ text."""
        if len(actions) < 2 and not any(re.search(p, text) for p in self.PIPELINE_CONJUNCTIONS["sequential"] + self.PIPELINE_CONJUNCTIONS["conditional"]):
            return None
            
        steps = []
        is_conditional = False
        
        # Heuristic phân tách các bước dựa trên keyword "rồi", "sau đó", "nếu"
        # Chia nhỏ text thành các phần
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
            
            # Tìm action trong part này
            step_action = "unknown"
            for act, kws in self.ACTION_KEYWORDS.items():
                if any(kw in part for kw in kws):
                    step_action = act
                    break
            
            # Tìm target trong part này
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
        """Phân loại ý định từ text input."""
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
            # Kiểm tra pipeline trước khi gán ACTION đơn thuần
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
# TẦNG 2: CONTEXT WINDOW MANAGER
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
# TẦNG 3: PROMPT ENRICHER — Biến câu hỏi mơ hồ → prompt đầy đủ
# ============================================================================

class PromptEnricher:
    """
    Biến câu hỏi ngắn/mơ hồ thành prompt có đầy đủ ngữ cảnh cho LLM.
    
    Luồng xử lý:
    1. Nhận raw input + IntentAnalysis + context
    2. Xây dựng enriched prompt với:
       - Intent metadata
       - Relevant context từ history
       - Hướng dẫn cụ thể cho LLM dựa trên intent type
       - Constraint / guardrail
    """
    
    # Template cho từng loại intent
    INTENT_TEMPLATES = {
        IntentType.ACTION: (
            "## YÊU CẦU HÀNH ĐỘNG\n"
            "User yêu cầu thực hiện hành động.\n"
            "- Hành động phát hiện: {actions}\n"
            "- Đối tượng: {targets}\n"
            "- Mức khẩn cấp: {urgency}\n\n"
            "HƯỚNG DẪN: Hãy phân tích yêu cầu, lập kế hoạch cụ thể và thực hiện từng bước.\n"
            "Nếu thiếu thông tin, hãy HỎI LẠI trước khi thực hiện.\n"
        ),
        IntentType.DEBUG: (
            "## YÊU CẦU DEBUG / SỬA LỖI\n"
            "User đang gặp vấn đề và cần hỗ trợ debug.\n\n"
            "HƯỚNG DẪN:\n"
            "1. Hỏi hoặc xác định: lỗi gì? ở đâu? error message?\n"
            "2. Đề xuất các bước chẩn đoán (kiểm tra log, chạy lại, v.v.)\n"
            "3. Nếu có đủ thông tin → đề xuất fix cụ thể\n"
        ),
        IntentType.STATUS_CHECK: (
            "## KIỂM TRA TRẠNG THÁI\n"
            "User muốn biết trạng thái của một tác vụ.\n\n"
            "HƯỚNG DẪN: Kiểm tra context gần nhất để xác định user đang hỏi về cái gì.\n"
            "Nếu không có context → hỏi lại cụ thể.\n"
        ),
        IntentType.QUESTION: (
            "## CÂU HỎI THÔNG TIN\n"
            "User đang hỏi về kiến thức hoặc thông tin.\n\n"
            "HƯỚNG DẪN: Trả lời chính xác, có cấu trúc, kèm ví dụ nếu có thể.\n"
        ),
        IntentType.CONTEXT_SWITCH: (
            "## CHUYỂN ĐỀ TÀI\n"
            "User muốn chuyển sang topic mới. BỎ QUA context cũ.\n\n"
            "HƯỚNG DẪN: Tập trung 100% vào yêu cầu mới của user.\n"
            "KHÔNG kéo thông tin từ topic cũ vào.\n"
        ),
        IntentType.AMBIGUOUS: (
            "## CÂU HỎI MƠ HỒ\n"
            "Câu hỏi của user không đủ rõ ràng.\n"
            "- Lý do mơ hồ: {ambiguity_reasons}\n\n"
            "HƯỚNG DẪN:\n"
            "1. Liệt kê các cách hiểu có thể\n"
            "2. Chọn cách hiểu hợp lý nhất dựa trên context (nếu có)\n"
            "3. Trả lời theo cách hiểu đó, NHƯNG hỏi lại để xác nhận\n"
            "4. KHÔNG được bịa ra hành động hoặc kết quả\n"
        ),
        IntentType.CONFIRMATION: (
            "## XÁC NHẬN\n"
            "User đang xác nhận hoặc đồng ý với đề xuất trước đó.\n\n"
            "HƯỚNG DẪN: Thực hiện hành động đã được đề xuất trong context.\n"
        ),
        IntentType.MULTI_ACTION: (
            "## CHUỖI HÀNH ĐỘNG PHỨC TẠP (PIPELINE)\n"
            "User yêu cầu một chuỗi các hành động có liên quan đến nhau.\n"
            "- Tổng số bước: {total_steps}\n"
            "- Chi tiết pipeline: \n{pipeline_details}\n\n"
            "HƯỚNG DẪN:\n"
            "1. Thực hiện TUẦN TỰ các bước đã phân tích.\n"
            "2. Kiểm tra điều kiện (nếu có) trước mỗi bước.\n"
            "3. Observation của bước trước phải được dùng để quyết định cho bước sau.\n"
            "4. Báo cáo tiến độ sau mỗi bước quan trọng.\n"
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
        Chuyển đổi raw input thành enriched prompt.
        
        Args:
            raw_input: Câu hỏi gốc của user
            extra_context: Context bổ sung (active file, project info, v.v.)
        
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
        """Ghi lại response của assistant vào history."""
        self.context_manager.add_turn("assistant", response)
    
    def _build_enriched_prompt(
        self,
        raw_input: str,
        intent: IntentAnalysis,
        history_context: str,
        extra_context: Optional[Dict]
    ) -> str:
        """Xây dựng prompt enriched từ tất cả thông tin."""
        
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
                cond = f" (Điều kiện: {s.condition})" if s.condition else ""
                dep = f" (Phụ thuộc bước: {s.depends_on})" if s.depends_on else ""
                pipeline_details += f"  Step {s.step_id}: {s.action} trên {s.target or 'N/A'}{cond}{dep}\n"

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
        
        # ── Phần 3: Extra context (project, active file, etc.) ──
        if extra_context:
            ctx_lines = ["## NGỮ CẢNH BỔ SUNG"]
            for key, value in extra_context.items():
                ctx_lines.append(f"- {key}: {value}")
            parts.append("\n".join(ctx_lines) + "\n")
        
        # ── Phần 4: Câu hỏi gốc của user ──
        parts.append(f"## CÂU HỎI CỦA USER\n{raw_input}")
        
        # ── Phần 5: Constraint / Guardrail ──
        if intent.is_ambiguous:
            parts.append(
                "\n⚠️ LƯU Ý: Câu hỏi này MƠ HỒ. "
                "Hãy nêu rõ bạn hiểu câu hỏi như thế nào và hỏi lại nếu cần."
            )
        
        return "\n".join(parts)


# ============================================================================
# ENRICHED AGENT — Tích hợp ContextEnricher vào ReAct Agent
# ============================================================================

def create_enriched_system_prompt() -> str:
    """
    System prompt nâng cao cho agent có context enrichment.
    So với SYSTEM_PROMPT gốc trong agent_core.py, bổ sung thêm:
    - Hướng dẫn xử lý câu hỏi mơ hồ
    - Format trả lời có cấu trúc
    - Guardrails chống hallucination
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
- Example: "Tôi hiểu bạn muốn [X]. Đúng không? Hay bạn muốn nói [Y]?"

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
TRẢ LỜI: <your response>
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
