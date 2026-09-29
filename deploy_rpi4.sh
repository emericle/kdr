#!/usr/bin/env bash
# ==============================================================================
# KDR Deployment Script for Raspberry Pi 4 (rpi4)
# Deploys frontend to /var/www/html/kdr and backend service connecting to
# PostgreSQL on pinky.local.
# ==============================================================================

set -euo pipefail

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
TARGET_HOST="${TARGET_HOST:-rpi4}"
TARGET_USER="${TARGET_USER:-emericle}"
WEB_ROOT="/var/www/html"
FRONTEND_DIR="${WEB_ROOT}/kdr"
BACKEND_DIR="${FRONTEND_DIR}/backend"
SERVICE_NAME="kdr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║            KDR Deployment to Raspberry Pi 4             ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo -e "Target Host:       ${GREEN}${TARGET_HOST}${NC}"
echo -e "Frontend Target:   ${GREEN}${FRONTEND_DIR}${NC}"
echo -e "Backend Target:    ${GREEN}${BACKEND_DIR}${NC}"
echo -e "Database Target:   ${GREEN}pinky.local${NC}"
echo ""

# ------------------------------------------------------------------------------
# 1. Preflight Checks
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[1/7]${NC} Checking local prerequisites and target host connectivity..."

if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    echo -e "${RED}Error: .env file not found in ${SCRIPT_DIR}.${NC}"
    exit 1
fi

if ! grep -q "pinky.local" "${SCRIPT_DIR}/.env"; then
    echo -e "${YELLOW}Warning: DATABASE_URL does not explicitly reference pinky.local.${NC}"
else
    echo -e "${GREEN}✓ Verified DATABASE_URL points to pinky.local${NC}"
fi

if ! ssh -o ConnectTimeout=8 "${TARGET_HOST}" "echo 'SSH Connected'" > /dev/null 2>&1; then
    echo -e "${RED}Error: Cannot connect to ${TARGET_HOST} via SSH.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ SSH connection to ${TARGET_HOST} verified${NC}"

# ------------------------------------------------------------------------------
# 2. Remote Directory Preparation
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[2/7]${NC} Preparing remote directories on ${TARGET_HOST}..."

ssh "${TARGET_HOST}" "bash -s" <<REMOTE_MKDIR
sudo mkdir -p "${FRONTEND_DIR}" "${BACKEND_DIR}"
sudo chown -R ${TARGET_USER}:${TARGET_USER} "${FRONTEND_DIR}"
REMOTE_MKDIR

echo -e "${GREEN}✓ Remote directories created with correct ownership${NC}"

# ------------------------------------------------------------------------------
# 3. Deploy Frontend Assets
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[3/7]${NC} Deploying frontend to ${FRONTEND_DIR}..."

# Copy dashboard.html as index.html
scp "${SCRIPT_DIR}/src/static/dashboard.html" "${TARGET_HOST}:${FRONTEND_DIR}/index.html"

# Copy any additional static assets if present
if [ -d "${SCRIPT_DIR}/src/static" ]; then
    rsync -avz --exclude="dashboard.html" "${SCRIPT_DIR}/src/static/" "${TARGET_HOST}:${FRONTEND_DIR}/" || true
fi

echo -e "${GREEN}✓ Frontend deployed to ${FRONTEND_DIR}/index.html${NC}"

# ------------------------------------------------------------------------------
# 4. Deploy Backend Application & Data
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[4/7]${NC} Deploying backend to ${BACKEND_DIR}..."

rsync -avz --delete \
    --exclude=".venv" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".pytest_cache" \
    --exclude="tests" \
    --exclude="logs/*.log" \
    --exclude=".git" \
    "${SCRIPT_DIR}/src" \
    "${SCRIPT_DIR}/requirements.txt" \
    "${SCRIPT_DIR}/start.sh" \
    "${SCRIPT_DIR}/daemon.sh" \
    "${TARGET_HOST}:${BACKEND_DIR}/"

# Sync data directory if it exists (for cache persistence)
if [ -d "${SCRIPT_DIR}/data" ]; then
    rsync -avz "${SCRIPT_DIR}/data/" "${TARGET_HOST}:${BACKEND_DIR}/data/"
fi

# Securely copy .env with database credentials pointing to pinky.local
scp "${SCRIPT_DIR}/.env" "${TARGET_HOST}:${BACKEND_DIR}/.env"

echo -e "${GREEN}✓ Backend code and configuration synced${NC}"

# ------------------------------------------------------------------------------
# 5. Remote Virtual Environment & Dependencies
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[5/7]${NC} Setting up Python environment on ${TARGET_HOST}..."

ssh "${TARGET_HOST}" "bash -s" <<'REMOTE_SETUP'
set -euo pipefail
cd /var/www/html/kdr/backend

# Detect Python version (3.14, 3.13, or python3)
PYTHON_BIN=""
if command -v python3.14 &> /dev/null; then
    PYTHON_BIN="python3.14"
elif command -v python3.13 &> /dev/null; then
    PYTHON_BIN="python3.13"
elif command -v python3 &> /dev/null; then
    PYTHON_BIN="python3"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "Error: Python 3 not found on target host."
    exit 1
fi

echo "Using Python binary: $PYTHON_BIN"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment at .venv..."
    $PYTHON_BIN -m venv .venv
fi

