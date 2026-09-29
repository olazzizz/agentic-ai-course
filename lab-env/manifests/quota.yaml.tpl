apiVersion: v1
kind: ResourceQuota
metadata:
  name: lab-quota
  labels:
    app.kubernetes.io/part-of: agentic-labs
spec:
  hard:
    requests.cpu: "${LAB_QUOTA_CPU_REQ}"
    requests.memory: "${LAB_QUOTA_MEM_REQ}"
    limits.cpu: "${LAB_QUOTA_CPU_LIM}"
    limits.memory: "${LAB_QUOTA_MEM_LIM}"
    pods: "${LAB_QUOTA_PODS}"
    requests.storage: "${LAB_QUOTA_STORAGE}"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: lab-limits
  labels:
    app.kubernetes.io/part-of: agentic-labs
spec:
  limits:
    - type: Container
      defaultRequest:
        cpu: 100m
        memory: 128Mi
      default:
        cpu: 500m
        memory: 512Mi
