# Agentic AI & MCP: Zero to Hero

**Audience:** a Solution Architect who must design, size, secure and explain agentic AI solutions, and prototype them personally.
**Balance:** vendor-neutral first (Parts 1 to 4), Red Hat second (Part 5), customer work last (Part 6).
**Pace:** 16 chapters, about 12 weeks at 6 h/week. **Fast track (4 weeks):** Ch 1, 2, 3, 7, 8, 10, 12, 15, 16.
**Cost target:** $0, hard cap about $25 for the whole path. Every lab targets an OpenAI-compatible endpoint (`base_url` + `model`), so local, free and paid models are interchangeable.

> This field moves monthly. Verify versions, free tiers and product support status (GA vs tech preview) before relying on them or quoting them to customers.

## Repository layout

```
agentic-labs/
├── SYLLABUS.md
├── chapter-NN/
│   ├── chapter-NN-<topic>.html     # the lessons (open in a browser)
│   └── labNN/                      # lab code; copy .env.example to .env (never commit .env)
└── lab-env/                        # shared, optional cluster + OpenRouter toolkit
```

Commands in a lab folder reach the shared toolkit at `../../lab-env/`.

## Cost strategy

| Tier | What | Cost |
|---|---|---|
| Local models | Ollama, or RamaLama / Podman AI Lab; 3 to 8B models (Qwen3, Llama 3.x, Granite). Slow on CPU, and small models are unreliable at tool calling | $0 |
| Free API tiers | Google AI Studio, GitHub Models, Groq, OpenRouter `:free` models; rate-limited, subject to change | $0 |
| Cheap paid API | Small, fast, tool-capable models. Set a **hard spend limit** in the provider console | about $10 in total |
| Kubernetes | `kind` locally, the free Red Hat Developer Sandbox, or a disposable OpenShift cluster via `lab-env/` | $0 to cluster cost |
| GPU | Avoid. Rent one only for Lab 13 (free Colab tier or a few spot hours) | $0 to $5 |

**Cluster labs:** 9, 14, 15a/c/d. Run several in one cluster session and destroy the cluster once. **GPU lab:** 13 only.

---

## Part 1: Foundations

### Chapter 1: LLM essentials for architects
- 1.1 How LLMs behave: tokens, context window, sampling, non-determinism, hallucination
- 1.2 Model landscape: closed vs open-weight, reasoning models, SLMs, multimodal; selection criteria
- 1.3 Key techniques: prompting, structured outputs, RAG vs fine-tuning vs prompt engineering
- 1.4 Economics: input vs output pricing, prompt caching, batch APIs, why agents multiply cost

**Lab 1 (2 h, $0):** compare a local and a hosted model on latency, tokens/s, cost and JSON-schema reliability.

### Chapter 2: From LLM to agent
- 2.1 Workflow vs agent; autonomy as a spectrum
- 2.2 The agent loop: ReAct, stop conditions, tool errors as observations
- 2.3 Building blocks: model, instructions, tools, memory, guardrails, orchestration
- 2.4 Patterns: chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer, plan-and-execute, reflection
- 2.5 When not to use agents

**Lab 2 (3 h, about $0.10):** an agent loop from scratch (about 100 lines), with guards, tracing and a workflow comparison.

## Part 2: Building agents

### Chapter 3: Tool use and function calling
- 3.1 Mechanics: schemas, calls, results, parallel calls
- 3.2 Tool design: naming, descriptions as prompts, few well-scoped tools, actionable errors, idempotency
- 3.3 Tool categories: retrieval, computation, code execution (sandboxing), browser/computer use, APIs, other agents
- 3.4 Failure modes: wrong tool, bad arguments, loops, tool overload

**Lab 3 (3 h, $0 to $1):** extend Lab 2 with 5 tools (REST, SQLite, web fetch...); break tool descriptions on purpose and measure success over 20 tasks.

### Chapter 4: Agent frameworks landscape
- 4.1 The map: LangGraph, CrewAI, LlamaIndex, Pydantic AI, smolagents, OpenAI Agents SDK, Google ADK, Claude Agent SDK, Microsoft Agent Framework, Quarkus LangChain4j / Spring AI
- 4.2 Graph vs role-based vs code-first: control, debuggability, state, human-in-the-loop
- 4.3 Build vs adopt vs low-code
- 4.4 Framework neutrality and why it matters to enterprises

