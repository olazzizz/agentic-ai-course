#!/usr/bin/env bash
#
# labenv.sh: reproducible, disposable lab environments on an (ephemeral) OpenShift cluster.
#
#   ./labenv.sh preflight                     check the cluster (capacity, permissions, GPUs, OpenShift AI)
#   ./labenv.sh up <lab> [--with <module>]    create the lab project, quota and an LLM backend
#                                               modules: openrouter (no GPU, remote inference)
#                                                        ollama     (small CPU model inside the cluster)
#   ./labenv.sh test-llm <lab>                call the lab's LLM endpoint from INSIDE the cluster (1 tiny request)
#   ./labenv.sh forward <lab> [svc] [port] [local-port]   port-forward a service to localhost (default: ollama)
#   ./labenv.sh status <lab>                  what is running, LLM backend, quota, uptime / cost estimate
#   ./labenv.sh export <lab>                  save manifests, events and logs to ./artifacts (BEFORE teardown)
#   ./labenv.sh down <lab>                    export, then delete the lab project
#   ./labenv.sh list                          list lab projects on this cluster
#   ./labenv.sh render <lab> <template>       print a rendered manifest (no cluster needed)
#   ./labenv.sh models [filters]              OpenRouter models + prices: --free --tools --structured
#                                               --search TEXT --min-context N --limit N   (no cluster needed)
#   ./labenv.sh price <model-id>              print HOSTED_MODEL / HOSTED_PRICE_* lines for Lab 1's .env
#   ./labenv.sh key-check                     OpenRouter key usage and limit (never prints the key)
#
# Requirements: bash 3.2+, oc (for cluster commands), curl + python3 (for models/price/key-check).
# Config: copy labenv.env.example to labenv.env (or export variables). See README.md.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_DIR="$SCRIPT_DIR/manifests"
ENV_FILE="${LABENV_FILE:-$SCRIPT_DIR/labenv.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

# ---------- defaults (override in labenv.env or the environment) ----------
: "${LAB_PREFIX:=agentic-lab}"
: "${LAB_NAMESPACE:=}"            # set to reuse ONE existing project (e.g. Developer Sandbox)
: "${LAB_ASSUME_YES:=0}"          # 1 = skip the "is this the right cluster?" prompt
: "${LAB_COST_PER_HOUR:=}"        # USD/hour of your cluster, only used for the status estimate
: "${LAB_SECRETS_FILE:=}"         # KEY=VALUE file holding OPENROUTER_API_KEY (or HOSTED_* as in Lab 1's .env)
: "${LAB_OPENSHIFT_AI:=auto}"     # auto | 1 | 0
: "${LAB_ARTIFACT_DIR:=$SCRIPT_DIR/artifacts}"

: "${LAB_QUOTA_CPU_REQ:=8}"
: "${LAB_QUOTA_MEM_REQ:=24Gi}"
: "${LAB_QUOTA_CPU_LIM:=16}"
: "${LAB_QUOTA_MEM_LIM:=32Gi}"
: "${LAB_QUOTA_PODS:=30}"
: "${LAB_QUOTA_STORAGE:=50Gi}"

: "${LAB_OPENROUTER_BASE_URL:=https://openrouter.ai/api/v1}"
: "${LAB_OPENROUTER_MODEL:=}"     # required for the openrouter module; find one with: labenv.sh models --tools

: "${LAB_OLLAMA_IMAGE:=docker.io/ollama/ollama:latest}"
: "${LAB_OLLAMA_MODEL:=qwen3:4b}"
: "${LAB_OLLAMA_CPU_REQ:=2}"
: "${LAB_OLLAMA_CPU_LIM:=4}"
: "${LAB_OLLAMA_MEM_REQ:=6Gi}"
: "${LAB_OLLAMA_MEM_LIM:=10Gi}"
: "${LAB_OLLAMA_PVC_SIZE:=20Gi}"

: "${LAB_TEST_IMAGE:=registry.access.redhat.com/ubi9/ubi:latest}"   # any image that has curl

