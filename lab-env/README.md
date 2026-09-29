# lab-env: disposable lab environments on OpenShift (with cheap, GPU-free inference)

A small toolkit to make every lab **reproducible on a throw-away OpenShift cluster**: one command to prepare the lab project and its LLM backend, one to save your results, one to remove everything.

```
lab-env/
├── labenv.sh              # the CLI (bash 3.2+; `oc` for cluster commands, curl+python3 for OpenRouter helpers)
├── labenv.env.example     # configuration (copy to labenv.env; no secrets in it)
└── manifests/             # templates: lab-info, quota, ollama, llm-test (more modules added chapter by chapter)
```

Run it with `bash labenv.sh <command>` (or `chmod +x` and use `./labenv.sh`).

## 1. Which labs need a cluster, and which need a GPU?

The cluster is the biggest fixed cost, so spend its hours only where they matter.

| Labs | Cluster? | GPU? |
|---|---|---|
| 0–8, 10–12 (basics, agents, MCP build, security, evals, guardrails) | Optional: laptop is enough | **No** (OpenRouter or a small local model) |
| 9 (enterprise MCP: auth, gateway, scaling) | **Yes** | No |
| 13 (vLLM serving/benchmark) | Yes, or a GPU box | **Yes.** This lab is *about* serving models, so remote inference can't replace it (a free Colab GPU is the cheap route) |
| 14 (K8s agent platform) | **Yes** | No |
| 15 (Red Hat portfolio, Llama Stack, agentic ops) | **Yes**, ideally with OpenShift AI | No for agent labs; yes if you want to see model serving on OpenShift AI |

**Cost tip:** a new cluster spends a long stretch provisioning (often tens of minutes for a full cluster, less for hosted-control-plane offerings; check your platform) and you pay for that idle time each time. Because each lab gets its own **project**, you can run several labs in **one cluster session** (e.g. 9 + 14 + 15), tear down each lab project as you finish, and destroy the cluster once. One cluster per lab works too, at a higher cost.

## 2. Choosing where the model runs

