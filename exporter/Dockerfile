FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scpi_exporter.py .

EXPOSE 9101
CMD ["python", "-u", "scpi_exporter.py"]