MODULES="openrouter ollama"
TEMPLATE_VARS="LAB_NS LAB_NAME LAB_STARTED_ISO LAB_STARTED_EPOCH LAB_TEST_IMAGE \
LAB_QUOTA_CPU_REQ LAB_QUOTA_MEM_REQ LAB_QUOTA_CPU_LIM LAB_QUOTA_MEM_LIM LAB_QUOTA_PODS LAB_QUOTA_STORAGE \
LAB_OLLAMA_IMAGE LAB_OLLAMA_MODEL LAB_OLLAMA_CPU_REQ LAB_OLLAMA_CPU_LIM LAB_OLLAMA_MEM_REQ LAB_OLLAMA_MEM_LIM LAB_OLLAMA_PVC_SIZE"

# ---------- helpers ----------
log()  { printf '\033[1;34m[labenv]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[labenv] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[labenv] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found in PATH."; }

require_login() {
  need_cmd oc
  oc whoami >/dev/null 2>&1 || die "Not logged in. Run: oc login --server=<api-url> ..."
}

confirm_cluster() {
  local server user ans
  server="$(oc whoami --show-server)"
  user="$(oc whoami)"
  log "Cluster: $server   user: $user"
  [[ "$LAB_ASSUME_YES" == "1" ]] && return 0
  read -r -p "Run on THIS cluster? [y/N] " ans || die "No answer (non-interactive?). Set LAB_ASSUME_YES=1."
  [[ "$ans" =~ ^[Yy]$ ]] || die "Aborted."
}

validate_lab() {
  [[ "$1" =~ ^[a-z0-9]([-a-z0-9]{0,30}[a-z0-9])?$ ]] \
    || die "Invalid lab name '$1' (lowercase letters, digits, '-'; max 32 chars). Example: lab01"
}

# Value of NAME from the environment, else from LAB_SECRETS_FILE (KEY=VALUE lines). Never logged.
secret_value() {
  local name="$1" val="${!1:-}"
  if [[ -z "$val" && -n "$LAB_SECRETS_FILE" && -f "$LAB_SECRETS_FILE" ]]; then
    val="$(grep -E "^${name}=" "$LAB_SECRETS_FILE" | tail -1 | cut -d= -f2- || true)"
    val="${val%\"}"; val="${val#\"}"
  fi
  printf '%s' "$val"
}

# Sets and exports every variable the templates use.
export_vars() {
  LAB_NAME="$1"
  if [[ -n "$LAB_NAMESPACE" ]]; then LAB_NS="$LAB_NAMESPACE"; else LAB_NS="${LAB_PREFIX}-$1"; fi
  LAB_STARTED_ISO="${LAB_STARTED_ISO:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  LAB_STARTED_EPOCH="${LAB_STARTED_EPOCH:-$(date +%s)}"
  local v
  for v in $TEMPLATE_VARS; do export "${v?}"; done
}

# Pure-bash ${VAR} substitution (no envsubst dependency). Only whitelisted variables are replaced;
# any other ${...} (e.g. shell variables inside a container script) is left untouched.
render() {
  local file="$MANIFEST_DIR/$1" content v
  [[ -f "$file" ]] || die "Template not found: $file"
  content="$(cat "$file")"
  for v in $TEMPLATE_VARS; do
    content="${content//\$\{$v\}/${!v-}}"
  done
  printf '%s\n' "$content"
}

apply() {
  render "$1" | oc apply -n "$LAB_NS" -f - >/dev/null
}

apply_optional() {
  local out
  if ! out="$(render "$1" | oc apply -n "$LAB_NS" -f - 2>&1)"; then
    warn "Could not apply $1 (insufficient permissions?). Continuing without it."
    warn "$out"
  fi
}

detect_openshift_ai() {
  case "$LAB_OPENSHIFT_AI" in
    1|0) echo "$LAB_OPENSHIFT_AI"; return ;;
  esac
  if oc get crd datascienceclusters.datasciencecluster.opendatahub.io >/dev/null 2>&1; then echo 1; return; fi
  if oc get csv -A -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep -qiE '^(rhods|opendatahub)-operator'; then echo 1; else echo 0; fi
}

ensure_namespace() {
  if oc get project "$LAB_NS" >/dev/null 2>&1; then
    log "Project $LAB_NS already exists (reusing)."
  else
    log "Creating project $LAB_NS"
    oc new-project "$LAB_NS" --skip-config-write >/dev/null \
      || die "Cannot create project $LAB_NS. On restricted clusters (e.g. Developer Sandbox) set LAB_NAMESPACE to an existing project."
  fi
  oc label namespace "$LAB_NS" "agentic-labs/managed=true" "agentic-labs/lab=$LAB_NAME" --overwrite >/dev/null 2>&1 \
    || warn "Could not label the namespace (insufficient permissions?). Not critical."
  if [[ "$(detect_openshift_ai)" == "1" ]]; then
    log "OpenShift AI detected: marking $LAB_NS as a Data Science Project."
    oc label namespace "$LAB_NS" "opendatahub.io/dashboard=true" --overwrite >/dev/null 2>&1 \
      || warn "Could not add the Data Science Project label."
  fi
}

