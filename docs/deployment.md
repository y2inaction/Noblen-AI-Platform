# Deployment (Ubuntu / AWS EC2)

Target: an Ubuntu EC2 instance running Docker + Docker Compose behind Nginx, with
HTTPS via Let's Encrypt. **Detailed rollout happens in Phase 11.**

## Golden rule: do not disturb other apps
The server may host other applications (e.g. CIDU). Therefore:
- Use an **isolated compose project name** (`noblen-ai-platform`) and a dedicated
  network/volumes (already configured in the compose files).
- Deploy into a dedicated directory, e.g. `/opt/noblen-ai-platform`.
- **Before changing anything**, inspect: `docker ps`, published ports, disk
  (`df -h`), memory (`free -m`), firewall/security groups.
- Never stop unrelated containers, delete unrelated volumes, or overwrite unrelated
  directories.

## Flow
```
GitHub → pull latest → docker compose build → docker compose up -d
```

```bash
# On the server, in the repo directory:
cp .env.example .env         # then fill in real secrets
./infra/scripts/deploy.sh    # builds & starts ONLY the Noblen project
```

`deploy.sh` builds and starts only the isolated Noblen stack and waits for the
backend health check. Migrations run automatically at container start
(`backend/docker/entrypoint.sh`) when `DATABASE_URL` points at PostgreSQL.

## HTTPS
Mount certificates into `infra/nginx/certs` and add a TLS `server` block (443) that
redirects 80 → 443. Automate issuance/renewal with Certbot/Let's Encrypt.

## Backups
```bash
./infra/scripts/backup.sh ./backups     # timestamped, gzipped pg_dump
```
Restore instructions are printed by the script. Schedule via cron and store backups
off-box. Configurable data-retention policies are planned (spec §43).

## Production compose
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
Postgres/Redis are not published to the host in production; only Nginx is public.