**Lab 4 (4 h, $0 to $2):** re-implement the Lab 3 agent in LangGraph and one other framework; write a one-page comparison.

### Chapter 5: Context engineering, memory and RAG
- 5.1 Context engineering: compaction, summarization, isolation via sub-agents
- 5.2 Memory types: short-term, long-term, episodic, procedural
- 5.3 RAG done right, and agentic RAG
- 5.4 Vector stores: pgvector, Milvus, Qdrant, Chroma; when Postgres is enough
- 5.5 Skills and instruction files (`AGENTS.md`, Agent Skills)

**Lab 5 (4 h, $0):** agentic RAG over a folder of Red Hat docs, with a 20-question retrieval test before and after reranking.

### Chapter 6: Multi-agent systems and A2A
- 6.1 Why and why not: specialization vs cost, complexity, compounding errors
- 6.2 Topologies: supervisor, hierarchical, swarm/handoff, blackboard
- 6.3 The A2A protocol and how it complements MCP (MCP = agent to tool, A2A = agent to agent)
- 6.4 Long-running and asynchronous agents: durable execution, checkpoints, resumption

**Lab 6 (4 h, $0 to $2):** supervisor plus two specialists, one exposed over A2A; compare token cost with a single agent.

## Part 3: The MCP track

### Chapter 7: MCP fundamentals
- 7.1 Why MCP: the N×M problem becomes N+M; neutral governance
- 7.2 Architecture: host, client, server; JSON-RPC 2.0
- 7.3 Server primitives: tools, resources, prompts
- 7.4 Client features and extensions: elicitation, tasks, MCP Apps
- 7.5 Transports: stdio vs Streamable HTTP
- 7.6 Spec versions and what changed in the latest revision (stateless core, scaling on Kubernetes)

**Lab 7 (2 h, $0):** install an existing MCP server in a host, inspect traffic with MCP Inspector, call its tools manually.

### Chapter 8: Building MCP servers and clients
- 8.1 Build a server (Python or TypeScript SDK)
- 8.2 Design principles: tools around user tasks, concise outputs, read-only vs mutating
- 8.3 Wrapping existing systems (OpenAPI to MCP is a start, not a finish)
- 8.4 Build a client with dynamic discovery and allow-lists
- 8.5 Testing: Inspector, in-memory transport, contract tests, tool-selection evals

**Lab 8 (5 h, $0):** an MCP server (4 tools, 1 resource, 1 prompt) connected to a desktop host and to your own agent; tests; container image with Podman.

### Chapter 9: MCP in the enterprise
- 9.1 Remote deployment and horizontal scaling
- 9.2 Authorization: OAuth 2.1, resource indicators, token exchange / on-behalf-of
- 9.3 MCP gateways as the central policy point
- 9.4 Registries and governance
- 9.5 Build vs buy vs consume

**Lab 9 (4 h, $0, cluster):** put the Lab 8 server behind Keycloak on `kind` with 2 replicas, and route it through a gateway with a per-user tool allow-list and audit log.

### Chapter 10: MCP and agent security
- 10.1 Threat model: prompt injection, tool poisoning, rug pulls, confused deputy, exfiltration, supply chain, SSRF
- 10.2 The lethal trifecta: private data, untrusted content, external communication
- 10.3 Mitigations: least privilege, approval gates, sandboxing, egress policy, pinning, audit
- 10.4 Frameworks: OWASP LLM and Agentic guidance, MITRE ATLAS

**Lab 10 (3 h, $0):** attack your own stack (indirect injection, malicious tool description, exfiltration), apply three mitigations, document before and after.

## Part 4: Production

### Chapter 11: Evaluation and observability
- 11.1 Why evals are the hardest part
- 11.2 Eval types: tool unit tests, task success, trajectory, LLM-as-judge, regression
- 11.3 Benchmarks worth knowing, and why to trust your own evals more
- 11.4 Observability: traces, cost and latency per run, OpenTelemetry GenAI conventions, Langfuse / Phoenix

**Lab 11 (4 h, $0):** add tracing, build a 30-task eval set, produce a cost and success dashboard for two models.

### Chapter 12: Guardrails, safety and governance
- 12.1 Guardrail layers and open options
- 12.2 Agent identity: non-human identities, scopes, workload identity
- 12.3 Governance and regulation: risk classification, sovereignty, EU AI Act basics, ISO 42001, NIST AI RMF
- 12.4 Human oversight patterns

