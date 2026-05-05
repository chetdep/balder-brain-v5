import re
import os
import subprocess
from typing import Dict, Any, Tuple

class SafetyGate:
    """
    Hệ thống kiểm soát an toàn câu lệnh cho Balder.
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
        """Kiểm tra câu lệnh dựa trên Whitelist (Chỉ cho phép những gì được liệt kê)."""
        command_lower = command.lower().strip()
        base_cmd = command_lower.split()[0] if command_lower else ""
        
        # 1. Check Whitelist
        if base_cmd not in cls.WHITELIST_COMMANDS:
            return False, f"Lỗi Policy: Lệnh '{base_cmd}' không nằm trong danh sách được phép (Whitelist). Hãy dùng các tool chuyên dụng hoặc yêu cầu cấp quyền."

        # 2. Check for dangerous patterns even in whitelisted commands
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, command_lower):
                return False, f"Lỗi An Toàn: Phát hiện mẫu nguy hiểm trong lệnh: '{pattern}'."

        return True, "Safe"

    @classmethod
    def filesystem_delete(cls, path: str, recursive: bool = False, reason: str = "") -> str:
        """
        Layer 3: Tool Wrapper cho hành động xóa.
        Thực hiện xóa an toàn thông qua PowerShell.
        """
        if not path or path in [".", "/", "C:\\", "C:/"]:
            return "Lỗi: Không được phép xóa thư mục gốc hoặc đường dẫn trống."
            
        if not reason:
            return "Lỗi: Bạn phải cung cấp lý do (reason) cho hành động xóa này."
            
        # Chuẩn bị lệnh PowerShell
        ps_cmd = f"Remove-Item -Path \"{path}\" -Force"
        if recursive:
            ps_cmd += " -Recurse"
            
        try:
            # Thực thi lệnh
            result = subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, text=True)
            if result.returncode == 0:
                return f"Thành công: Đã xóa '{path}'. Lý do: {reason}"
            else:
                return f"Lỗi thực thi: {result.stderr}"
        except Exception as e:
            return f"Lỗi hệ thống: {str(e)}"

class ExecutionGate:
    """
    Cửa chặn thực thi dựa trên rủi ro (Risk-based Execution Gate).
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
        """Xác định mức độ rủi ro của một hành động."""
        risk = cls.POLICY.get(action, "unknown")
        
        # Đặc biệt cho run_command, kiểm tra sâu hơn
        if action == "run_command":
            cmd = args.get("command", "").lower()
            if any(p in cmd for p in ["powershell", "shell", "invoke"]):
                risk = "high"
            elif any(p in cmd for p in ["git", "ls", "dir"]):
                risk = "low"
            else:
                risk = "medium"
                
        return risk
