import sys
import asyncio
from rich.console import Console

# Đảm bảo in được tiếng Việt trên console Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt
from agent_brain_v3 import BrainV3Agent

console = Console()


async def run_cli():
    # Tiêu đề giao diện
    console.print(Panel.fit(
        "[bold cyan]🤖 Balder Agent Hub — Text-based ReAct Engine[/]\n"
        "[dim]Powered by Gemma4 IQ3_XS | Local Ollama[/dim]",
        border_style="cyan"
    ))
    console.print("[dim]Hệ thống đang hoạt động. Gõ 'exit' để thoát.[/dim]\n")

    # Khởi tạo Agent
    agent = BrainV3Agent(verbose=True)

    while True:
        try:
            # Lấy lệnh từ người dùng
            user_input = Prompt.ask("\n[bold green]User (Giao lệnh)[/bold green]")
            if user_input.lower() in ['exit', 'quit']:
                console.print("[yellow]Bắt đầu đóng hệ thống... Tạm biệt![/]")
                break

            agent.add_user_message(user_input)

            # Khởi chạy ReAct loop
            with console.status(
                "[bold yellow]Agent đang suy nghĩ và hành động... (Ctrl+C để dừng)",
                spinner="dots"
            ):
                while True:
                    try:
                        result = await agent.run_step()

                        if result["type"] == "text":
                            # === Phản hồi cuối cùng (không có Action) ===
                            console.print(Panel(
                                Markdown(result["content"]),
                                title="[bold blue]Agent[/bold blue]",
                                border_style="blue"
                            ))
                            break

                        elif result["type"] == "tool_call":
                            # === LLM đang gọi tool ===
                            thought = result.get("thought", "")
                            if thought:
                                console.print(f"  [dim italic]💭 {thought}[/]")

                            console.print(
                                f"[bold magenta]🛠 Action:[/] {result['action']}"
                            )
                            console.print(
                                f"  [cyan]Input:[/] {result['action_input']}"
                            )

                            # Cắt bớt observation dài
                            obs = str(result['observation'])
                            if len(obs) > 500:
                                obs = obs[:500] + "... (truncated)"
                            console.print(f"  [yellow]Observation:[/] {obs}")
                            # Tiếp tục vòng lặp — chờ LLM reasoning tiếp

                        elif result["type"] == "parse_error":
                            # === JSON parse thất bại, LLM sẽ tự retry ===
                            console.print(
                                f"[bold yellow]⚠ Parse Error:[/] {result['content']}"
                            )
                            # Tiếp tục vòng lặp — LLM sẽ thấy lỗi và sửa format

                        elif result["type"] == "max_steps":
                            console.print(Panel(
                                f"[bold yellow]⚠ {result['content']}[/]",
                                border_style="yellow"
                            ))
                            break

                        elif result["type"] == "cancelled":
                            console.print(
                                "[red]Hành động đã bị hủy bởi người dùng.[/]"
                            )
                            break

                        elif result["type"] == "error":
                            console.print(Panel(
                                f"[bold red]Lỗi hệ thống:[/] {result['content']}",
                                border_style="red"
                            ))
                            break

                    except asyncio.CancelledError:
                        console.print("[red]Đã ngắt quá trình suy nghĩ.[/]")
                        break

        except KeyboardInterrupt:
            console.print("\n[yellow]Hành động bị ngắt (Ctrl+C).[/]")
            continue
        except Exception as e:
            console.print(f"[red]Lỗi không mong muốn: {e}[/]")
            break


if __name__ == "__main__":
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        pass