**Lab 12 (3 h, $0):** add PII redaction, a topic guardrail and an approval gate; write a one-page agent risk card.

### Chapter 13: Inference, serving and cost engineering
- 13.1 Serving engines: vLLM, TGI, SGLang, llama.cpp
- 13.2 Optimization: quantization, speculative decoding, prefix/KV caching, LoRA, model routing
- 13.3 Distributed inference: parallelism, prefill/decode disaggregation, llm-d
- 13.4 Sizing rules of thumb for agent workloads
- 13.5 TCO: API vs self-hosted break-even

**Lab 13 (3 h, $0 to $5, GPU):** serve a small model with vLLM and benchmark at 1/8/32 concurrency with and without prefix caching. CPU fallback: a sizing spreadsheet from public specs.

## Part 5: Platform and Red Hat

### Chapter 14: Kubernetes-native agent platforms
- 14.1 Why agents need a platform
- 14.2 Building blocks: operators, KServe, gateways, Keycloak/SPIFFE, OpenTelemetry, GitOps
- 14.3 Open projects: Llama Stack, Kagenti, kagent, KServe, llm-d
- 14.4 Sandboxing agent-executed code

**Lab 14 (4 h, $0, cluster):** on `kind`, deploy the MCP server and agent as separate workloads with a NetworkPolicy, secrets, an OTel collector and Argo CD.

### Chapter 15: The Red Hat AI portfolio for agentic workloads
- 15.1 Portfolio map: AI Inference Server, RHEL AI, OpenShift AI, llm-d
- 15.2 Agents on OpenShift AI
- 15.3 Red Hat MCP servers and AI assistants (agentic operations)
- 15.4 Platform engineering angle: Developer Hub, GitOps, Service Mesh, Keycloak, Connectivity Link, Advanced Cluster Security
- 15.5 Deployment footprints: on-prem, air-gapped, sovereign, hybrid, edge
- 15.6 Honest positioning against hyperscaler and other alternatives

**Labs (14 h total, $0):** 15a Developer Sandbox deployment; 15b RamaLama / Podman AI Lab with Granite; 15c Llama Stack with an MCP tool group; 15d agentic-operations demo with the OpenShift/Kubernetes MCP server.

## Part 6: The architect's toolkit

### Chapter 16: Solution design, customer engagement and capstone
- 16.1 Use-case qualification: value, feasibility, risk
- 16.2 Discovery questions
- 16.3 Reference architectures: RAG assistant, ops agent with MCP gateway, multi-agent with A2A, air-gapped sovereign
- 16.4 Maturity model and matching governance
- 16.5 Delivery: PoC design, FinOps, change management
- 16.6 Objection handling

**Capstone (10 to 15 h, $0 to $10):** architecture document, working prototype on Kubernetes, eval report (30+ tasks), 15-minute demo and one-page executive summary.

---

## Self-assessment checkpoints

| After | Can you... |
|---|---|
| Ch 2 | explain on a whiteboard the difference between a workflow and an agent, and when you would refuse to use an agent? |
| Ch 6 | justify single-agent vs multi-agent on cost, latency and reliability? |
| Ch 10 | produce a threat model for an MCP-based agent in 30 minutes? |
| Ch 13 | size a GPU footprint for 200 concurrent agent users, with assumptions stated? |
| Ch 15 | position Red Hat against a hyperscaler managed option honestly in a customer meeting? |
| Ch 16 | run a 45-minute discovery workshop and leave with a qualified use case? |

## Core resources (free)

- **Concepts:** Anthropic *Building effective agents*; OpenAI *A practical guide to building agents*; Hugging Face Agents Course; DeepLearning.AI short courses
- **MCP:** modelcontextprotocol.io (spec, changelog), official SDKs and reference servers, MCP Inspector
- **Security:** OWASP Top 10 for LLM Applications, OWASP Agentic Security Initiative, Simon Willison on prompt injection
- **Serving and platform:** vLLM, llm-d, Llama Stack, Kagenti, KServe docs
- **Red Hat:** developers.redhat.com, Red Hat Developer Sandbox, OpenShift AI and RHEL AI docs, RamaLama and InstructLab on GitHub
