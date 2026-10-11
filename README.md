# Argo Workflows CI/CD Lab

A deliberately small, end-to-end Kubernetes CI/CD lab:

- **Frontend:** static HTML served by Nginx.
- **Backend:** FastAPI with `GET /version`, `GET /notes`, and `POST /notes`.
- **Database:** PostgreSQL with a persistent volume claim.
- **Build/publish:** Buildah builds two OCI images and pushes them to GHCR.
- **Pipeline:** Argo Workflows runs backend tests, builds both images in parallel, then deploys the Helm chart.

This is a learning setup for a single-node k3s cluster, not a production deployment. In particular, the image-build task runs a privileged Buildah container, and the workflow service account can modify resources in the `demo` namespace. Keep this confined to your lab cluster.

## Repository map

```text
backend/                  FastAPI application and tests
frontend/                 HTML UI and Dockerfile
charts/demo/              Helm chart for frontend, backend, PostgreSQL, and Nginx config
workflows/                Argo WorkflowTemplate and deployment RBAC
scripts/                  Local helper scripts
```

## How the Argo DAG runs

```text
                 ┌──────────────┐
                 │  test-backend│
                 └──────┬───────┘
                        │
               ┌────────┴────────┐
               ▼                 ▼
      ┌────────────────┐ ┌────────────────┐
      │ build-backend  │ │ build-frontend │
      └────────┬───────┘ └────────┬───────┘
               └────────┬─────────┘
                        ▼
                 ┌──────────────┐
                 │    deploy    │
                 └──────────────┘
```

Argo concepts exercised here:

- `WorkflowTemplate`: a reusable, cluster-stored workflow definition.
- `Workflow`: a specific execution created when the template is submitted.
- `templates`: reusable task definitions; `build-image` is parameterized and invoked twice.
- `dag`: expresses dependencies and lets both image builds run in parallel.
- `inputs.parameters` / `workflow.parameters`: pass repository, image, and tag values through the graph.
- Kubernetes `ServiceAccount`, `Role`, and `RoleBinding`: limit deployment permissions to the `demo` namespace.

The workflow is submitted manually at first. Argo Workflows executes the pipeline; a source-control webhook trigger is a separate integration (usually Argo Events or a GitHub Actions webhook). Start by proving the workflow before adding an event trigger.

## 0. Prerequisites

Your k3s node is already up. Confirm the active context is the expected local cluster:

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl get storageclass
```

The standard k3s `local-path` StorageClass should normally be available for the PostgreSQL PVC.

### GitHub and GHCR setup

1. Push this repository to `https://github.com/yash-baviskar-dsw/argo-workflow-pipelines`.
2. For this first run, make the source repository **public** so workflow pods can clone it without adding a second credential. Do not put secrets in the repository.
3. Create a **GitHub classic personal access token** with `write:packages` and `read:packages` for GHCR. Keep it out of shell history and source control.
4. GHCR packages are commonly private initially. A pull secret is included below, so the first run can deploy private packages too. You can change the packages to public later if desired.

If Git is prompting for a GitHub username on HTTPS pushes, one option is to authenticate once with GitHub CLI rather than entering credentials every push:

```bash
sudo dnf install -y gh
gh auth login -h github.com
gh auth setup-git
git remote -v
git push -u origin main
```

Complete the browser/device login when `gh` asks. If you already use SSH keys, you can instead change `origin` to the repository's SSH URL.

## 1. Install Argo Workflows

Use a pinned release instead of an unpinned `latest` URL. The commands below use Argo Workflows `v4.1.2`:

```bash
export ARGO_WORKFLOWS_VERSION=v4.1.2
kubectl create namespace argo
kubectl apply --server-side -n argo \
  -f "https://github.com/argoproj/argo-workflows/releases/download/${ARGO_WORKFLOWS_VERSION}/quick-start-minimal.yaml"

kubectl get pods -n argo -w
```

Wait until the controller and server pods are ready, then install the CLI on Fedora:

```bash
cd /tmp
curl -fsSLO "https://github.com/argoproj/argo-workflows/releases/download/${ARGO_WORKFLOWS_VERSION}/argo-linux-amd64.gz"
gunzip -f argo-linux-amd64.gz
chmod +x argo-linux-amd64
sudo install -m 0755 argo-linux-amd64 /usr/local/bin/argo
argo version
```

Smoke-test Argo before introducing your pipeline:

```bash
argo submit -n argo --watch \
  https://raw.githubusercontent.com/argoproj/argo-workflows/v4.1.2/examples/hello-world.yaml
argo list -n argo
```

For the UI, run this in a separate terminal and keep it open:

```bash
kubectl -n argo port-forward service/argo-server 2746:2746
```

Then open **https://localhost:2746** and approve the local/self-signed certificate warning.

> The Argo quick-start install is intended for development/testing, not production.

## 2. Create the app namespace and required secrets

Create the namespace first because the Role and RoleBinding live in it:

```bash
kubectl create namespace demo
```

Create one GHCR token secret in `argo` for image pushes, and another in `demo` for pulling the resulting images. Use the same PAT, but do not paste the token into a checked-in file.

```bash
read -rsp 'GHCR token (write:packages/read:packages): ' GHCR_TOKEN; echo
kubectl create secret docker-registry ghcr-push-secret \
  -n argo --docker-server=ghcr.io \
  --docker-username=yash-baviskar-dsw \
  --docker-password="$GHCR_TOKEN"
kubectl create secret docker-registry ghcr-pull-secret \
  -n demo --docker-server=ghcr.io \
  --docker-username=yash-baviskar-dsw \
  --docker-password="$GHCR_TOKEN"
unset GHCR_TOKEN
```

