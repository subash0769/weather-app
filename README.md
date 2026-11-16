# Weather & Time App: GitOps Walkthrough

A tiny Flask app that shows the current time and weather for a city (Open-Meteo, no API key).
Used as the sample workload for: Docker -> Docker Hub -> Kubernetes -> Gateway API + Traefik -> Helm -> Argo CD -> GitHub Actions.

- App listens on port **8000**
- Health endpoint: `/healthz` (use for liveness/readiness probes)
- JSON endpoint: `/api/weather?city=Pokhara`

Suggested repos:
- `weather-app` (app code + Dockerfile + `.github/workflows/cicd.yml`)
- `weather-infra` (manifests + Helm chart; Argo CD watches this one)

---

## 0. Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py            # http://localhost:8000
```

Production command (put in your Dockerfile `CMD`):
```
gunicorn -b 0.0.0.0:8000 app:app
```

---

## 1. Docker

Write your Dockerfile, then:

```bash
docker build -t <dockerhub-user>/weather-app:v1 .
docker run -p 8000:8000 <dockerhub-user>/weather-app:v1
docker login
docker push <dockerhub-user>/weather-app:v1
```

---

## 2. Plain Kubernetes manifests (write these yourself)

Create `k8s/deployment.yaml` and `k8s/service.yaml`:

- Deployment: image `<dockerhub-user>/weather-app:v1`, `containerPort: 8000`, replicas 2
- Service: `port: 80` -> `targetPort: 8000`
- Probes: `httpGet: { path: /healthz, port: 8000 }` for both liveness and readiness

```bash
kubectl apply -f k8s/
```

---

## 3. Install Gateway API CRDs

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml
kubectl get crd | grep gateway
```

