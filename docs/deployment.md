# Deployment & Infrastructure Guide

This guide documents the server configuration, Nginx reverse proxy, and Cloudflare Tunnel setup for deploying the KDR dashboard (e.g., on a Raspberry Pi or local server exposed via Cloudflare Tunnel).

---

## Application Architecture

- **KDR Backend**: Runs on port `8001` (FastAPI with WebSocket support).
- **KDR Static Frontend**: Vue 3 + Chart.js dashboard deployed at `/var/www/html/kdr` (accessed via `/kdr`).
- **Co-hosted Services**: Spelr backend runs on port `8080` (`/var/www/html/spelr`).
- **Reverse Proxy**: Nginx listening on port `80`.
- **Tunnel**: Cloudflare Tunnel routing traffic from the domain (e.g., `elcire.me`) to local Nginx.

---

## Nginx Configuration

In `/etc/nginx/sites-available/default`:

```nginx
# KDR Static Dashboard
location /kdr {
    alias /var/www/html/kdr;
    try_files $uri $uri/ /kdr/index.html;
}

# KDR REST API (proxied to port 8001, preserving /api/ prefix)
location /kdr/api/ {
    proxy_pass http://localhost:8001/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# KDR WebSocket (proxied to port 8001 with protocol upgrade)
location /kdr/ws {
    proxy_pass http://localhost:8001/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "Upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Validate and reload Nginx:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

The `location /kdr/ws` block upgrades the browser's WebSocket to the backend's
`/ws`. The live ticks for AAPL, BTC and TSLA travel over that socket. If the
upgrade is broken the REST checks still pass, so `bin/deploy_rpi4.sh` now opens
the proxied socket and reads the backend's init frame; a failure there fails the
deploy instead of shipping a dashboard that shows "Disconnected - reconnecting".

---

## Backend Logging

The systemd service sets `Environment=KDR_LOG_FILE=/var/log/kdr/backend.log`. The
pipeline's rotating file handler reads that variable, so every backend record —
including an Alpaca stream that cannot verify its TLS certificate — is written to
a file you can tail:

```bash
tail -f /var/log/kdr/backend.log
```

The Alpaca Trade Stream connects over TLS. certifi (in `requirements.txt`) ships
the CA bundle the stream verifies against; `src/stream_client.py` points the
process at it before the stream starts. A bare interpreter with no CA bundle
fails the handshake with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
certificate`, which leaves the market buffer empty and the charts at
"Waiting for Market Stream".

---

## Cloudflare Tunnel Configuration

When proxying local Nginx without a local TLS certificate, configure the tunnel ingress to use **HTTP** (port 80) rather than HTTPS to avoid TLS verification errors (`tls: failed to verify certificate... doesn't contain IP SANs`).

### 1. Tunnel Ingress (`/etc/cloudflared/config.yml`)

```yaml
ingress:
  - hostname: elcire.me
    service: http://192.168.1.6
  - hostname: www.elcire.me
    service: http://192.168.1.6
  - service: http://192.168.1.6  # Default fallback
```

### 2. Systemd Service (`/etc/systemd/system/cloudflared.service`)

Run the tunnel using the tunnel ID directly:

```ini
[Unit]
Description=Cloudflare Tunnel client
After=network-online.target
Wants=network-online.target

[Service]
TimeoutStartSec=15
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run 9340a2b8-de03-44bb-a1f3-1e1ead73aaf6 --config /etc/cloudflared/config.yml
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart cloudflared
```

---

## Verification & Health Checks

1. **Verify Backend**:
   ```bash
   curl http://localhost:8001/api/status
   curl http://localhost:8001/api/market-indexes
   ```

2. **Verify Nginx Proxy**:
   ```bash
   curl http://localhost/kdr/api/status
   curl http://localhost/kdr/
   ```

3. **Verify Public Tunnel Endpoints**:
   - `https://elcire.me/kdr/` (Dashboard frontend)
   - `https://elcire.me/kdr/api/market-indexes` (API)
   - WebSocket connection at `wss://elcire.me/kdr/ws`

4. **Monitor Tunnel Logs**:
   ```bash
   sudo journalctl -u cloudflared -f
   ```
