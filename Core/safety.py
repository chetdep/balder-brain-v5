import re
import os
import subprocess
from typing import Dict, Any, Tuple

class SafetyGate:
    """
    Safety control system for Balder commands.
    Layer 2: Command Safety Gate
    Layer 3: Tool Wrapper
    """
    
    # Layer 2: Whitelist Policy
    WHITELIST_COMMANDS = [
        "git", "python", "npm", "pip", "dir", "ls", "cd", 
        "echo", "cat", "mkdir", "type", "Get-ChildItem", 
        "Get-Location", "Get-Content"
    ]
    
    DANGEROUS_PATTERNS = [
        r"format\s+[a-zA-Z]:",
        r"del\s+.*\/s",
        r"rd\s+.*\/s",
        r">/dev/null",
        r"\|", r">", r"&", r";",  # No piping/redirection in raw run_command
        # PowerShell bypass patterns
        r"invoke-expression",
        r"iex\s*[\.\(]",
        r"start-process",
        r"-encodedcommand",
        r"invoke-webrequest",
        r"downloadstring",
        r"downloadfile",
        r"new-object\s+net\.webclient",
        r"set-executionpolicy",
        r"bypass",
        # Dangerous system tools
        r"\bdiskpart\b",
        r"\bcertutil\b",
        r"\bbitsadmin\b",
        r"\bwmic\b",
        r"\bnet\s+user\b",
        r"\bnet\s+localgroup\b",
        r"\breg\s+(?:add|delete)\b",
        r"\btakeown\b",
        r"\bicacls\b",
        # Unix dangerous commands (should never appear on Windows)
        r"\brm\s+-rf\b",
        r"\bchmod\b",
        r"\bsudo\b",
        r"\bdd\s+if=",
        r"\bmkfs\b",
    ]

    @classmethod
    def validate_command(cls, command: str) -> Tuple[bool, str]:
        """Check the command against the Whitelist (only allow listed commands)."""
        command_lower = command.lower().strip()
        base_cmd = command_lower.split()[0] if command_lower else ""
        
        # 1. Check Whitelist
        if base_cmd not in cls.WHITELIST_COMMANDS:
            return False, f"Policy Error: Command '{base_cmd}' is not in the allowed list (Whitelist). Use specialized tools or request permissions."

        # 2. Check for dangerous patterns even in whitelisted commands
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, command_lower):
                return False, f"Safety Error: Dangerous pattern detected in command: '{pattern}'."

        return True, "Safe"

    @classmethod
    def filesystem_delete(cls, path: str, recursive: bool = False, reason: str = "") -> str:
        """
        Layer 3: Tool Wrapper for deletion actions.
        Performs secure deletion via PowerShell.
        """
        if not path or path in [".", "/", "C:\\", "C:/"]:
            return "Error: Cannot delete root directory or empty path."
            
        if not reason:
            return "Error: You must provide a reason for this deletion action."
            
        # Prepare PowerShell command
        ps_cmd = f"Remove-Item -Path \"{path}\" -Force"
        if recursive:
            ps_cmd += " -Recurse"
            
        try:
            # Execute command
            result = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True)
            if result.returncode == 0:
                return f"Success: Deleted '{path}'. Reason: {reason}"
            else:
                return f"Execution Error: {result.stderr}"
        except Exception as e:
            return f"System Error: {str(e)}"

class ExecutionGate:
    """
    Risk-based Execution Gate.
    """
    
    POLICY = {
        "git": "low",
        "python": "medium",
        "npm": "medium",
        "pip": "medium",
        "powershell": "high",
        "filesystem_delete": "high",
        "create_file": "medium"
    }

    @classmethod
    def check_risk(cls, action: str, args: Dict[str, Any]) -> str:
        """Determine the risk level of an action."""
        risk = cls.POLICY.get(action, "unknown")
        
        # Special handling for run_command, check deeper
        if action == "run_command":
            cmd = args.get("command", "").lower()
            if any(p in cmd for p in ["powershell", "shell", "invoke"]):
                risk = "high"
            elif any(p in cmd for p in ["git", "ls", "dir"]):
                risk = "low"
            else:
                risk = "medium"
                
        return risk
