# Use the official Docker-in-Docker image as the base.
# This image includes the Docker daemon and all its dependencies.
FROM docker:25.0-dind

# Install system dependencies:
# - python3 and pip for the application
# - nodejs-current and npm for npx-based servers
# - git for dependency installation if needed from git repos
# - fuse-overlayfs for a more performant Docker storage driver on Fly.io
# - tini as an init process to properly manage subprocesses
RUN apk add --no-cache python3 py3-pip git fuse-overlayfs tini nodejs-current npm

# Set up the working directory
WORKDIR /app

# Add the src directory to PYTHONPATH so Python can find the application module.
ENV PYTHONPATH="/app/src"

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies from requirements.txt
# The --break-system-packages flag is required to override the
# "externally-managed-environment" error (PEP 668).
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy the rest of the application files
COPY . .

# Copy the entrypoint script and make it executable
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create data directory and set permissions
RUN mkdir -p /data && chmod 755 /data

# Expose the port the web UI will run on
EXPOSE 8000

# Set the entrypoint to our custom script
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# The command to run the application using the new dual transport architecture
# Defaults to HTTP mode which is appropriate for production deployment
# Prepended with 'tini -s --' to register as subreaper and ensure proper process reaping
# as recommended for Docker-in-Docker setups.
CMD ["tini", "-s", "--", "python", "-m", "mcp_router"] 