# Reuse the original start time when `up` is re-run, so the elapsed/cost estimate stays honest.
load_start_time() {
  local prev
  prev="$(oc get configmap lab-info -n "$LAB_NS" -o jsonpath='{.data.started_epoch}' 2>/dev/null || true)"
  if [[ -n "$prev" ]]; then
    LAB_STARTED_EPOCH="$prev"
    LAB_STARTED_ISO="$(oc get configmap lab-info -n "$LAB_NS" -o jsonpath='{.data.started_iso}' 2>/dev/null || true)"
  fi
  export LAB_STARTED_EPOCH LAB_STARTED_ISO
}

# The ONE contract every in-cluster agent uses, whatever serves the model:
#   Secret lab-llm -> LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   (consume with envFrom.secretRef)
write_llm_secret() {
  local base_url="$1" api_key="$2" model="$3" tmp rc=0
  tmp="$(mktemp)"; chmod 600 "$tmp"
  printf 'LLM_BASE_URL=%s\nLLM_API_KEY=%s\nLLM_MODEL=%s\n' "$base_url" "$api_key" "$model" > "$tmp"
  oc create secret generic lab-llm -n "$LAB_NS" --from-env-file="$tmp" --dry-run=client -o yaml \
    | oc apply -n "$LAB_NS" -f - >/dev/null || rc=$?
  rm -f "$tmp"
  [[ "$rc" -eq 0 ]] || die "Failed to create Secret lab-llm."
  oc label secret lab-llm -n "$LAB_NS" app.kubernetes.io/part-of=agentic-labs --overwrite >/dev/null
  log "Secret lab-llm written (LLM_BASE_URL=$base_url, LLM_MODEL=$model; the key is not printed)."
}

# In-cluster smoke test: proves egress + credentials + model name from where agents will actually run.
run_llm_test() {
  local s f rc=1
  oc delete job llm-test -n "$LAB_NS" --ignore-not-found >/dev/null 2>&1 || true
  apply llm-test.yaml.tpl
  log "Testing the LLM endpoint from INSIDE the cluster (one tiny request)..."
  for _ in $(seq 1 60); do
    s="$(oc get job llm-test -n "$LAB_NS" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
    f="$(oc get job llm-test -n "$LAB_NS" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    if [[ -n "$s" || -n "$f" ]]; then break; fi
    sleep 3
  done
  oc logs job/llm-test -n "$LAB_NS" 2>&1 | sed 's/^/    /' || true
  if [[ "${s:-}" == "1" ]]; then log "In-cluster LLM test: OK"; rc=0; else
    warn "In-cluster LLM test FAILED. Typical causes: egress to the endpoint blocked (proxy/firewall), wrong key, wrong model id, model still loading."
  fi
  oc delete job llm-test -n "$LAB_NS" --ignore-not-found >/dev/null 2>&1 || true
  return "$rc"
}

# ---------- modules (inference backends: use ONE per lab) ----------
module_openrouter_up() {
  local key model
  key="$(secret_value OPENROUTER_API_KEY)"
  model="$LAB_OPENROUTER_MODEL"
  if [[ -z "$key" && "$(secret_value HOSTED_BASE_URL)" == *openrouter.ai* ]]; then
    key="$(secret_value HOSTED_API_KEY)"
    [[ -n "$model" ]] || model="$(secret_value HOSTED_MODEL)"
    log "Using the OpenRouter HOSTED_* values from $LAB_SECRETS_FILE."
  fi
  [[ -n "$key" ]]   || die "No OpenRouter key: export OPENROUTER_API_KEY or set LAB_SECRETS_FILE (see README)."
  [[ -n "$model" ]] || die "No model: set LAB_OPENROUTER_MODEL. Find cheap tool-capable ones with: bash labenv.sh models --tools"
  write_llm_secret "$LAB_OPENROUTER_BASE_URL" "$key" "$model"
  run_llm_test || warn "Continuing anyway; fix the cause and re-run: bash labenv.sh test-llm $LAB_NAME"
}

