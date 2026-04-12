FROM kalilinux/kali-rolling

ENV DEBIAN_FRONTEND=noninteractive
ARG INCLUDE_METASPLOIT=false
ARG PIP_CONSTRAINTS=""

# [FIX BAD MIRROR] Use a single, reliable Kali mirror to avoid flaky mirror redirects during apt operations
RUN printf 'deb http://kali.download/kali kali-rolling main contrib non-free non-free-firmware\n' > /etc/apt/sources.list

# Fix Kali GPG Keys
RUN apt-get update --allow-insecure-repositories && \
    apt-get install -y --no-install-recommends --allow-unauthenticated kali-archive-keyring && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Core system + Python
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        build-essential make autoconf automake libtool pkg-config \
        curl wget git ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install kali-linux-headless (brings most pentesting tools + dependencies)
# This includes: nmap, nikto, dirb, gobuster, sqlmap, netcat, ssh, etc.
RUN apt-get update && \
    echo "console-setup console-setup/variant select Latin1 and Latin5 - western Europe and Turkic languages" | debconf-set-selections && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        kali-linux-headless \
        seclists \
        burpsuite \
        default-jre \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Metasploit: optional install via build-arg to avoid pulling heavy tooling
# by default. Set build arg `INCLUDE_METASPLOIT=true` to install the
# distribution package during image build.
RUN if [ "$INCLUDE_METASPLOIT" = "true" ]; then \
            apt-get update && \
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends metasploit-framework && \
            apt-get clean && rm -rf /var/lib/apt/lists/*; \
        else \
            echo "Skipping metasploit-framework (INCLUDE_METASPLOIT!=true)"; \
        fi

# libnss-wrapper: lets CAI's virtual-hosts feature (dns_proxy) inject custom
# hostname/IP mappings into nmap, curl, hydra, etc. via LD_PRELOAD.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libnss-wrapper && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# FIX: pip system-packages warning
RUN mkdir -p /root/.pip && \
    echo "[global]" > /root/.pip/pip.conf && \
    echo "break-system-packages = true" >> /root/.pip/pip.conf



# Work directory
WORKDIR /opt/cai

# Copy minimal dependency descriptor first so Docker can cache layers when
# dependencies (pyproject.toml) don't change. This helps speed rebuilds.
COPY pyproject.toml /opt/cai/

# Create isolated virtualenv and pre-install build tools (cached layer).
RUN python3 -m venv /opt/venv && \
        /opt/venv/bin/pip install --upgrade pip setuptools wheel build && \
        rm -rf /root/.cache/pip

# Copy full repository into image (invalidate above layer only when source
# files change).
COPY . /opt/cai

# Install CAI and optional extras. If a constraints/requirements file is
# provided via the build-arg `PIP_CONSTRAINTS` (path relative to repo root),
# install via that file to enable reproducible installs. Otherwise install
# the package from source into the virtualenv.
RUN if [ -n "$PIP_CONSTRAINTS" ] && [ -f "/opt/cai/$PIP_CONSTRAINTS" ]; then \
            /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
            /opt/venv/bin/pip install -r /opt/cai/$PIP_CONSTRAINTS ; \
        else \
            /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
            /opt/venv/bin/pip install "/opt/cai[rag,tui]" ; \
        fi && rm -rf /root/.cache/pip

# Ensure the virtualenv's bin directory is first in PATH so `cai` resolves
# to the venv-installed console script.
ENV PATH="/opt/venv/bin:${PATH}"

# .env Template (overwritten if .env file present in logs)
RUN echo 'OPENAI_API_KEY="sk-1234"' > /opt/cai/.env && \
    echo 'ANTHROPIC_API_KEY=""' >> /opt/cai/.env && \
    echo 'DEEPSEEK_API_KEY=""' >> /opt/cai/.env && \
    echo 'OLLAMA_API_BASE=""' >> /opt/cai/.env && \
    echo 'PROMPT_TOOLKIT_NO_CPR=1' >> /opt/cai/.env && \
    echo 'CAI_STREAM=false' >> /opt/cai/.env

#  logs directory
RUN mkdir -p /opt/cai/logs

# Startup Script
RUN cat > /opt/cai/start.sh <<'SH'
#!/bin/bash
# ── Resolve target UID/GID for file-ownership fix ──────────────────────────
# The container runs as root (required for nmap, openvpn, masscan raw sockets).
# PUID/PGID are passed from docker-compose so that every file written to the
# bind-mounted volumes (logs/, workspaces/, intelligence.json) gets chowned
# back to the host user on startup AND on clean exit.
_PUID="${PUID:-1000}"
_PGID="${PGID:-1000}"

# chown the mount-points now so the host can read any files from prior runs
chown -R "${_PUID}:${_PGID}" /opt/cai/logs /opt/cai/workspaces 2>/dev/null || true
# tui_config.json might be a single bind-mount file
chown "${_PUID}:${_PGID}" /opt/cai/tui_config.json 2>/dev/null || true

# On exit, chown everything back so the host user is never locked out
trap '
    chown -R "'"${_PUID}:${_PGID}"'" /opt/cai/logs /opt/cai/workspaces 2>/dev/null || true
    chown "'"${_PUID}:${_PGID}"'" /opt/cai/tui_config.json 2>/dev/null || true
' EXIT

if [ -f /config/.env ]; then
    echo "Loading .env from /config/.env"
    cp /config/.env /opt/cai/.env
fi
# Ensure runtime dirs exist (workspaces/ may be gitignored on host)
mkdir -p /opt/cai/workspaces /opt/cai/logs
# tui_config.json: if the bind-mount created a directory (gitignored on host)
# replace it with a minimal valid JSON file so the TUI can write settings.
if [ -d /opt/cai/tui_config.json ]; then
    rmdir /opt/cai/tui_config.json 2>/dev/null && echo '{"tools":{"inject_mode":"command"}}' > /opt/cai/tui_config.json
fi
cd /opt/cai
# Run cai without exec so the EXIT trap fires when the session ends.
# (exec would replace the shell, discarding the trap.)
cai "$@"
SH

RUN chmod +x /opt/cai/start.sh

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import cai; print('OK')" || exit 1

ENTRYPOINT ["/opt/cai/start.sh"]
CMD []

# Expose textual-web default port so containers can publish the web TUI
ENV TEXTUAL_WEB_HOST=0.0.0.0
ENV TEXTUAL_WEB_PORT=8000
EXPOSE 8000