echo "Installing/updating dependencies..."
.venv/bin/pip install --upgrade pip > /dev/null 2>&1
.venv/bin/pip install -r requirements.txt
REMOTE_SETUP

echo -e "${GREEN}✓ Python dependencies installed in virtual environment${NC}"

# ------------------------------------------------------------------------------
# 6. Configure Systemd Service & Nginx
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[6/7]${NC} Configuring systemd service and Nginx reverse proxy..."

ssh "${TARGET_HOST}" "bash -s" <<'REMOTE_SERVICES'
set -euo pipefail

# 1. Systemd Service Definition
sudo tee /etc/systemd/system/kdr.service > /dev/null <<'EOF'
[Unit]
Description=KDR Financial Market Trading Ingestion & Dashboard
After=network.target

[Service]
Type=simple
User=emericle
WorkingDirectory=/var/www/html/kdr/backend
EnvironmentFile=/var/www/html/kdr/backend/.env
ExecStart=/var/www/html/kdr/backend/.venv/bin/python src/scraper.py --debug
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable kdr.service
sudo systemctl restart kdr.service

# 2. Configure Nginx Reverse Proxy for /kdr, /kdr/api/, and /kdr/ws
NGINX_DEFAULT="/etc/nginx/sites-available/default"

if ! grep -q "location /kdr" "$NGINX_DEFAULT"; then
    echo "Adding KDR routes to Nginx configuration..."
    
    # Insert KDR location blocks right before "location /spelr" or before "location /api"
    sudo python3 - <<'PY_SCRIPT'
import sys

nginx_conf = "/etc/nginx/sites-available/default"
with open(nginx_conf, "r") as f:
    content = f.read()

kdr_block = """
\t# KDR Financial Market Dashboard & Proxy
\tlocation /kdr {
\t\ttry_files $uri $uri/ /kdr/index.html;
\t}

\tlocation /kdr/api/ {
\t\tproxy_pass http://127.0.0.1:8001/api/;
\t\tproxy_set_header Host $host;
\t\tproxy_set_header X-Real-IP $remote_addr;
\t\tproxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
\t\tproxy_set_header X-Forwarded-Proto $scheme;
\t}

\tlocation /kdr/ws {
\t\tproxy_pass http://127.0.0.1:8001/ws;
\t\tproxy_http_version 1.1;
\t\tproxy_set_header Upgrade $http_upgrade;
\t\tproxy_set_header Connection "Upgrade";
\t\tproxy_set_header Host $host;
\t\tproxy_set_header X-Real-IP $remote_addr;
\t\tproxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
\t}
"""

if "location /kdr" not in content:
    target = "location /spelr"
    if target in content:
        content = content.replace(target, kdr_block.strip("\n") + "\n\n\t" + target, 1)
    else:
        # Fallback before "location /api"
        target = "location /api"
        content = content.replace(target, kdr_block.strip("\n") + "\n\n\t" + target, 1)

    with open(nginx_conf, "w") as f:
        f.write(content)
print("Updated nginx configuration successfully.")
PY_SCRIPT
fi

# Test and reload Nginx
sudo nginx -t
sudo systemctl reload nginx
REMOTE_SERVICES

echo -e "${GREEN}✓ Systemd service running and Nginx reloaded${NC}"

# ------------------------------------------------------------------------------
# 7. Validation & Health Checks
# ------------------------------------------------------------------------------
echo -e "${YELLOW}[7/7]${NC} Verifying deployment health..."

ssh "${TARGET_HOST}" "bash -s" <<'REMOTE_TEST'
set -euo pipefail

echo "Waiting for kdr.service to become active..."
COUNT=0
while [ $COUNT -lt 25 ]; do
    if sudo systemctl is-active --quiet kdr.service; then
        echo "✓ kdr.service is active"
        break
    fi
    sleep 1
    COUNT=$((COUNT + 1))
done

if ! sudo systemctl is-active --quiet kdr.service; then
    echo "Error: kdr.service failed to become active. Recent logs:"
    sudo journalctl -u kdr.service -n 30 --no-pager
    exit 1
fi

# Give backend a moment to bind port 8001
COUNT=0
while [ $COUNT -lt 15 ]; do
    if curl -s http://127.0.0.1:8001/api/status | grep -q "online"; then
        echo "✓ Backend port 8001 status OK"
        break
    fi
    sleep 1
    COUNT=$((COUNT + 1))
done

echo "Checking Nginx frontend route (/kdr/)..."
curl -sI http://127.0.0.1/kdr/ | grep -q "200 OK" && echo "✓ Frontend route OK"

echo "Checking Nginx API proxy (/kdr/api/market-indexes)..."
curl -s http://127.0.0.1/kdr/api/market-indexes | grep -q "VIX" && echo "✓ Nginx API proxy OK"
REMOTE_TEST

# Test directly from deployment host
echo ""
echo -e "${GREEN}Testing remote endpoints from current machine:${NC}"
curl -sI "http://${TARGET_HOST}/kdr/" | head -n 1
curl -s "http://${TARGET_HOST}/kdr/api/market-indexes" | head -c 120
echo "..."

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Deployment Successful! System is LIVE!          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo -e "Dashboard URL: ${CYAN}http://${TARGET_HOST}/kdr/${NC}"
echo -e "Direct URL:    ${CYAN}http://${TARGET_HOST}:8001/${NC}"
echo ""