(Check the Gateway API releases page for the newest version; Traefik's chart docs list which version it expects.)

---

## 4. Install Helm (if missing)

```bash
helm version || curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

---

## 5. Install Traefik with Helm (Gateway provider enabled)

```bash
helm repo add traefik https://traefik.github.io/charts
helm repo update

helm install traefik traefik/traefik \
  --namespace traefik --create-namespace \
  --set providers.kubernetesGateway.enabled=true
```

Check the GatewayClass and Gateway are accepted:

```bash
kubectl get gatewayclass
kubectl get gateway -A
# Look for ACCEPTED/PROGRAMMED = True
```

The chart creates a Gateway named **traefik-gateway** in namespace **traefik**.

---

## 6. Create the HTTPRoute

`k8s/http-route.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: weather-app
  namespace: default
spec:
  parentRefs:
    - name: traefik-gateway
      namespace: traefik
  hostnames:
    - weather.local          # change to your hostname
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: weather-app   # your Service name
          port: 80
```

```bash
kubectl apply -f k8s/http-route.yaml
kubectl get httproute
```

The route is in `default` but the Gateway is in `traefik`. By default the Gateway only allows routes from the **same** namespace, so the route won't attach yet. Fix it next.

### Edit the Gateway: allow routes from all namespaces

```bash
kubectl edit gateway traefik-gateway -n traefik
```

Change:
```yaml
allowedRoutes:
  namespaces:
    from: Same
```
to:
```yaml
allowedRoutes:
  namespaces:
    from: All
```

Delete and re-apply the route so it re-attaches:

```bash
kubectl delete -f k8s/http-route.yaml
kubectl apply  -f k8s/http-route.yaml
kubectl describe httproute weather-app     # Accepted: True
```

Test (get Traefik's external IP / NodePort first):
```bash
kubectl get svc -n traefik
kubectl patch svc traefik -n traefik -p '{"spec":{"type":"NodePort"}}' # to attach nodeport to the traefik

curl -H "Host: weather.local" http://<traefik-ip>/
```

---

## 7. Helm chart for the app (we write values, not templates from scratch)

```bash
helm create weather-app
```

Edit `weather-app/values.yaml`:

```yaml
replicaCount: 2

image:
  repository: <dockerhub-user>/weather-app
  tag: "v1"
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80
  targetPort: 8000

httpRoute:
  enabled: true
  parentRefs:
    - name: traefik-gateway
      namespace: traefik
  hostnames:
    - weather.local
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: weather-app
          port: 80

livenessProbe:
  httpGet:
    path: /healthz
    port: 8000
readinessProbe:
  httpGet:
    path: /healthz
    port: 8000
```

Notes:
- `helm create` generates an `httpRoute` block in recent Helm versions (3.17+). If yours doesn't, add `templates/httproute.yaml` yourself.
- In `templates/deployment.yaml` make sure `containerPort` uses the target port (8000), and the probes point at port 8000.
- Verify before committing:

```bash
helm lint weather-app
helm template weather-app ./weather-app
helm install weather-app ./weather-app   # optional local test
```

---

## 8. Argo CD

Use the namespace `argocd`. The official install manifest hardcodes this namespace in its ClusterRoleBindings, so a different name like `argo-cd` needs extra patching.

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

Run the Argo CD server in HTTP mode, since Traefik terminates traffic on the `web` entrypoint:

```bash
kubectl patch configmap argocd-cmd-params-cm -n argocd \
  --type merge -p '{"data":{"server.insecure":"true"}}'
kubectl rollout restart deployment argocd-server -n argocd
```

### Expose with a normal Ingress

`argocd-ingress.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd
  namespace: argocd
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  ingressClassName: traefik
  rules:
    - host: argocd.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: argocd-server
                port:
                  number: 80
```

Traefik's Ingress provider is on by default in the chart. Apply:

```bash
kubectl apply -f argocd-ingress.yaml
```

Add `argocd.local` and `weather.local` to your `/etc/hosts`, pointing at the Traefik IP.

### First login

```bash
argocd admin initial-password -n argocd
# or
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

kubectl port-forward -n traefik svc/traefik 8080:80  # we are port forwarding the traefik service

Open `http://argocd.local`, log in as `admin`, then change the password: **User Info -> Update Password**.

### Connect the repo and create the application (UI)

1. **Settings -> Repositories -> Connect Repo**: HTTPS, your `weather-infra` URL (add username + PAT if private).
2. **Applications -> New App**:
   - Name: `weather-app`
   - Sync policy: **Automatic**; sync options: tick **Auto-Create Namespace**
   - Source: repo URL, revision `main`, path to the Helm chart (e.g. `weather-app`)
   - Destination: `https://kubernetes.default.svc`, namespace `default`
3. Create, then Sync.

---

## 9. GitHub Actions CI/CD

### Secrets (repo -> Settings -> Secrets and variables -> Actions -> New repository secret)

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | your Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |
| `INFRA_REPO_TOKEN` | GitHub PAT (classic) with `repo` scope |

Create the PAT: GitHub -> Settings -> Developer settings -> Personal access tokens -> Tokens (classic) -> Generate new token (classic) -> tick `repo`.

### `.github/workflows/cicd.yml` (in the app repo)

```yaml
name: cicd

on:
  push:
    branches: [main]

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    outputs:
      short_sha: ${{ steps.vars.outputs.short_sha }}
    steps:
      - uses: actions/checkout@v4

      - name: Set short SHA
        id: vars
        run: echo "short_sha=$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ${{ secrets.DOCKERHUB_USERNAME }}/weather-app:${{ steps.vars.outputs.short_sha }}
            ${{ secrets.DOCKERHUB_USERNAME }}/weather-app:latest

  update-manifest:
    needs: build-and-push
    runs-on: ubuntu-latest
    steps:
      - name: Checkout infra repo
        uses: actions/checkout@v4
        with:
          repository: <github-user>/weather-infra
          token: ${{ secrets.INFRA_REPO_TOKEN }}
          ref: main

      - name: Update image tag in values.yaml
        run: |
          sed -i 's|^\(\s*tag:\s*\).*|\1"${{ needs.build-and-push.outputs.short_sha }}"|' weather-app/values.yaml

      - name: Commit and push
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add weather-app/values.yaml
          git commit -m "ci: update image tag to ${{ needs.build-and-push.outputs.short_sha }}" || echo "No changes"
          git push
```

What happens end to end:
1. Push to `main` in the app repo.
2. Job 1 builds the image, tags it with the short SHA, and pushes to Docker Hub.
3. Job 2 updates `tag:` in the infra repo's `values.yaml` using the infra PAT.
4. Argo CD sees the commit, auto-syncs, and rolls out the new image.

Make sure the `tag:` line in `values.yaml` is the only line matching `tag:`, or tighten the `sed` pattern.

---

## Quick troubleshooting

| Symptom | Check |
|---|---|
| Gateway not `Accepted` | CRDs installed? `providers.kubernetesGateway.enabled=true`? |
| HTTPRoute not attaching | `allowedRoutes.namespaces.from` set to `All`; parentRef name/namespace correct |
| 404 from Traefik | Host header matches `hostnames`; Service name/port correct |
| Argo CD UI redirect loop | `server.insecure: "true"` set and server restarted |
| Pods not ready | Probes hit `/healthz` on port 8000 |
# weather-app
