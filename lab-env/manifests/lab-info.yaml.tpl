apiVersion: v1
kind: ConfigMap
metadata:
  name: lab-info
  labels:
    app.kubernetes.io/part-of: agentic-labs
data:
  lab: "${LAB_NAME}"
  started_iso: "${LAB_STARTED_ISO}"
  started_epoch: "${LAB_STARTED_EPOCH}"
