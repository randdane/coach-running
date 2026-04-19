FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python -e .
ENV PATH=/opt/venv/bin:$PATH
ENV PYTHONPATH=/app/src

COPY src ./src
COPY prompts ./prompts

EXPOSE 8000
CMD ["python", "-m", "coach.main"]