Set a local demo-only PostgreSQL password. It is stored as a Kubernetes Secret, not as a value in Git:

```bash
read -rsp 'Demo PostgreSQL password: ' DB_PASSWORD; echo
kubectl create secret generic demo-postgres-secret -n demo \
  --from-literal=password="$DB_PASSWORD"
unset DB_PASSWORD
```

For a disposable lab, choose a password that is safe to lose. Do not reuse a real account password.

## 3. Apply the workflow's limited deployment RBAC

From the repository root:

```bash
kubectl apply -f workflows/rbac.yaml
kubectl get serviceaccount pipeline-runner -n argo
kubectl get role,rolebinding -n demo
```

The workflow runs in `argo`, but its `pipeline-runner` ServiceAccount receives a RoleBinding only in `demo`. It does not need cluster-admin.

## 4. Push the application source to GitHub

From the repository root:

```bash
git add .
git commit -m "Add Argo Workflows CI/CD lab"
git branch -M main
git push -u origin main
```

If your local repository already has a commit and/or remote, don't re-add the remote; check with `git status` and `git remote -v` first.

The repo URL configured in `workflows/ci-cd-workflowtemplate.yaml` is:

```text
https://github.com/yash-baviskar-dsw/argo-workflow-pipelines.git
```

Edit the template if your actual repository name differs.

## 5. Install the WorkflowTemplate

```bash
kubectl apply -n argo -f workflows/ci-cd-workflowtemplate.yaml
kubectl get workflowtemplate -n argo
```

This creates a reusable definition; it does **not** run a pipeline yet.

## 6. Run the pipeline

Run this from the local repository root, after the commit is pushed:

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)"
argo submit -n argo --from workflowtemplate/ci-cd-pipeline \
  -p repo-url=https://github.com/yash-baviskar-dsw/argo-workflow-pipelines.git \
  -p git-revision=main \
  -p image-tag="$IMAGE_TAG" \
  --watch
```

What happens:

1. `test-backend` clones the source and runs the FastAPI tests using SQLite, so it doesn't depend on the deployed PostgreSQL instance.
2. `build-backend` and `build-frontend` both wait for tests to pass, then run in parallel. Buildah builds and pushes the two images to GHCR using `ghcr-push-secret`.
3. `deploy` waits for both builds, checks out the chart, creates a short-lived in-pod kubeconfig from the workflow Pod's ServiceAccount token, then runs `helm upgrade --install` into `demo`.

The workflow should finish `Succeeded`. If it doesn't, inspect the first failed task before changing YAML:

```bash
argo list -n argo
argo get -n argo @latest
argo logs -n argo @latest
kubectl get pods -n argo
kubectl get events -n argo --sort-by=.lastTimestamp | tail -25
```

To inspect task-level pod logs, find the failed pod with `kubectl get pods -n argo` and run `kubectl logs -n argo <pod-name> -c main`.

## 7. Verify the deployment

```bash
kubectl get pods,svc,pvc -n demo
kubectl rollout status deployment/demo-postgres -n demo --timeout=180s
kubectl rollout status deployment/demo-backend -n demo --timeout=180s
kubectl rollout status deployment/demo-frontend -n demo --timeout=180s
```

Reach the frontend with port-forward (works even if NodePort routing is inconvenient):

```bash
kubectl -n demo port-forward service/demo-frontend 8080:80
```

Open **http://localhost:8080**. The page calls `/api/version`, lists saved notes, and posts new notes. Nginx proxies `/api/*` to the backend Service; the backend stores notes in PostgreSQL.

You can also check the API directly:

```bash
curl -s http://localhost:8080/api/version | python -m json.tool
curl -s http://localhost:8080/api/notes | python -m json.tool
curl -s -X POST http://localhost:8080/api/notes \
  -H 'Content-Type: application/json' \
  -d '{"text":"First note from the Argo workflow lab"}' | python -m json.tool
curl -s http://localhost:8080/api/notes | python -m json.tool
```

## 8. Validate and debug

```bash
# Application tests
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -v
cd ..

# Helm syntax/render validation (requires Helm CLI)
helm lint charts/demo
helm template demo charts/demo \
  --set-string imageTag=local-test \
  --set backend.image.repository=ghcr.io/yash-baviskar-dsw/argo-workflow-pipelines-backend \
  --set frontend.image.repository=ghcr.io/yash-baviskar-dsw/argo-workflow-pipelines-frontend

# Kubernetes state
argo list -n argo
kubectl describe pod -n demo -l app.kubernetes.io/instance=demo
kubectl logs -n demo deployment/demo-backend
kubectl logs -n demo deployment/demo-postgres
```

If an image pull fails, inspect `kubectl describe pod -n demo <pod-name>` and verify `ghcr-pull-secret` exists. If the PostgreSQL PVC stays Pending, check `kubectl get storageclass` and `kubectl describe pvc -n demo`.

## Important limitations

- This is a manual-submit CI/CD exercise, not yet push-triggered automation.
- The Buildah build pod is privileged to keep image building understandable on a single-node lab. Do not copy that permission into a shared/production cluster without redesigning the build isolation.
- PostgreSQL credentials are supplied via an existing Secret; no credential is checked into the chart.
- The sample app has no authentication, and the PostgreSQL PVC is local to the k3s node.
