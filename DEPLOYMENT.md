# Deployment

Owner-facing steps: [OWNER_SETUP.md](OWNER_SETUP.md). This page is the technical reference.

Primary deployment is **GitHub Actions** (see HOSTING.md): `forecast-validation.yml` runs now; `kalshi-paper.yml`
runs only after Kalshi authorization in a private repository. The Docker stack below is optional (an
always-on dashboard on a private server).

## Optional private server (any Ubuntu 24.04 VM: Google Cloud e2-micro, Oracle micro, home PC)

```bash
git clone https://github.com/devincombs2007-rgb/BreadMaker3000 && cd BreadMaker3000
bash deploy/install.sh          # Docker + compose, .env from template, safety check, start core + dashboard
bash deploy/tailscale.sh        # private HTTPS access from the iPhone
```
`deploy/cloud-init.yaml` does the same unattended at VM creation (credentials handled without logging -
see SECURITY.md). Measured idle memory: core ~73 MiB, dashboard ~77 MiB.

## Operations

```bash
sudo docker compose ps
sudo docker compose logs -f --tail 100 core
sudo docker compose exec core breadmaker kalshi-status
git pull && sudo docker compose up -d --build
```

## Backup & recovery

```bash
sudo docker run --rm -v breadmaker_bm-data:/data -v $PWD:/b alpine tar czf /b/bm-data-$(date +%F).tgz /data
```
Kalshi-derived data in this backup must stay private (Developer Agreement s3.1).