module_ollama_up() {
  log "Deploying Ollama (CPU) with model $LAB_OLLAMA_MODEL"
  apply ollama.yaml.tpl
  oc rollout status deployment/ollama -n "$LAB_NS" --timeout=600s
  log "Pulling model $LAB_OLLAMA_MODEL (first pull takes a few minutes)"
  oc exec -n "$LAB_NS" deployment/ollama -- env OLLAMA_HOST=127.0.0.1:11434 ollama pull "$LAB_OLLAMA_MODEL"
  write_llm_secret "http://ollama.${LAB_NS}.svc:11434/v1" "ollama" "$LAB_OLLAMA_MODEL"
}

# ---------- cluster commands ----------
cmd_preflight() {
  require_login
  log "Server:        $(oc whoami --show-server)"
  log "User:          $(oc whoami)"
  log "OpenShift:     $(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null || echo 'unknown (no access to clusterversion)')"
  if oc auth can-i '*' '*' --all-namespaces >/dev/null 2>&1; then
    log "Permissions:   cluster-admin (full control)"
  else
    warn "Permissions:   NOT cluster-admin. Quotas/limits may be skipped and project creation may be restricted."
  fi
  if oc auth can-i create projectrequests >/dev/null 2>&1; then
    log "Projects:      you can create projects"
  else
    warn "Projects:      you cannot create projects; set LAB_NAMESPACE to an existing one."
  fi

  local nodes gpus
  nodes="$(oc get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.allocatable.cpu}{"\t"}{.status.allocatable.memory}{"\t"}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' 2>/dev/null || true)"
  log "Nodes (name, allocatable CPU, allocatable memory, GPUs):"
  printf '%s\n' "$nodes" | sed 's/^/    /'
  gpus="$(printf '%s\n' "$nodes" | awk -F'\t' '$4 != "" && $4 != "0"' | wc -l | tr -d ' ')"
  if [[ "$gpus" == "0" ]]; then
    log "GPU nodes:     none. Fine: use '--with openrouter' for model inference (or '--with ollama' for small CPU models)."
  else
    log "GPU nodes:     $gpus"
  fi

  local sc
  sc="$(oc get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{end}' 2>/dev/null || true)"
  if [[ -n "$sc" ]]; then log "Default StorageClass: $sc"; else warn "No default StorageClass found; PVCs (needed only by the ollama module) will stay Pending."; fi

  local ops
  ops="$(oc get csv -A -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | sort -u \
        | grep -Ei 'rhods|opendatahub|gpu-operator|nvidia|nfd|servicemesh|serverless|rhbk|keycloak|kuadrant|rhcl|connectivity|pipelines|gitops' || true)"
  if [[ -n "$ops" ]]; then
    log "Relevant operators installed:"; printf '%s\n' "$ops" | sed 's/^/    /'
  else
    log "No relevant operators detected (or no permission to list them)."
  fi

  if [[ "$(detect_openshift_ai)" == "1" ]]; then
    log "OpenShift AI:  detected ($(oc get datasciencecluster -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'phase unknown'))"
  else
    log "OpenShift AI:  not detected (fine for most labs; needed for Lab 15)."
  fi
  if [[ -n "$(secret_value OPENROUTER_API_KEY)$(secret_value HOSTED_API_KEY)" ]]; then
    log "OpenRouter:    an API key is available to this script (not printed). Verify egress with: test-llm after 'up'."
  fi
  log "Preflight done. Nothing was changed on the cluster."
}

