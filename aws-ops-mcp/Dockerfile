FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ami_checker.py server.py .
ENV BRIDGE_HOST=0.0.0.0 BRIDGE_PORT=9002
EXPOSE 9002
CMD ["python", "server.py"]
