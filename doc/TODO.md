# 🧠 Financial Asset QA System (RAG + Multi-Router)

## 🎯 Project Goal
Build a full-stack financial QA system with:
- Asset price & trend analysis (via APIs)
- Financial knowledge QA (via RAG)
- Structured, factual, low-hallucination responses
- Multi-turn conversation support

---

# 🏗️ System Architecture

User Query
    ↓
[LLM Router / Classifier]
    ↓
 ┌───────────────┬───────────────┬───────────────┐
 │ Asset Query   │ Knowledge QA  │ General Chat  │
 └──────┬────────┴──────┬────────┴──────┬────────┘
        ↓               ↓               ↓
  Market Data API     RAG System      LLM Direct
        ↓               ↓               ↓
        └──────→ Response Generator ←──────┘
                         ↓
                      Output

---

# 📦 Tech Stack

## Backend
- FastAPI
- Python 3.10+

## LLM
- OpenAI / Claude / local LLM

## RAG
- LangChain or LlamaIndex
- Chroma / FAISS (vector DB)

## Market Data APIs
- yfinance (Yahoo Finance)
- Alpha Vantage
- SEC EDGAR
- baostock (China market)
- HKEX (Hong Kong)

## Frontend
- React / Next.js

---

# 🧩 Core Modules

---

## ✅ 1. Router (Intent Classification)

### Task
Classify user query into:
1. Asset-related
2. Financial knowledge
3. General / other

### Implementation

- Use LLM (NOT keyword matching)

### Prompt Design

You are a classifier. Classify the user query into one of: 
1. Asset pricing / market data
2. Financial knowledge
3. General / unrelated
Return only the number.


---

## ✅ 2. Asset Query Module (MARKET DATA)

### Task
Handle:
- Stock price
- Trend (7d, 30d)
- Price movement explanation
- Market news related to asset

---

### Data Sources

#### MUST implement at least one:
- yfinance (Python)
- Alpha Vantage (API key)

#### OPTIONAL (bonus):
- SEC EDGAR (US filings)
- cninfo (China filings)
- HKEX news

---

### Implementation Steps

#### Step 1: Fetch data

```python
import yfinance as yf

ticker = yf.Ticker("AAPL")
hist = ticker.history(period="7d")
```

#### Step 2: Compute metrics
latest price
percentage change
trend detection

#### Step 3: Trend classification
Uptrend
Downtrend
Sideways

#### Step 4: (Optional) News / event retrieval
earnings
macro news

#### Step 5: Structured Output
e.g.:
{
  "price": 180,
  "change_7d": "+3.2%",
  "trend": "uptrend"
}

#### Step 6: LLM Answer Generation
Prompt:
Based on the following market data:

{data}

Answer the user's question.

Requirements:
- Separate [FACTS] and [ANALYSIS]
- Do NOT hallucinate
- Use structured explanation

## ✅ 3. Knowledge QA (RAG)
### 📚 Data Source Strategy
You MUST build a small financial knowledge base.

Recommended sources:

Option A (fastest)
  -Wikipedia API
  -Investopedia articles

Option B (better)
  -SEC filings (10-K, 10-Q)
  -Financial textbooks (PDF)

### 🛠️ Pipeline
#### Step 1: Data collection
scrape or download text from the selected sources

#### Step 2: Chunking
```
chunk_size = 500
overlap = 100
```
#### Step 3: Embedding
OpenAI embeddings or similar

#### Step 4: Vector DB
Chroma or FAISS

#### Step 5: Retrieval
```
docs = retriever.get_relevant_documents(query)
```
#### Step 6: Answer Generation
Prompt:
Use ONLY the provided context to answer.

Context:
{docs}

Question:
{query}

Requirements:
- No hallucination
- Cite key facts
- Structured explanation

### ✅ 4. General Chat Module
If query is unrelated:
→ directly call LLM

Prompt:
Answer normally, but maintain professional tone.

### ✅ 5. Conversation Memory
Task
Maintain chat history per session
Implementation
chat_history = [
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
Rules
Append every turn
Reset when new session starts


### ✅ 6. Query Routing Logic
```
if type == 1:
    return asset_module(query)

elif type == 2:
    return rag_module(query)

else:
    return llm_direct(query)
```

### ✅ 7. API Layer (FastAPI)
Endpoints:
- POST /chat
input: user message
output: response
- POST /new_session
reset memory

### ✅ 8. Frontend
Features:
- Chat UI
- Session reset
- Streaming response (optional)

## ⚠️ Hallucination Control (VERY IMPORTANT)
MUST DO:
- Asset queries → ONLY from API
- Knowledge queries → ONLY from RAG
- NEVER allow free generation for facts

## 🧠 Prompt Engineering Summary
Router prompt
→ classification only

Asset prompt
→ facts + analysis separation

RAG prompt
→ strict grounding

## 🚀 Advanced (Optional Bonus)
- Tool calling / agent framework
- Multi-source aggregation
- Real-time news integration
- Chart visualization

## 📊 Deliverables
- GitHub repo
- README with:
architecture diagram
data sources
prompt design
system design
Demo video

## 🧪 Testing Cases
Asset:
- "阿里巴巴当前股价是多少？"
- "BABA最近7天涨跌情况如何？"
- "阿里巴巴最近为何1月15日大涨？"

Knowledge:
- "什么是市盈率？"
- "收入和净利润的区别是什么？"
- "某公司最近季度财报摘要是什么？"

General:
- "讲一个笑话"