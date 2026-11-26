# Enterprise Deployment Guide — Harbor + Rancher (Kubernetes)

This guide covers building, pushing, and deploying **Planning Story (`v2.0.0`)** using **Harbor** (container registry) and **Rancher** (Kubernetes).

## Architecture in Production

- **Single-Origin Pod (`port 3000`):** The Node.js/Express Gateway listens on `0.0.0.0:3000`, handles Microsoft Entra ID (Azure AD) SSO, and proxies `/`, `/static/*`, and `/api/*` (including unbuffered SSE `/api/chat/stream`) to the internal Python FastAPI + DuckDB V2 backend on `127.0.0.1:8001`.
- **Non-Root Security:** Container runs as `UID 10001` (`appuser`), `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`.
- **Probes:**
  - `startupProbe` & `readinessProbe`: `GET /api/ready` (verifies BOTH Node Gateway and Python/DuckDB V2 views are healthy).
  - `livenessProbe`: `GET /api/health`.
- **Ingress SSE Support:** `deploy/rancher/ingress.yaml` disables proxy buffering (`proxy-buffering: "off"`), sets 300s stream timeouts, and enables cookie session affinity across replicas.

---

## Step 1 — Build & Push Image to Harbor

### Option A: Automated via GitHub Actions
Push to `main` or push a version tag (`git tag v2.0.1 && git push origin v2.0.1`).  
`.github/workflows/push-to-harbor.yml` runs JS syntax checks, builds root `Dockerfile`, and pushes:
- `amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:latest`
- `amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:<short-sha>`
- `amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:<git-tag>`

Configure repository secrets: `HARBOR_USERNAME`, `HARBOR_PASSWORD`.

### Option B: Manual Build & Push
```bash
docker login amr-registry-pre.caas.intel.com

docker build \
  -f Dockerfile \
  --build-arg HTTP_PROXY=http://proxy-dmz.intel.com:912 \
  --build-arg HTTPS_PROXY=http://proxy-dmz.intel.com:912 \
  --build-arg NO_PROXY=localhost,127.0.0.0/8,10.0.0.0/8,.intel.com \
  --build-arg NPM_REGISTRY=https://pixi.intel.com/ \
  -t amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:2.1.0 \
  -t amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:latest \
  .

docker push amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:2.0.0
docker push amr-registry-pre.caas.intel.com/if-pdh-ai-assist/planningstory:latest
```

---

## Step 2 — Create Harbor Registry & App Secrets in Rancher (`if-pdh-ai-assist`)

- **Harbor Project:** `https://amr-registry-pre.caas.intel.com/harbor/projects/125194/repositories`
- **Rancher Namespace:** `https://amr-pre.caas.intel.com/dashboard/c/c-skrnn/explorer/namespace/if-pdh-ai-assist`

1. **Create Harbor `imagePullSecret` (`harbor-regcred`) in `if-pdh-ai-assist`:**
   ```bash
   kubectl -n if-pdh-ai-assist create secret docker-registry harbor-regcred \
     --docker-server=amr-registry-pre.caas.intel.com \
     --docker-username=<HARBOR_ROBOT_OR_USER> \
     --docker-password=<HARBOR_PASSWORD>
   ```
2. **Load Entra ID & Runtime Secrets directly from your local `.env`:**
   ```bash
   kubectl -n if-pdh-ai-assist create secret generic planningstory-secrets --from-env-file=.env
   ```

---

## Step 3 — Update Hostname & Deploy in Rancher

1. Update `planningstory.intel.com` and `OLLAMA_BASE_URL` in:
   - `deploy/rancher/configmap.yaml` (`AZURE_REDIRECT_URI`, `OLLAMA_BASE_URL`)
   - `deploy/rancher/ingress.yaml` (`hosts`, `tls.secretName`)
2. Deploy via Kustomize (CLI or Rancher **Import YAML**):
   ```bash
   kubectl apply -k deploy/rancher/
   kubectl -n if-pdh-ai-assist rollout status deployment/planningstory
   ```

---

## Step 4 — Post-Deploy Verification

```bash
# Verify pod readiness & single-origin health
kubectl -n if-pdh-ai-assist get pods,svc,ingress
curl -fsS https://planningstory.intel.com/api/ready
curl -fsS https://planningstory.intel.com/api/v2/health
```

### Rollback (if needed)
```bash
kubectl -n if-pdh-ai-assist rollout undo deployment/planningstory
```
