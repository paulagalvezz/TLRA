
# 🐷 OpenAI Function Calling Demo: The Three Little Pigs 🐺

A didactic Python script that demonstrates the difference between a regular LLM conversation and one with **function calling** capabilities, using the classic Three Little Pigs tale.

## 📚 What You'll Learn

- How to define **tools/functions** for the OpenAI API
- How the LLM **decides when** to call functions based on context
- How to **process function calls** and feed results back to the LLM
- The difference between an LLM that can only talk vs. one that can **take action**

## 🎭 The Demo

The script runs two scenarios with the same conversation:

| Scenario | Tools Available | What Happens |
|----------|----------------|--------------|
| **Scenario 1** | ❌ None | The pig can only *talk* and react to the wolf |
| **Scenario 2** | ✅ `call_elder_brother()` | The pig can *actually* call the elder brother! |

### The Conversation

1. **User:** "knock knock..."
2. **Pig:** *(cautiously asks who's there)*
3. **User:** "I am the wolf! Open the door or I will blow away your house!"
4. **Pig:** *(In Scenario 2, calls his elder brother for help!)*

## 🚀 Setup & Run

### Prerequisites

- Python 3.10 or higher
- [Ollama](https://ollama.com/) installed with `qwen3:8b` downloaded
- `uv` package manager ([install uv](https://docs.astral.sh/uv/getting-started/installation/))

### Step 1: Create and activate the virtual environment

```bash
cd week3/straw-house
uv venv
source .venv/bin/activate  # On macOS/Linux
# or
.venv\Scripts\activate     # On Windows
```

### Step 2: Install dependencies

```bash
uv sync
```

### Step 3: Configure your API key

Create a `.env` file in this folder:

```bash
#whatever, it will not be used
OPENAI_API_KEY=your-api-key-here

# Optional: choose model (default: qwen3:8b)
MODEL=qwen3:8b

# Optional: use alternative OpenAI-compatible API endpoint
# OPENAI_ENDPOINT=http://172.22.240.1:11434/v1  # Ollama
```

> ⚠️ **Never commit your `.env` file!** It's already in `.gitignore`.

### Step 4: Run the demo

```bash
uv run three_pigs_function_calling.py
```

## 🔧 How Function Calling Works

### 1. Define the Tool

```python
AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_elder_brother",
            "description": "Call the eldest brother to come help protect the pig from the wolf. He lives in the sturdy brick house, making it the safest shelter, and he is known for being brave and always hel>
            "parameters": {
                "type": "object",
                "properties": {
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "emergency"],
                        "description": "How urgent the wolf threat is."
                    },
                    "message": {
                        "type": "string",
                        "description": "A brief message explaining the danger and asking the eldest brother to help."
                    }
                },
                "required": ["urgency", "message"]
            }
        }
    }
]
```

### 2. Pass Tools to the API

```python
response = client.chat.completions.create(
    model=MODEL,
    messages=messages,
    tools=AVAILABLE_TOOLS,
    temperature=0.7
)
```

### 3. Check if the LLM Wants to Call a Function

```python
if response.choices[0].message.tool_calls:
    for tool_call in response.choices[0].message.tool_calls:
        function_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)
        # Execute the function and return results to the LLM
```

## 💡 Key Takeaway

> **Function calling transforms LLMs from chatbots into agents that can take real-world actions!**

Without function calling, the pig can only *say* "I'll call the hunter." With function calling, the pig can *actually* call the hunter and get a response.

## 📖 Further Reading

- [OpenAI Function Calling Documentation](https://platform.openai.com/docs/guides/function-calling)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)

## 📖 License

This repository is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](../../../LICENSE) (`CC BY-NC-SA 4.0`).

## 👤 Author

[@granludo](https://github.com/granludo) - Marc Alier



---

© 2026 **Marc Alier i Forment** (Universitat Politècnica de Catalunya) · <https://wasabi.essi.upc.edu/ludo> · <https://lamb-project.org>
BSC Agents Course — *Transformers, LLMs, RAG and Agents: From Theory to Production*.
Licensed under [Creative Commons BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): reuse must credit the author, no commercial use, derivatives under the same license.
