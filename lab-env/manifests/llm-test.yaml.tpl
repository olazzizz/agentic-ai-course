# One tiny chat completion against whatever Secret "lab-llm" points to (OpenRouter, Ollama, ...).
# Runs INSIDE the cluster, so it proves egress + credentials + model id where agents will actually run.
# Note: ${LLM_*} and ${code} below are shell variables for the container; labenv does not touch them.
apiVersion: batch/v1
kind: Job
metadata:
  name: llm-test
  labels:
    app.kubernetes.io/part-of: agentic-labs
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    metadata:
      labels:
        app.kubernetes.io/part-of: agentic-labs
    spec:
      restartPolicy: Never
      containers:
        - name: curl
          image: "${LAB_TEST_IMAGE}"
          envFrom:
            - secretRef:
                name: lab-llm
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            runAsNonRoot: true
            seccompProfile:
              type: RuntimeDefault
          command: ["/bin/sh", "-c"]
          args:
            - |
              code=$(curl -sS -o /tmp/out.json -w '%{http_code}' -m 120 \
                -H "Authorization: Bearer ${LLM_API_KEY}" \
                -H 'Content-Type: application/json' \
                "${LLM_BASE_URL}/chat/completions" \
                -d "{\"model\":\"${LLM_MODEL}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: pong\"}]}")
              echo "endpoint: ${LLM_BASE_URL}   model: ${LLM_MODEL}   HTTP ${code}"
              head -c 500 /tmp/out.json; echo
              [ "${code}" = "200" ]
