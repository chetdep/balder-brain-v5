# Research Report: Enhancing Intent Recognition and Reducing Behavioral Hallucination in Small LLMs (Gemma 4 Series)

## 1. Executive Summary

During experiments with locally-hosted LLMs, specifically small models from the Gemma 4 family (2B, 4B, and 26B), I observed a clear pattern: while small models handle simple, linear tasks effectively, they struggle with natural language nuances, ambiguous requests, or multi-step workflows.

The issue is not just traditional "hallucination" (factual errors). In the context of routers, workflows, and agents, a more critical problem emerges: **Behavioral Hallucination**. This occurs when the model misinterprets user intent, selects the wrong processing path, triggers incorrect actions, or executes an action when the user was merely inquiring about it.

Initially, I relied on standard techniques suggested by Gemini, such as semantic routing, cosine similarity, attention mechanisms, chain probability, Bayesian inference, and graph reasoning. However, individual application of these methods proved insufficient for small models facing complex commands or safety-critical scenarios.

To address this, I implemented a multi-layered architecture combining:
- **Capsule Text**: For context compression and normalization.
- **Three-Tier Inference Architecture**: To separate fast recognition from deep reasoning.
- **Scoring/Routing Algorithms**: To move beyond simple "top-1" selection.
- **Deep Reasoning Workflows**: To enforce structured thinking.
- **Ambiguity Ask-Back Mechanism**: To prevent guessing.
- **Safety Gates**: To validate actions before execution.

Preliminary results are promising. Across 40 test cases in 8 scenarios, version **V5 (Gemma 4 4B with advanced architecture)** achieved an average score of **93.8%**, outperforming other variants. This suggests that for intent recognition and multi-step workflows, model size is not the sole determinant; the orchestration of the reasoning flow and behavioral control play a decisive role.

---

## 2. Background and Motivation

The motivation stems from a practical need: running LLMs locally on limited hardware while maintaining high-quality natural language understanding. While cloud-based large models are powerful, they introduce costs, latency, privacy concerns, and external dependencies.

For local small LLMs, the core challenge is correctly identifying user intent to trigger the right workflow. A single misinterpretation leads to behavioral errors: wrong tool calls, missed confirmations, or unauthorized executions. 

My initial observations showed that small models perform well in "straight-line" tasks:
1. User provides a clear command.
2. Model identifies the action.
3. System executes.

However, when users speak naturally—combining ideas, asking indirectly, using conditionals, or providing incomplete data—small models often falter. This research focuses on improving these models through **orchestration architecture** rather than simply increasing parameter count.

---

## 3. Why Gemma 4 (2B, 4B, 26B)?

I chose the Gemma 4 family to minimize noise in comparison. Using different model families would introduce variables like tokenizers, instruction-following styles, and training data differences. By staying within one family, I could focus on the primary question: **How does model size impact effectiveness when using the same architectural improvements?**

### 3.1. Gemma 4 2B
Represents ultra-small models capable of running on consumer hardware or resource-constrained environments. If these can be improved, it drastically lowers the barrier for offline AI deployment. However, 2B models are the most prone to losing context and failing at complex reasoning.

### 3.2. Gemma 4 4B
Serves as the "sweet spot" between speed and capability. It generally follows instructions better than 2B and maintains output formatting more reliably.

### 3.3. Gemma 4 26B
Used as a benchmark for larger models. Note that in this experiment, the 26B version was also "wrapped" in the same architectural enhancements (Capsule Text, 3-tier inference, etc.). This allows us to see if larger models still maintain a significant advantage when both are optimized architecturally.

*Observation:* 26B did not achieve the highest score in this specific router/workflow benchmark. This suggests that larger models may suffer from higher latency, "overthinking," or difficulty adhering to strict JSON contracts in fast-response scenarios.

---

## 4. Behavioral Hallucination Defined

I categorized hallucinations into three levels:
1. **Content Hallucination**: Factual errors or made-up information.
2. **Intent Hallucination**: Misunderstanding the user's goal (e.g., interpreting "How do I delete a file?" as a command to "Delete a file").
3. **Action Hallucination**: Selecting the wrong tool, skipping steps, or executing without permission.

The goal of this research is to ensure the model **behaves** correctly, not just answers eloquently.

---

## 5. Architectural Foundation (Mathematical/Logical)

I incorporated several mathematical concepts to transition from rigid if-else routers to flexible "soft" routers:

