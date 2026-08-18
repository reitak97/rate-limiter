# slim (not alpine) so pip-installed wheels with C extensions don't need to
# be compiled from source against musl libc.
FROM python:3.12-slim

WORKDIR /app

# Copy only the dependency manifest first and install before copying the rest
# of the source. Docker caches each layer by its inputs, so editing app code
# later won't invalidate this layer and force a full reinstall.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the actual application code (this layer *does* invalidate on
# every source change, but by now deps are already cached above it).
COPY . .

# Documents the port to humans/tools (docker-compose, ECS); does not actually
# publish it — the `ports:`/portMappings entries elsewhere do that.
EXPOSE 8000

# Runs the same app object (app.main:app) that `uvicorn app.main:app --reload`
# would locally, just without --reload and bound to all interfaces so it's
# reachable from outside the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]