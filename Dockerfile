# Imagem única: JDK embutido no produto Capella + Capella + plugin python4capella
# + Xvfb + servidor MCP. Ver docs/decisions/0003-empacotamento-docker.md.
FROM debian:bookworm-slim

ARG CAPELLA_VERSION=7.0.1
ARG CAPELLA_BUILD=7.0.1.202503211540
ARG PYTHON4CAPELLA_VERSION=1.4.1

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        libjavascriptcoregtk-4.0-18 libwebkit2gtk-4.0-37 \
        libxtst6 xdg-utils x11-xserver-utils xvfb xauth dbus-x11 \
        python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Capella (produto Linux x86_64, traz JRE embutido) — versão pinada por ser a
# exatamente testada pela release 1.4.1 do python4capella (report-7.0.1.xml).
RUN curl -fsSL \
        "https://download.eclipse.org/capella/core/products/releases/${CAPELLA_VERSION}/capella-${CAPELLA_BUILD}-linux-gtk-x86_64.tar.gz" \
        -o /tmp/capella.tar.gz \
    && mkdir -p /opt/capella \
    && tar -xzf /tmp/capella.tar.gz -C /opt/capella --strip-components=1 \
    && rm /tmp/capella.tar.gz

# Plugin python4capella, instalado via p2 director direto no update.zip (sem GUI,
# mas o director de um RCP ainda inicializa o SWT — precisa de Xvfb mesmo aqui).
RUN curl -fsSL \
        "https://github.com/labs4capella/python4capella/releases/download/${PYTHON4CAPELLA_VERSION}/org.eclipse.python4capella.update.zip" \
        -o /tmp/python4capella.zip \
    && xvfb-run -a /opt/capella/capella \
        -application org.eclipse.equinox.p2.director \
        -repository "jar:file:/tmp/python4capella.zip!/" \
        -installIU org.eclipse.python4capella.feature.feature.group,org.eclipse.python4capella.commandline.feature.feature.group \
        -destination /opt/capella \
        -profile DefaultProfile \
        -nosplash -consoleLog \
    && rm /tmp/python4capella.zip

ENV CAPELLA_HOME=/opt/capella
ENV PATH="/opt/capella:${PATH}"

# Servidor MCP (uv gerencia o venv Python do servidor — não é o Python do Capella)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

VOLUME /workspace/models

ENTRYPOINT ["uv", "run", "capella-mcp"]