cmd_up() {
  local lab="${1:-}"
  [[ -n "$lab" ]] || die "usage: labenv.sh up <lab> [--with <module>]   (modules: $MODULES)"
  shift
  local modules=() backends=0 m
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with) [[ -n "${2:-}" ]] || die "--with needs a module name"; modules+=("$2"); shift 2 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
  validate_lab "$lab"
  for m in ${modules[@]+"${modules[@]}"}; do
    declare -F "module_${m}_up" >/dev/null || die "Unknown module '$m'. Available: $MODULES"
    backends=$((backends + 1))
  done
  [[ "$backends" -le 1 ]] || die "Choose ONE inference backend per lab (openrouter OR ollama). To compare them, run Lab 1's compare.py from your laptop."
  require_login
  confirm_cluster
  export_vars "$lab"

  ensure_namespace
  load_start_time
  apply_optional quota.yaml.tpl
  apply lab-info.yaml.tpl
  for m in ${modules[@]+"${modules[@]}"}; do "module_${m}_up"; done

  log "Lab '$lab' is up in project $LAB_NS."
  for m in ${modules[@]+"${modules[@]}"}; do
    case "$m" in
      ollama)
        log "Laptop access: bash labenv.sh forward $lab   (2nd terminal), then LOCAL_BASE_URL=http://localhost:11434/v1  LOCAL_MODEL=$LAB_OLLAMA_MODEL" ;;
      openrouter)
        log "Laptop access: no forward needed. Use OpenRouter directly (HOSTED_BASE_URL=$LAB_OPENROUTER_BASE_URL)." ;;
    esac
    log "In-cluster agents: add   envFrom: - secretRef: {name: lab-llm}   -> LLM_BASE_URL, LLM_API_KEY, LLM_MODEL"
  done
  warn "REMINDER: the cluster bills until you destroy it. Run './labenv.sh down $lab' and then decommission the cluster."
}

cmd_test_llm() {
  local lab="${1:-}"
  [[ -n "$lab" ]] || die "usage: labenv.sh test-llm <lab>"
  validate_lab "$lab"; require_login; export_vars "$lab"
  oc get secret lab-llm -n "$LAB_NS" >/dev/null 2>&1 || die "Secret lab-llm not found in $LAB_NS. Run: up $lab --with openrouter|ollama"
  run_llm_test
}

cmd_forward() {
  local lab="${1:-}" svc="${2:-ollama}" port="${3:-11434}"
  local local_port="${4:-$port}"
  [[ -n "$lab" ]] || die "usage: labenv.sh forward <lab> [service] [port] [local-port]"
  validate_lab "$lab"; require_login; export_vars "$lab"
  log "Forwarding localhost:$local_port -> svc/$svc:$port in $LAB_NS (Ctrl-C to stop)"
  exec oc port-forward -n "$LAB_NS" "svc/$svc" "$local_port:$port"
}

cmd_status() {
  local lab="${1:-}"
  [[ -n "$lab" ]] || die "usage: labenv.sh status <lab>"
  validate_lab "$lab"; require_login; export_vars "$lab"
  oc get pods,pvc,svc -n "$LAB_NS" || true
  oc get resourcequota -n "$LAB_NS" 2>/dev/null || true
  local url model epoch hours est
  url="$(oc get secret lab-llm -n "$LAB_NS" -o jsonpath='{.data.LLM_BASE_URL}' 2>/dev/null | base64 --decode 2>/dev/null || true)"
  model="$(oc get secret lab-llm -n "$LAB_NS" -o jsonpath='{.data.LLM_MODEL}' 2>/dev/null | base64 --decode 2>/dev/null || true)"
  if [[ -n "$url" ]]; then log "LLM backend: $url   model: $model"; else log "LLM backend: none configured"; fi
  epoch="$(oc get configmap lab-info -n "$LAB_NS" -o jsonpath='{.data.started_epoch}' 2>/dev/null || true)"
  if [[ -n "$epoch" ]]; then
    hours="$(awk -v s="$epoch" -v n="$(date +%s)" 'BEGIN{printf "%.2f", (n-s)/3600}')"
    log "Lab uptime: ${hours} h"
    if [[ -n "$LAB_COST_PER_HOUR" ]]; then
      est="$(awk -v h="$hours" -v c="$LAB_COST_PER_HOUR" 'BEGIN{printf "%.2f", h*c}')"
      log "Estimated cluster cost since 'up': ~\$${est} (at \$${LAB_COST_PER_HOUR}/h). The cluster keeps billing until destroyed."
    fi
  fi
  log "Token spend is separate: run 'bash labenv.sh key-check' for OpenRouter usage."
}

