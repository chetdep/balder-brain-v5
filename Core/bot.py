import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from agent_brain_v3 import BrainV3Agent
from dotenv import load_dotenv

load_dotenv()

# Dictionary để lưu instance agent cho từng user (để giữ context riêng)
user_agents = {}

async def get_agent(user_id: int) -> BrainV3Agent:
    if user_id not in user_agents:
        user_agents[user_id] = BrainV3Agent(verbose=True)
    return user_agents[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Chào mừng! Tôi là Balder V3 (Hyper-Reasoning Agent).\n"
        "Tôi có thể giúp bạn lập trình, quản lý file và suy luận logic phức tạp."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_input = update.message.text
    
    agent = await get_agent(user_id)
    agent.add_user_message(user_input)
    
    # Gửi thông báo đang suy nghĩ
    thinking_msg = await update.message.reply_text("🤔 Balder đang suy luận...")

    try:
        while True:
            result = await agent.run_step()
            
            if result["type"] == "tool_call":
                # Cập nhật trạng thái đang gọi tool
                status_text = f"🛠️ Đang thực hiện: {result['action']}\n💭 {result['thought']}"
                await thinking_msg.edit_text(status_text)
            
            elif result["type"] == "text":
                # Kết quả cuối cùng
                await thinking_msg.delete()
                await update.message.reply_text(result["content"], parse_mode='Markdown')
                break
                
            elif result["type"] in ["error", "max_steps", "cancelled"]:
                await thinking_msg.edit_text(f"❌ Lỗi: {result['content']}")
                break
                
    except Exception as e:
        await update.message.reply_text(f"💥 Đã xảy ra lỗi hệ thống: {str(e)}")

if __name__ == '__main__':
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in environment variables.")
    else:
        application = ApplicationBuilder().token(token).build()
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("Balder V3 Bot is running...")
        application.run_polling()
