FROM python:3.11-slim

WORKDIR /Mythic/

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "mythic/c2_functions/Notion.py"]