cmd_export() {
  local lab="${1:-}"
  [[ -n "$lab" ]] || die "usage: labenv.sh export <lab>"
  validate_lab "$lab"; require_login; export_vars "$lab"
  local out pod
  out="$LAB_ARTIFACT_DIR/$lab/$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$out/logs"
  { oc version; oc get clusterversion; } > "$out/cluster.txt" 2>&1 || true
  # Secrets are deliberately NOT exported.
  oc get all,pvc,configmap,route,resourcequota -n "$LAB_NS" -o yaml > "$out/resources.yaml" 2> "$out/resources.err" \
    || warn "Some resources could not be exported (see $out/resources.err)."
  oc get events -n "$LAB_NS" --sort-by=.lastTimestamp > "$out/events.txt" 2>&1 || true
  while read -r pod; do
    [[ -n "$pod" ]] || continue
    oc logs -n "$LAB_NS" "$pod" --all-containers=true > "$out/logs/${pod#pod/}.log" 2>&1 || true
  done < <(oc get pods -n "$LAB_NS" -o name 2>/dev/null)
  log "Exported to $out"
  log "Files you created on your laptop (code, results.csv) are already local. Anything written inside pods is lost unless you 'oc cp' it."
}

cmd_down() {
  local lab="${1:-}" export_first=1
  [[ -n "$lab" ]] || die "usage: labenv.sh down <lab> [--no-export]"
  [[ "${2:-}" == "--no-export" ]] && export_first=0
  validate_lab "$lab"; require_login; confirm_cluster; export_vars "$lab"
  [[ "$export_first" == "1" ]] && cmd_export "$lab"
  if [[ -n "$LAB_NAMESPACE" ]]; then
    log "LAB_NAMESPACE is set: keeping project $LAB_NS, deleting only resources created by labenv."
    oc delete all,pvc,configmap,secret -n "$LAB_NS" -l app.kubernetes.io/part-of=agentic-labs --ignore-not-found
    oc delete resourcequota,limitrange -n "$LAB_NS" -l app.kubernetes.io/part-of=agentic-labs --ignore-not-found 2>/dev/null || true
  else
    log "Deleting project $LAB_NS"
    oc delete project "$LAB_NS" --ignore-not-found
  fi
  log "Lab '$lab' removed. Now decommission the CLUSTER itself (that is where the money is)."
  warn "If you used OpenRouter: delete or rotate the API key you gave this lab (it was stored in a cluster Secret)."
}

cmd_list() {
  require_login
  oc get projects -o name 2>/dev/null | sed 's#^project.project.openshift.io/##; s#^project/##' | grep "^${LAB_PREFIX}-" \
    || log "No lab projects with prefix '${LAB_PREFIX}-'."
}

cmd_render() {
  local lab="${1:-}" tpl="${2:-}"
  [[ -n "$lab" && -n "$tpl" ]] || die "usage: labenv.sh render <lab> <template.yaml.tpl>"
  validate_lab "$lab"; export_vars "$lab"
  render "$tpl"
}

