import asyncio
from openai import AsyncOpenAI

async def main():
    client = AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    print("Sending request to MoE...")
    try:
        resp = await client.chat.completions.create(
            model="gemma4:26b-moe-iq3xs",
            messages=[{"role": "user", "content": "Say 'hello'"}],
            timeout=60
        )
        print(f"Response: {resp.choices[0].message.content}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