Agents only need *an OpenAI-compatible endpoint*. This toolkit standardizes that as **Secret `lab-llm`** (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`); any in-cluster agent consumes it with `envFrom: - secretRef: {name: lab-llm}`, so switching backend never changes agent code.

| Option | Pros | Cons | Use for |
|---|---|---|---|
| **OpenRouter** (`--with openrouter`) | No GPU, no model hosting, one key for hundreds of models, easy model swaps and comparisons, pay per token (cents per lab with cheap models), `:free` models exist | Data leaves your environment (to OpenRouter and the upstream provider); needs internet egress from the cluster; tool-calling quality varies per model; free tiers are rate-limited and can disappear; a small platform fee on credit purchases (check current terms) | **Default for all agent labs** with synthetic/public data |
| **Ollama on cluster CPU** (`--with ollama`) | Data stays in the cluster, works offline/air-gapped, no per-token cost | Slow (a few tokens/s), only small models, each new cluster re-downloads the model, uses cluster CPU/RAM you pay for | Showing the sovereign/on-prem story; CPU sizing intuition |
| **Ollama on your laptop** | Free, private, instant | Not reachable from the cluster; laptop hardware limits | Labs 1–8 |
| **vLLM on a GPU node** (Lab 13, later module) | Real production serving behavior, throughput, KV-cache tuning | GPU cost and quota, cluster GPU operator setup, long start-up | Learning inference engineering; customer sizing |
| **Direct provider API** (Anthropic, OpenAI, Google…) | Best models, first-party features | One key per provider, lock-in to that provider's API quirks | Cross-checking OpenRouter results |

**Customer-conversation angle:** OpenRouter is a *learning* tool here. In a regulated customer's architecture the equivalent is an **internal LLM gateway** (LiteLLM, Envoy AI Gateway, a cloud gateway, or Llama Stack) in front of approved models, with the same OpenAI-compatible contract you're practicing.

## 3. Quick start (OpenRouter path)

```bash
cd agentic-labs/lab-env
cp labenv.env.example labenv.env

# secrets live OUTSIDE git, chmod 600 (see §4)
mkdir -p ~/.config/labenv && chmod 700 ~/.config/labenv
printf 'OPENROUTER_API_KEY=sk-or-...\n' > ~/.config/labenv/secrets.env && chmod 600 ~/.config/labenv/secrets.env
echo 'LAB_SECRETS_FILE=$HOME/.config/labenv/secrets.env' >> labenv.env

# pick a cheap model that supports tool calling (needed from Chapter 3), cheapest first
bash labenv.sh models --tools --structured --limit 15
echo 'LAB_OPENROUTER_MODEL=<model-id-from-the-list>' >> labenv.env

oc login --server=https://api.<your-cluster>:6443 -u <user>
bash labenv.sh preflight                    # read-only: capacity, permissions, GPUs, OpenShift AI
bash labenv.sh up lab03 --with openrouter   # project + quota + Secret lab-llm + in-cluster connectivity test
# ... do the lab ...
bash labenv.sh key-check                    # token spend so far (never prints the key)
bash labenv.sh down lab03                   # exports logs/manifests to ./artifacts, then deletes the project
```

Then **decommission the cluster** (§8). `down` removes only the lab.

Commands: `preflight`, `up`, `test-llm`, `forward`, `status`, `export`, `down`, `list`, `render`, `models`, `price`, `key-check` (run `bash labenv.sh help`).

## 4. OpenRouter: setup, spend control and privacy

**Setup**
1. Create an account and an API key at openrouter.ai. Use a **dedicated key per lab batch**.
2. In the key settings, set a **credit limit** on it (a few dollars) so a runaway agent loop cannot drain the account. Verify the option in the current dashboard.
3. Add a small amount of credit (or start with `:free` models: `bash labenv.sh models --free --tools`).
4. Put the key in the secrets file (not in `labenv.env`, not in git). The file may instead hold the same `HOSTED_*` lines as Lab 1's `.env` when `HOSTED_BASE_URL` is OpenRouter; the script accepts either.

**Choosing a model (agents are demanding)**
- Require **tool calling** (`--tools`), and **structured outputs** (`--structured`) when you need schema-constrained JSON.
- `bash labenv.sh price <model-id>` prints the `HOSTED_MODEL` / `HOSTED_PRICE_IN` / `HOSTED_PRICE_OUT` lines that Lab 1's `compare.py` expects.
- Compare a `:free` model against a cheap paid one for tool-call reliability: free tiers are usually fine for Labs 1–2 and painful for loop-heavy or eval-heavy labs (rate limits, and one agent run = many calls).
- Free-tier limits change: check OpenRouter's current documentation before planning around them.

**Rough budget** (estimates: input tokens ≈ Lab 1's loop formula; assumes small models at ~$0.05–$0.50 per 1M tokens; multiply by 10–50× for frontier models)

| Lab | Approx. tokens | Approx. cost |
|---|---|---|
| 1 comparison | < 0.1 M | < $0.02 |
| 3 tool use (20 tasks × ~8 steps) | ~1 M | $0.05 – $0.50 |
| 6 multi-agent | ~2 M | $0.10 – $1 |
| 11 evals (30 tasks × 2 models) | ~3 M | $0.15 – $1.50 |
| Capstone | ~5 M | $0.25 – $2.50 |

The whole path stays comfortably under ~$10 with small models. Track it with `key-check`.

**Privacy**
- Prompts go to OpenRouter and on to the upstream model provider. **Use only synthetic or public data.** Never send customer data.
- Free models may log or train on prompts. Review OpenRouter's data-policy/privacy settings and model provider terms before using anything sensitive.
- The API key is stored in a cluster Secret for in-cluster agents; **delete or rotate the key after the cluster is destroyed.**
- The cluster needs outbound HTTPS to `openrouter.ai`. Restricted/disconnected clusters won't have it: `up` runs an in-cluster test and warns you (re-run any time with `bash labenv.sh test-llm <lab>`).

## 5. What `up` creates

| Object | Purpose |
|---|---|
| Project `agentic-lab-<lab>` (labelled `agentic-labs/managed=true`) | Isolation and one-command cleanup |
| `ResourceQuota` + `LimitRange` | Prevents a runaway lab from consuming the cluster (needs cluster-admin; skipped with a warning otherwise) |
| `ConfigMap lab-info` | Start time for the uptime/cost estimate |
| `Secret lab-llm` | The LLM contract: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Written by the chosen backend module; never exported |
| Module `openrouter` | Writes `lab-llm`, then runs an in-cluster smoke test Job (one tiny request) to prove egress + key + model |
| Module `ollama` | PVC + Deployment + Service (CPU), model auto-pulled, `lab-llm` pointing at the in-cluster Service. **No Route on purpose**; reach it with `forward` |

One backend per lab (`openrouter` **or** `ollama`). To compare them, run Lab 1's `compare.py` from your laptop against both.

With **OpenShift AI** present, the project is also labelled as a Data Science Project (verify in your version). **Restricted clusters** (e.g. Developer Sandbox): set `LAB_NAMESPACE=<existing-project>`; `down` then deletes only what the script created.

## 6. Lab 1 variants

| Variant | Steps | What you learn |
|---|---|---|
| **A. Laptop only (free)** | Ollama on laptop + OpenRouter as `HOSTED_*`. No cluster | Baseline numbers |
| **B. Cluster CPU vs OpenRouter** | `up lab01 --with ollama`, `forward lab01` (use `forward lab01 ollama 11434 11435` if laptop Ollama runs), keep `LOCAL_BASE_URL` pointing at the forwarded port, run `compare.py` | CPU inference sizing on real cloud nodes: record the node type and compare tokens/s with your laptop |

Default model is `qwen3:4b` for cluster CPU. For an 8B model set `LAB_OLLAMA_MODEL=qwen3:8b`, `LAB_OLLAMA_MEM_REQ=10Gi`, `LAB_OLLAMA_MEM_LIM=14Gi`.

## 7. Using a cluster with OpenShift AI

- `preflight` reports whether OpenShift AI is present, its phase, and related operators (GPU, NFD, Service Mesh, Serverless, Keycloak, Pipelines, GitOps…).
- Install OpenShift AI through whatever your platform provides (a catalog item that includes it, or OperatorHub). I deliberately did **not** script the operator install: channels and the `DataScienceCluster` API change between releases, so hard-coding them would break silently.
- For Lab 15 I'll add modules (Llama Stack, MCP servers on OpenShift AI, optionally model serving with a GPU) against the version you actually have. Tell me your OpenShift AI version then.

## 8. Decommissioning the cluster (where the money is)

`labenv.sh down` cleans the lab, not the cluster. Typical teardown (check your platform's current procedure):

| Cluster type | Teardown |
|---|---|
| Red Hat Demo Platform (if you use it) | Delete/retire the service from your dashboard |
| ROSA | `rosa delete cluster -c <name>` |
| Self-managed installer (IPI) | `openshift-install destroy cluster --dir <install-dir>` (keep that directory!) |
| OpenShift Local | `crc stop` (pause) or `crc delete` |

Set a calendar reminder when you create the cluster and a provider budget alert. Set `LAB_COST_PER_HOUR` so `status` shows a running estimate. Don't forget the OpenRouter key (§4).

## 9. Security notes

- `up`/`down` ask you to confirm the API server first, to protect you from the wrong cluster. `LAB_ASSUME_YES=1` skips this (don't set it globally).
- Keys are never placed on a command line: they go to `oc` via a `chmod 600` temp file that is removed immediately, and to `curl` via stdin.
- `export` excludes Secrets on purpose.
- Everything in the project is treated as untrusted after the lab.

## 10. Validation status (please read)

| Checked | How |
|---|---|
| Shell quality | `shellcheck` clean, `bash -n` OK, bash 3.2-compatible constructs |
| Templates | Rendered and parsed as valid Kubernetes YAML; container-side `${...}` variables preserved |
| `models` / `price` / `key-check` | Run against a **mock** of the OpenRouter API (the sandbox can't reach openrouter.ai). This caught and fixed one bug (unknown prices showing as free) |
| Cluster flows | `up` (openrouter and ollama), `test-llm` success and failure, backend guard, `status`, `export`, `down`, shared-project mode, all against a **stub `oc`**, checking the exact commands issued and that the API key never appears in any command line |
| **Not checked** | **A real OpenShift cluster and the real OpenRouter API** |

Most likely to need a tweak on first real use:
1. **OpenRouter API field names** (`/models` pricing and `supported_parameters`, and the `/key` endpoint). These are based on their documented API; if `models` or `key-check` misbehave, paste the output.
2. **Test image**: `ubi9/ubi` is assumed to have `curl` and be pullable anonymously. Override with `LAB_TEST_IMAGE`.
3. **Ollama under OpenShift's random UID** (`oc logs deploy/ollama`) and **Docker Hub pull limits** (mirror the image).
4. **No default StorageClass** (only matters for the ollama module; `preflight` warns).

## 11. Roadmap (modules added as we reach the labs)

| Module | Lab |
|---|---|
| `pgvector` | 5 |
| `mcp-server` (deploy your image, Service, optional authenticated Route) | 8–9 |
| `keycloak` + gateway policies | 9 |
| `otel-langfuse` (tracing) | 11 |
| `vllm` (GPU) | 13 |
| `llama-stack` (on OpenShift AI) | 15 |

Add a module: drop a template in `manifests/` and a `module_<name>_up()` function in `labenv.sh`; `up <lab> --with <name>` picks it up.
