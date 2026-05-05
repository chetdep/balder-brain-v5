"""
==========================================================================
BALDER HIERARCHICAL SKILLS — Mapping from JAVIS 3-Layer Architecture
==========================================================================
Kiến trúc 3 tầng:
  1. Intent Class (Lớp Ý định)
  2. Capability Group (Nhóm Khả năng - Surface Layers)
  3. Concrete Endpoint (Điểm cuối thực thi)
==========================================================================
"""

from enum import Enum
from typing import Dict, List, Any

# --- TẦNG 1: INTENT CLASS ---
class IntentClass(Enum):
    TALK = "TALK_ABOUT_DOMAIN"
    ACTION = "PERFORM_ACTION"
    WORKFLOW = "MULTI_STEP_ACTION"
    STATUS = "ASK_FOR_STATUS"
    DEBUG = "DEBUG_FAILURE"
    RESEARCH = "KNOWLEDGE_LOOKUP"

# --- TẦNG 2: CAPABILITY GROUP (Surfaces & Layers) ---
class CapabilityGroup(Enum):
    # Office Layers
    OFFICE_EMAIL = "office.email"
    OFFICE_DRIVE = "office.drive"
    OFFICE_DOC = "office.document"
    
    # Web Layers
    WEB_READ = "web.read"
    WEB_SEARCH = "web.search"
    WEB_INTERACT = "web.interact"
    WEB_WORKFLOW = "web.workflow"
    
    # Desktop Layers
    DESKTOP_UI = "desktop.ui"
    DESKTOP_SYSTEM = "desktop.system"
    
    # Lab & Self Layers
    LAB_RESEARCH = "lab.research"
    SELF_MODEL = "self_model.trace"
    
    # General Chat
    CHAT_GENERAL = "chat.general"

# --- TẦNG 3: CONCRETE ENDPOINTS (Placeholders) ---
SKILL_PLACEHOLDER = "ABC...XYZ"

BALDER_SKILLS_MAPPING = {
    CapabilityGroup.OFFICE_EMAIL: {
        "stats": SKILL_PLACEHOLDER,
        "read": SKILL_PLACEHOLDER,
        "send": SKILL_PLACEHOLDER
    },
    CapabilityGroup.OFFICE_DRIVE: {
        "list": SKILL_PLACEHOLDER,
        "upload": SKILL_PLACEHOLDER,
        "search": SKILL_PLACEHOLDER
    },
    CapabilityGroup.WEB_READ: {
        "current_page": SKILL_PLACEHOLDER,
        "extract_main": SKILL_PLACEHOLDER
    },
    CapabilityGroup.WEB_SEARCH: {
        "news": SKILL_PLACEHOLDER,
        "general": SKILL_PLACEHOLDER
    },
    CapabilityGroup.DESKTOP_SYSTEM: {
        "run_cmd": SKILL_PLACEHOLDER,
        "check_status": SKILL_PLACEHOLDER
    },
    CapabilityGroup.LAB_RESEARCH: {
        "experiment": SKILL_PLACEHOLDER,
        "optimize": SKILL_PLACEHOLDER
    }
}

def get_hierarchical_skill(capability: str, endpoint: str = None) -> str:
    """
    Truy xuất skill dựa trên cấu trúc phân tầng.
    """
    try:
        cap_enum = CapabilityGroup(capability)
        cap_data = BALDER_SKILLS_MAPPING.get(cap_enum, {})
        if endpoint:
            return cap_data.get(endpoint, SKILL_PLACEHOLDER)
        return SKILL_PLACEHOLDER
    except ValueError:
        return "UNKNOWN_CAPABILITY"