# ---------- OpenRouter helpers (no cluster needed) ----------
cmd_models() {
  need_cmd curl; need_cmd python3
  local json py
  json="$(curl -fsS -m 30 "${LAB_OPENROUTER_BASE_URL}/models")" \
    || die "Cannot fetch ${LAB_OPENROUTER_BASE_URL}/models (network/proxy?)."
  read -r -d '' py <<'PYEOF' || true
import argparse, json, sys

ap = argparse.ArgumentParser()
ap.add_argument("--free", action="store_true")
ap.add_argument("--tools", action="store_true")
ap.add_argument("--structured", action="store_true")
ap.add_argument("--search", default="")
ap.add_argument("--min-context", type=int, default=0)
ap.add_argument("--limit", type=int, default=25)
ap.add_argument("--price-of", default="")
a = ap.parse_args()

data = json.load(sys.stdin).get("data", [])

def per_million(m, key):
    """USD per 1M tokens, or None when the price is missing/negative (unknown, e.g. router pseudo-models)."""
    try:
        v = float((m.get("pricing") or {}).get(key))
    except (TypeError, ValueError):
        return None
    return v * 1e6 if v >= 0 else None

def fmt(v):
    return "?" if v is None else "%.3f" % v

rows = []
for m in data:
    mid = m.get("id", "")
    params = m.get("supported_parameters") or []
    rows.append({
        "id": mid,
        "pin": per_million(m, "prompt"),
        "pout": per_million(m, "completion"),
        "ctx": m.get("context_length") or 0,
        "tools": "tools" in params,
        "struct": ("structured_outputs" in params) or ("response_format" in params),
    })

if a.price_of:
    hit = [r for r in rows if r["id"] == a.price_of]
    if not hit:
        sys.exit("Model id not found: " + a.price_of + "   (list ids with: labenv.sh models --search ...)")
    r = hit[0]
    if r["pin"] is None or r["pout"] is None:
        sys.exit("Pricing for %s is not published by the API (variable/routed model). Check the OpenRouter page." % r["id"])
    print("HOSTED_MODEL=%s" % r["id"])
    print("HOSTED_PRICE_IN=%s" % ("%.4f" % r["pin"]).rstrip("0").rstrip("."))
    print("HOSTED_PRICE_OUT=%s" % ("%.4f" % r["pout"]).rstrip("0").rstrip("."))
    if not r["tools"]:
        print("# note: this model does not advertise tool calling (needed from Chapter 3)", file=sys.stderr)
    sys.exit(0)

def keep(r):
    if a.free and not (r["pin"] == 0 and r["pout"] == 0):  # None (unknown) never counts as free
        return False
    if a.tools and not r["tools"]:
        return False
    if a.structured and not r["struct"]:
        return False
    if a.search and a.search.lower() not in r["id"].lower():
        return False
    return r["ctx"] >= a.min_context

def sort_key(r):
    unknown = r["pin"] is None or r["pout"] is None
    return (unknown, 0 if unknown else r["pin"] + r["pout"], -r["ctx"])

rows = sorted((r for r in rows if keep(r)), key=sort_key)
print("%-52s %9s %9s %9s  %-5s %-6s" % ("model id", "in $/M", "out $/M", "context", "tools", "json"))
for r in rows[: a.limit]:
    print("%-52s %9s %9s %9d  %-5s %-6s" % (r["id"][:52], fmt(r["pin"]), fmt(r["pout"]), r["ctx"],
          "yes" if r["tools"] else "-", "yes" if r["struct"] else "-"))
print("\n%d match(es), showing up to %d, cheapest first. Prices in USD per 1M tokens." % (len(rows), a.limit), file=sys.stderr)
if a.free:
    print("Free models: rate-limited, may change/disappear, and may log prompts. Synthetic/public data only.", file=sys.stderr)
PYEOF
  printf '%s' "$json" | python3 -c "$py" "$@"
}

cmd_price() {
  [[ -n "${1:-}" ]] || die "usage: labenv.sh price <model-id>"
  cmd_models --price-of "$1"
}

cmd_key_check() {
  need_cmd curl; need_cmd python3
  local key out
  key="$(secret_value OPENROUTER_API_KEY)"
  if [[ -z "$key" && "$(secret_value HOSTED_BASE_URL)" == *openrouter.ai* ]]; then key="$(secret_value HOSTED_API_KEY)"; fi
  [[ -n "$key" ]] || die "No OpenRouter key found (export OPENROUTER_API_KEY or set LAB_SECRETS_FILE)."
  # The key goes to curl through stdin (-K -) so it never appears in the process list.
  out="$(printf 'header = "Authorization: Bearer %s"\n' "$key" | curl -fsS -m 30 -K - "${LAB_OPENROUTER_BASE_URL}/key" 2>/dev/null)" \
    || out="$(printf 'header = "Authorization: Bearer %s"\n' "$key" | curl -fsS -m 30 -K - "${LAB_OPENROUTER_BASE_URL}/auth/key" 2>/dev/null)" \
    || die "Key check failed (bad key, network, or the endpoint changed: check the OpenRouter dashboard instead)."
  log "OpenRouter key status:"
  printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin).get("data", {})
for k in ("label", "usage", "limit", "limit_remaining", "is_free_tier"):
    print("    %s: %s" % (k, d.get(k)))
'
}

usage() { awk 'NR >= 3 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"; }

case "${1:-help}" in
  preflight) shift; cmd_preflight "$@" ;;
  up)        shift; cmd_up "$@" ;;
  test-llm)  shift; cmd_test_llm "$@" ;;
  forward)   shift; cmd_forward "$@" ;;
  status)    shift; cmd_status "$@" ;;
  export)    shift; cmd_export "$@" ;;
  down)      shift; cmd_down "$@" ;;
  list)      shift; cmd_list "$@" ;;
  render)    shift; cmd_render "$@" ;;
  models)    shift; cmd_models "$@" ;;
  price)     shift; cmd_price "$@" ;;
  key-check) shift; cmd_key_check "$@" ;;
  help|-h|--help) usage ;;
  *) usage; exit 1 ;;
esac