### 5.1. Semantic Routing & Cosine Similarity
Using vector embeddings to measure similarity between user commands and intents.
$$cos(\theta) = \frac{A \cdot B}{\|A\|\|B\|}$$
This reduces dependence on keywords but is insufficient on its own for ambiguous cases.

### 5.2. Attention-Aware Parsing
Recognizing that certain words (e.g., "if", "suppose", "after") carry more "weight" in determining the logic of a request.

### 5.3. Chain Probability
Treating multi-step workflows as a sequence of conditional probabilities:
$$P(x_1, x_2, ..., x_n) = \prod_{i=1}^{n} P(x_i | x_1, ..., x_{i-1})$$
If the probability of the "Intent Analysis" step is low, the "Action" step should not trigger.

### 5.4. Bayesian Inference
Updating the system's "belief" in an intent based on new evidence. If confidence is low, the system should ask for clarification.

### 5.5. Graph Reasoning
Mapping workflows as nodes and dependencies as edges, ensuring the system finds a valid path rather than just jumping to an action.

---

## 6. Weaknesses of Individual Methods

1. **Semantic Similarity Overlap**: Closely related intents (e.g., "Read email" vs "Summarize email") often have similar cosine scores.
2. **Small LLM Cognitive Load**: Forcing a small model to handle intent, tool selection, planning, and formatting simultaneously leads to failure.
3. **Overthinking**: Unconstrained "Deep Reasoning" can lead to long, rambling outputs that break JSON contracts.

---

## 7. The Proposed "V5" Improvements

### 7.1. Capsule Text
Context is compressed and normalized into structured "capsules" before being fed to the model. This reduces the cognitive load on 2B/4B models by providing pre-processed, high-signal data.

### 7.2. Three-Tier Inference Architecture
- **Tier 1 (Fast Recognition)**: Handles keywords and simple indicators.
- **Tier 2 (Algorithmic Routing)**: Uses scoring and semantic thresholds.
- **Tier 3 (Deep Reasoning/Safety)**: Validates plans and manages risks.

### 7.3. Multi-Factor Scoring
The router doesn't just pick the highest semantic score; it aggregates:
- Keyword match
- Semantic similarity
- Context compatibility
- Risk assessment
- Confidence score

### 7.4. Ask-Back Mechanism
The system is explicitly programmed to "not guess." If confidence is below a threshold, it asks the user for clarification.

### 7.5. Safety Gates
A hard validation layer that blocks dangerous actions (e.g., file deletion) regardless of what the LLM suggests, unless explicit confirmation is provided.

---

## 8. Experimental Setup

I compared 5 versions of the "Brain":
- **V1**: Rule-based (Keywords)
- **V2**: Math-based (Similarity scores)
- **V3**: Gemma 4 26B + Advanced Architecture
- **V4**: Gemma 4 2B + Advanced Architecture
- **V5**: Gemma 4 4B + Advanced Architecture

**40 Test Cases** across 8 categories: Basic Info, Simple Action, Context, Logic/Search, Pipeline, Comparison, Safety, and Privacy.

---

## 9. Key Results

| Version | Model | Avg. Score |
| :--- | :--- | :--- |
| V1 | Rule-based | 77.1% |
| V2 | Math-based | 83.3% |
| V3 | 26B (Optimized) | 77.1% |
| V4 | 2B (Optimized) | 87.5% |
| **V5** | **4B (Optimized)** | **93.8%** |

*Note: V5 achieved 100% in Safety and Privacy categories.*

---

## 10. Analysis & Conclusions

1. **4B is the "Sweet Spot"**: Gemma 4 4B provides the best balance of instruction-following and stability for local agentic tasks.
2. **Architecture Matters More Than Size**: The optimized 2B (V4) outperformed the optimized 26B (V3) and the base mathematical router (V2).
3. **Reasoning as a Workflow**: "Deep Reasoning" should be a structured process with checkpoints, not just a prompt instruction.
4. **Behavioral Control**: To reduce hallucination in small models, the question is not "Is the model smart enough?" but "Is the architecture controlling the model's behavior correctly?"

## 11. Next Steps

- **Scaling Tests**: Increase from 40 to 500+ test cases.
- **Specialized Comparison Module**: Improve reasoning for "Option A vs Option B" tasks.
- **Error Dataset**: Build a dataset of behavioral failures to fine-tune future models.

---

*This report is based on personal experimental data. For more details or to discuss the architecture, feel free to reach out via GitHub.*
