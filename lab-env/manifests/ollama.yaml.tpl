# Ollama on CPU, OpenAI-compatible API on port 11434.
# Deliberately NO Route: an unauthenticated LLM endpoint must not be exposed. Use `labenv.sh forward`.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ollama-models
  labels:
    app.kubernetes.io/part-of: agentic-labs
    app: ollama
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: "${LAB_OLLAMA_PVC_SIZE}"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama
  labels:
    app.kubernetes.io/part-of: agentic-labs
    app: ollama
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: ollama
  template:
    metadata:
      labels:
        app: ollama
        app.kubernetes.io/part-of: agentic-labs
    spec:
      containers:
        - name: ollama
          image: "${LAB_OLLAMA_IMAGE}"
          ports:
            - name: http
              containerPort: 11434
          env:
            - name: OLLAMA_HOST
              value: "0.0.0.0:11434"
            - name: OLLAMA_MODELS
              value: /models
            - name: HOME
              value: /home-ollama
            - name: OLLAMA_KEEP_ALIVE
              value: 30m
          resources:
            requests:
              cpu: "${LAB_OLLAMA_CPU_REQ}"
              memory: "${LAB_OLLAMA_MEM_REQ}"
            limits:
              cpu: "${LAB_OLLAMA_CPU_LIM}"
              memory: "${LAB_OLLAMA_MEM_LIM}"
          readinessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 30
            periodSeconds: 20
            failureThreshold: 6
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            runAsNonRoot: true
            seccompProfile:
              type: RuntimeDefault
          volumeMounts:
            - name: models
              mountPath: /models
            - name: home
              mountPath: /home-ollama
      volumes:
        - name: models
          persistentVolumeClaim:
            claimName: ollama-models
        - name: home
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: ollama
  labels:
    app.kubernetes.io/part-of: agentic-labs
    app: ollama
spec:
  selector:
    app: ollama
  ports:
    - name: http
      port: 11434
      targetPort: http
