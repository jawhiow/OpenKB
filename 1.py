import requests
import json

# ============ 配置区 ============
API_URL = "https://windhub.cc/v1/chat/completions"
API_KEY = "sk-vjmQUylkMLn8RLx07Zggo9wJWDO9a5i14pxRKd9eYKj2IV0z"          # ← 替换为你的 API Key
MODEL = "doubao-seed-2-0-pro-260215"
# ================================

def chat(messages: list, stream: bool = True) -> str:
    """调用 OpenAI 兼容接口，支持流式/非流式输出"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": stream,
    }

    response = requests.post(API_URL, headers=headers, json=payload, stream=stream)
    response.raise_for_status()

    if not stream:
        data = response.json()
        return data["choices"][0]["message"]["content"]

    # 流式输出
    full_content = ""
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data_str = line[len("data:"):].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk["choices"][0].get("delta", {})
            token = delta.get("content", "")
            if token:
                print(token, end="", flush=True)
                full_content += token
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
    print()  # 换行
    return full_content


def main():
    print(f"模型: {MODEL}")
    print("输入消息开始对话，输入 /quit 退出，输入 /clear 清空上下文\n")

    messages = []

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input == "/quit":
            print("再见！")
            break
        if user_input == "/clear":
            messages.clear()
            print("上下文已清空。\n")
            continue

        messages.append({"role": "user", "content": user_input})

        print("AI: ", end="", flush=True)
        try:
            reply = chat(messages, stream=True)
        except requests.exceptions.HTTPError as e:
            print(f"\n[HTTP 错误] {e}")
            print(f"响应内容: {e.response.text}")
            messages.pop()  # 移除失败的用户消息
            continue
        except Exception as e:
            print(f"\n[错误] {e}")
            messages.pop()
            continue

        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()