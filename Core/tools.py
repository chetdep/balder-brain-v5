import os
import json
import subprocess
from Core.safety import SafetyGate

def create_outcome(status: str, reason: str, retryable: bool = False, artifacts: list = None):
    """Create a standard JARVIS HandlerOutcome."""
    return json.dumps({
        "status": status,
        "reason": reason,
        "retryable": retryable,
        "artifacts": artifacts or []
    }, ensure_ascii=False)

def execute_command(command: str):
    """Layer 1: Command Executor with Whitelist Policy."""
    is_safe, msg = SafetyGate.validate_command(command)
    if not is_safe:
        return create_outcome("blocked", msg)
        
    try:
        # Simulate or execute for real depending on environment
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return create_outcome("handled", result.stdout)
        else:
            return create_outcome("fatal", result.stderr)
    except Exception as e:
        return create_outcome("fatal", str(e))

def read_file(filepath: str):
    """Safe file reading."""
    try:
        if not os.path.exists(filepath):
            return create_outcome("retryable_miss", "File does not exist.")
        with open(filepath, 'r', encoding='utf-8') as f:
            return create_outcome("handled", f.read())
    except Exception as e:
        return create_outcome("fatal", str(e))

def create_file(filepath: str = None, content: str = None, TargetFile: str = None, CodeContent: str = None, **kwargs):
    """Create a new file or overwrite an existing one (Supports JARVIS and Coder Agent standards)."""
    try:
        final_path = filepath or TargetFile
        final_content = content or CodeContent
        
        if not final_path:
            return create_outcome("fatal", "Missing file path (filepath or TargetFile).")
            
        dir_path = os.path.dirname(final_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
            
        with open(final_path, 'w', encoding='utf-8') as f:
            f.write(final_content or "")
        return create_outcome("handled", f"Created file {final_path}", artifacts=[final_path])
    except Exception as e:
        return create_outcome("fatal", str(e))

def list_directory(path: str = "."):
    """List directory contents."""
    try:
        items = os.listdir(path)
        return create_outcome("handled", f"List: {items}")
    except Exception as e:
        return create_outcome("fatal", str(e))

def filesystem_delete(path: str, recursive: bool = False, reason: str = ""):
    """Layer 3: Safety Wrapper cho deletions."""
    result_msg = SafetyGate.filesystem_delete(path, recursive, reason)
    if "Success" in result_msg:
        return create_outcome("handled", result_msg)
    else:
        return create_outcome("blocked", result_msg)

from Core.audit_tools import self_audit

AVAILABLE_TOOLS = {
    "run_command": execute_command,
    "read_file": read_file,
    "create_file": create_file,
    "write_to_file": create_file,
    "list_directory": list_directory,
    "filesystem_delete": filesystem_delete,
    "self_audit": self_audit
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a terminal/shell command. Used to create repositories, install dependencies, compile code, run git commands, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command line string to execute."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file to analyze code or errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file."}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file or overwrite an existing file with the specified content. Ensure paths are relative to current working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Path to the file to create."},
                    "content": {"type": "string", "description": "The complete source code/text content to write."}
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory to see available files or projects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to list context of. Use '.' for current directory."}
                },
                "required": ["path"]
            }
        }
    }
]
